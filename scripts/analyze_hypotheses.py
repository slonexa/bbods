import math
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Минимальный порог выборки для статистически значимого вывода (раздел 12 bbods-master-status1.md)
MIN_SAMPLE_SIZE = 100
BREAKEVEN_180X = 1.0 / 1.80  # 0.555555... (55.56%)

# Параметры лаборатории лага (портировано из lag.ts)
SPOT_MOVE_THRESHOLD_PCT = 0.02
LOOKBACK_TICKS = 15


def binomial_p_value(k: int, n: int, p0: float = BREAKEVEN_180X) -> float:
    """
    Односторонний точный биномиальный тест: P(X >= k | X ~ Binomial(n, p0)).
    Проверяет нулевую гипотезу H0: истинный Win Rate <= p0 (55.56%).
    """
    if n <= 0 or k <= 0:
        return 1.0
    if k > n:
        return 0.0
    # Для больших n > 1000 используем нормальное приближение с поправкой на непрерывность
    if n > 1000:
        mean = n * p0
        std = math.sqrt(n * p0 * (1.0 - p0))
        if std == 0:
            return 1.0
        z = (k - 0.5 - mean) / std
        return 0.5 * math.erfc(z / math.sqrt(2.0))

    prob_ge_k = 0.0
    for i in range(k, n + 1):
        prob_ge_k += math.comb(n, i) * (p0 ** i) * ((1.0 - p0) ** (n - i))
    return min(1.0, max(0.0, prob_ge_k))


def wilson_ci_95(k: int, n: int) -> Tuple[float, float]:
    """
    95% доверительный интервал Уилсона (Wilson score interval) для доли побед (в процентах 0..100).
    Надежен как на малых, так и на больших выборках.
    """
    if n <= 0:
        return (0.0, 0.0)
    z = 1.959963984540054  # 95% двусторонний (97.5% квантиль)
    phat = k / n
    denom = 1.0 + (z * z) / n
    center = (phat + (z * z) / (2.0 * n)) / denom
    half_width = (z * math.sqrt((phat * (1.0 - phat) + (z * z) / (4.0 * n)) / n)) / denom
    low = max(0.0, (center - half_width) * 100.0)
    high = min(100.0, (center + half_width) * 100.0)
    return (low, high)


def format_verdict(wins: int, n: int, p0: float = BREAKEVEN_180X, min_n: int = MIN_SAMPLE_SIZE) -> str:
    """
    Формирует строгий статистический вердикт согласно разделу 12 bbods-master-status1.md:
    не объявляет эдж при N < min_n или p-value >= 0.05.
    """
    if n == 0:
        return "⚪ НЕТ ДАННЫХ (N = 0)"
    wr = (wins / n) * 100.0
    p0_pct = p0 * 100.0
    p_val = binomial_p_value(wins, n, p0)
    ci_low, ci_high = wilson_ci_95(wins, n)

    stats_str = f"WR: {wr:.2f}% ({wins}/{n}) | 95% CI: [{ci_low:.1f}%, {ci_high:.1f}%] | p-val: {p_val:.4f}"
    if n < min_n:
        return (
            f"⏳ МАЛО ДАННЫХ (N={n} < {min_n}) [{stats_str}] — "
            f"вывод заблокирован во избежание ложного эджа на шуме. Копим до {min_n}+!"
        )
    if wr <= p0_pct:
        return f"🔴 ЭДЖА НЕТ [{stats_str}] — ниже точки безубыточности {p0_pct:.2f}%."
    if p_val >= 0.05:
        return (
            f"⚠️ НЕ ЗНАЧИМО / ВОЗМОЖЕН ШУМ [{stats_str}] — "
            f"WR выше {p0_pct:.2f}%, но p-value >= 0.05 (нет 95% статистической достоверности)."
        )
    return f"🟢 СТАТИСТИЧЕСКИ ЗНАЧИМЫЙ ЭДЖ ПОДТВЕРЖДЕН [{stats_str}] (N >= {min_n}, p < 0.05)"


def compute_lag_study(ticks: List[Dict], prob_field: str, platform_label: str) -> Dict:
    """
    Прямой порт методологии из lag.ts (раздел 4.4 и раздел 10/12 bbods-master-status1.md).
    Ищет конкретный тик движения цены (moveIndex, |Δspot| >= 0.02%) и замеряет точное время (в мс)
    до первого последующего тика (changeIndex в пределах LOOKBACK_TICKS), где котировка/вероятность
    реально сдвинулась.
    """
    # Группируем хронологически по (symbol, window_id)
    groups: Dict[Tuple[str, int], List[Dict]] = {}
    for t in ticks:
        key = (t["symbol"], t["window_id"])
        groups.setdefault(key, []).append(t)

    lags_ms: List[float] = []
    spot_moves_pct: List[float] = []

    for (_, _), series in groups.items():
        if len(series) < 3:
            continue
        # Сортируем по времени возрастания
        series.sort(key=lambda r: r["ts_ms"])

        last_sampled_change_idx = -1
        for i in range(1, len(series)):
            before_price = series[i - 1]["index_price"] or series[i - 1]["spot_price"] or 0.0
            after_price = series[i]["index_price"] or series[i]["spot_price"] or 0.0
            if before_price <= 0 or after_price <= 0:
                continue

            move_pct = ((after_price - before_price) / before_price) * 100.0
            if abs(move_pct) < SPOT_MOVE_THRESHOLD_PCT:
                continue

            move_idx = i
            # Ищем первый индекс начиная с тика импульса (move_idx .. move_idx + LOOKBACK_TICKS),
            # где котировка изменилась: если изменилась в тот же тик (j == move_idx), лаг = 0 мс.
            change_idx = -1
            max_j = min(len(series), move_idx + 1 + LOOKBACK_TICKS)
            for j in range(move_idx, max_j):
                p_prev = series[j - 1].get(prob_field) or 0.0
                p_curr = series[j].get(prob_field) or 0.0
                if p_prev > 0 and p_curr > 0 and abs(p_curr - p_prev) >= 1e-4:
                    change_idx = j
                    break

            if change_idx >= move_idx and change_idx != last_sampled_change_idx:
                lag_val = max(0.0, series[change_idx]["ts_ms"] - series[move_idx]["ts_ms"])
                # Отсекаем разрывы сессий > 30 сек
                if 0 <= lag_val <= 30000:
                    lags_ms.append(lag_val)
                    spot_moves_pct.append(move_pct)
                    last_sampled_change_idx = change_idx

    if not lags_ms:
        return {
            "platform": platform_label,
            "samples": 0,
            "medianLagMs": None,
            "p90LagMs": None,
            "shareOver1s": 0.0,
            "shareOver3s": 0.0,
            "conclusion": "Котировка фиксирована (не менялась после импульсов) или недостаточно тиков.",
        }

    sorted_lags = sorted(lags_ms)
    n = len(sorted_lags)
    median_ms = sorted_lags[n // 2]
    p90_ms = sorted_lags[min(n - 1, int(n * 0.9))]
    over_1s = round((sum(1 for v in sorted_lags if v > 1000.0) / n) * 100.0, 1)
    over_3s = round((sum(1 for v in sorted_lags if v > 3000.0) / n) * 100.0, 1)

    if p90_ms < 700:
        conclusion = "лаг суб-секундный — паттерн «ловли лага» не работает, инфраструктуру строить рано"
    elif p90_ms < 3000:
        conclusion = "лаг 1–3s — пограничная зона (сопоставимо с шагом опроса 1с): собираем ещё данных"
    else:
        conclusion = "лаг 3s+ — есть устойчивая задержка пересчета котировки после импульса спота!"

    conclusion += (
        " (ВАЖНО: шаг логгера = 1000 мс. Лаг ~1000 мс означает отклик на следующем же тике, "
        "а лаг 3000+ мс — реальное запаздывание котировки на несколько секунд)."
    )

    return {
        "platform": platform_label,
        "samples": n,
        "medianLagMs": round(median_ms),
        "p90LagMs": round(p90_ms),
        "shareOver1s": over_1s,
        "shareOver3s": over_3s,
        "conclusion": conclusion,
    }


def _parse_iso_ms(ts_str: str) -> float:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp() * 1000.0
    except Exception:
        return 0.0


def analyze_ticks_database(db_path: str = "ticks.db"):
    if not os.path.exists(db_path):
        print(f"❌ База данных тиков '{db_path}' не найдена!")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM sec_ticks")
    total_ticks = cursor.fetchone()[0]
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM sec_ticks")
    min_ts, max_ts = cursor.fetchone()

    print("=" * 80)
    print("🔬 АНАЛИЗ СТАТИСТИЧЕСКИХ ГИПОТЕЗ BYBIT ODDS & POLYMARKET (HFT 1s TICKS)")
    print("=" * 80)
    print(f"📊 Всего тиков в базе: {total_ticks:,}")
    print(f"⏱️ Период наблюдений:  {min_ts} ➔ {max_ts}")
    print(f"🛡️ Защита от шума:     Биномиальный тест (p < 0.05) + 95% CI Уилсона + порог N >= {MIN_SAMPLE_SIZE}\n")

    if total_ticks < 50:
        print("⚠️ Слишком мало данных для анализа (нужно минимум 200+ тиков).")
        conn.close()
        return

    # ──────────────────────────────────────────────────────────────────────────
    # ГИПОТЕЗА 4.5: МОМЕНТУМ ПЕРВЫХ МИНУТ 5-МИНУТНОГО ОКНА + OUT-OF-SAMPLE СПЛИТ
    # ──────────────────────────────────────────────────────────────────────────
    print("=" * 80)
    print("📈 ГИПОТЕЗА 4.5: Моментум первых 60–90 сек (с проверкой Out-of-Sample 50/50)")
    print("=" * 80)
    print("Правило: если за первые 60–90 сек индекс/спот сдвинулся >= 0.03% от открытия окна,")
    print("сохранится ли знак движения до экспирации 5-минутки? (Безубыточность 1.80x = 55.56%)\n")

    cursor.execute('''
        SELECT symbol, window_id,
               MIN(seconds_to_expiry) as min_exp,
               MAX(seconds_to_expiry) as max_exp,
               COUNT(*) as tick_count
        FROM sec_ticks
        WHERE window_type = '5MIN'
        GROUP BY symbol, window_id
        HAVING max_exp >= 220 AND min_exp <= 20
        ORDER BY window_id ASC
    ''')
    windows = cursor.fetchall()
    print(f"Найдено завершенных 5-минутных окон с полной историей: {len(windows)}")

    threshold_pct = 0.03
    idx_outcomes: List[int] = []  # 1 = win, 0 = loss (в хронологическом порядке)
    spot_outcomes: List[int] = []

    for win in windows:
        sym = win["symbol"]
        wid = win["window_id"]

        cursor.execute('''
            SELECT seconds_to_expiry, index_price, spot_price, futures_price,
                   window_open_index, window_open_spot
            FROM sec_ticks
            WHERE symbol = ? AND window_id = ?
            ORDER BY seconds_to_expiry DESC
        ''', (sym, wid))
        ticks = [dict(t) for t in cursor.fetchall()]
        if len(ticks) < 10:
            continue

        start_tick = ticks[0]
        end_tick = ticks[-1]
        early_ticks = [t for t in ticks if 180 <= t["seconds_to_expiry"] <= 240]
        if not early_ticks:
            continue
        early_tick = early_ticks[0]

        open_idx = start_tick["window_open_index"] or start_tick["index_price"]
        early_idx = early_tick["index_price"]
        final_idx = end_tick["index_price"]

        if open_idx and open_idx > 0:
            delta_early = (early_idx - open_idx) / open_idx * 100.0
            delta_final = (final_idx - open_idx) / open_idx * 100.0
            if abs(delta_early) >= threshold_pct:
                win_flag = 1 if ((delta_early > 0 and delta_final > 0) or (delta_early < 0 and delta_final < 0)) else 0
                idx_outcomes.append(win_flag)

        open_spot = start_tick["window_open_spot"] or start_tick["spot_price"]
        early_spot = early_tick["spot_price"]
        final_spot = end_tick["spot_price"]

        if open_spot and open_spot > 0:
            delta_early_s = (early_spot - open_spot) / open_spot * 100.0
            delta_final_s = (final_spot - open_spot) / open_spot * 100.0
            if abs(delta_early_s) >= threshold_pct:
                win_flag_s = 1 if ((delta_early_s > 0 and delta_final_s > 0) or (delta_early_s < 0 and delta_final_s < 0)) else 0
                spot_outcomes.append(win_flag_s)

    n_idx = len(idx_outcomes)
    w_idx = sum(idx_outcomes)
    print(f"\n📊 1) По Bybit Index Price (ecIndexPrice) — ВСЯ ВЫБОРКА:")
    print(f"   {format_verdict(w_idx, n_idx, BREAKEVEN_180X, MIN_SAMPLE_SIZE)}")

    if n_idx >= 10:
        mid = n_idx // 2
        in_sample = idx_outcomes[:mid]
        out_sample = idx_outcomes[mid:]
        print(f"   ├─ In-Sample (первые 50% окон, N={len(in_sample)}):  {format_verdict(sum(in_sample), len(in_sample), BREAKEVEN_180X, min_n=50)}")
        print(f"   └─ Out-of-Sample (вторые 50%, N={len(out_sample)}): {format_verdict(sum(out_sample), len(out_sample), BREAKEVEN_180X, min_n=50)}")

    n_spot = len(spot_outcomes)
    w_spot = sum(spot_outcomes)
    print(f"\n📊 2) По Bybit Spot Price — ВСЯ ВЫБОРКА:")
    print(f"   {format_verdict(w_spot, n_spot, BREAKEVEN_180X, MIN_SAMPLE_SIZE)}")

    # ──────────────────────────────────────────────────────────────────────────
    # ГИПОТЕЗА 4.4: ЧЕСТНЫЙ ЗАМЕР ЛАГА ПО МЕТОДИКЕ LAG.TS (МС МЕЖДУ СПОТОМ И КОЭФФОМ)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("⏱️ ГИПОТЕЗА 4.4: Лаборатория лага (методика lag.ts — отклик котировки на импульс >= 0.02%)")
    print("=" * 80)

    cursor.execute('''
        SELECT symbol, window_id, seconds_to_expiry, prob_up, poly_up_ask,
               index_price, spot_price, timestamp
        FROM sec_ticks
        WHERE window_type = '5MIN'
        ORDER BY symbol, window_id, timestamp ASC
    ''')
    raw_ticks = []
    for r in cursor.fetchall():
        d = dict(r)
        d["ts_ms"] = _parse_iso_ms(d["timestamp"])
        raw_ticks.append(d)

    poly_lag = compute_lag_study(raw_ticks, "poly_up_ask", "Polymarket 5M CLOB (poly_up_ask)")
    bybit_lag = compute_lag_study(raw_ticks, "prob_up", "Bybit 5M Odds (prob_up)")

    for res in (poly_lag, bybit_lag):
        print(f"\n🔹 Площадка: {res['platform']}")
        print(f"   Замеров импульсов (|ΔSpot| >= {SPOT_MOVE_THRESHOLD_PCT}%): {res['samples']}")
        if res["samples"] > 0:
            print(f"   Медиана лага: {res['medianLagMs']} мс | p90 лага: {res['p90LagMs']} мс")
            print(f"   Доля лагов > 1с: {res['shareOver1s']}% | Доля лагов > 3с: {res['shareOver3s']}%")
        print(f"   Вывод: {res['conclusion']}")

    # ──────────────────────────────────────────────────────────────────────────
    # РАЗДЕЛ 7 и 8: POLYMARKET 5/15M CLOB & ФЕНОМЕН 47-51 СЕКУНДЫ
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("🌐 РАЗДЕЛ 7 & 8: Polymarket 5/15m CLOB и окно 47–51 секунды")
    print("=" * 80)
    try:
        cursor.execute("SELECT COUNT(*) FROM sec_ticks WHERE poly_up_ask > 0 OR poly_down_ask > 0")
        poly_ticks = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*), MIN(cross_hedge_cost) FROM sec_ticks WHERE cross_hedge_cost > 0 AND cross_hedge_cost < 1.0")
        arb_ticks, min_cost = cursor.fetchone()
        cursor.execute('''
            SELECT COUNT(*), AVG(poly_up_ask), AVG(poly_down_ask)
            FROM sec_ticks
            WHERE window_type = '5MIN' AND sec_from_start BETWEEN 45 AND 55 AND (poly_up_ask > 0 OR poly_down_ask > 0)
        ''')
        w48_cnt, avg_up_48, avg_dn_48 = cursor.fetchone()
        print(f"Тиков с живым стаканом Polymarket 5/15m CLOB: {poly_ticks:,}")
        print(f"Секунд с кросс-вилкой (Bybit 1.8x + Poly CLOB < 1.00): {arb_ticks:,} (лучший Cost: {min_cost or 0:.4f})")
        print(f"Замеров в «горячем окне» 45–55 сек: {w48_cnt:,} (ср. Ask Up: {avg_up_48 or 0:.3f}, Down: {avg_dn_48 or 0:.3f})")
    except Exception as e:
        print(f"Статистика Polymarket CLOB только начала собираться: {e}")

    conn.close()

    # ──────────────────────────────────────────────────────────────────────────
    # РЕЗУЛЬТАТЫ АВТО-ДЕМО СДЕЛОК (С БИНОМИАЛЬНЫМ ТЕСТОМ ДЛЯ КАЖДОЙ ГИПОТЕЗЫ)
    # ──────────────────────────────────────────────────────────────────────────
    spreads_db = os.path.join(os.path.dirname(db_path), "spreads.db")
    if os.path.exists(spreads_db):
        print("\n" + "=" * 80)
        print("🤖 РЕЗУЛЬТАТЫ ИЗОЛИРОВАННЫХ ТЕСТ-ПОРТФЕЛЕЙ (AUTO PAPER TRADER + СТАТ. ТЕСТ)")
        print("=" * 80)
        sconn = sqlite3.connect(spreads_db)
        rows = sconn.execute('''
            SELECT deal_type,
                   SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN status = 'LOST' THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) as open_cnt,
                   ROUND(SUM(CASE WHEN status IN ('WON', 'LOST') THEN net_profit ELSE 0 END), 2) as closed_pnl,
                   ROUND(AVG(CASE WHEN status IN ('WON', 'LOST') THEN roi_pct ELSE NULL END), 2) as avg_roi
            FROM paper_trades
            GROUP BY deal_type
            ORDER BY deal_type
        ''').fetchall()
        for dtype, wins, losses, open_cnt, closed_pnl, avg_roi in rows:
            wins = wins or 0
            losses = losses or 0
            closed_n = wins + losses
            p0_target = 0.50 if dtype in ("sync_start_arb", "two_step_hedge", "cross_arb") else BREAKEVEN_180X
            verdict = format_verdict(wins, closed_n, p0=p0_target, min_n=MIN_SAMPLE_SIZE)
            print(
                f"   • {dtype:<15} | Закрыто: {closed_n:<3} (W:{wins}/L:{losses}, Активно:{open_cnt}) | "
                f"P&L: ${closed_pnl or 0:+8.2f} | Ср.ROI: {avg_roi or 0:+.2f}%"
            )
            print(f"     └─ Стат-тест: {verdict}")
        sconn.close()

    print("\n" + "=" * 80)
    print("✅ Анализ завершен.")
    print("=" * 80)


def get_hypotheses_summary(db_path: str = "ticks.db") -> Dict:
    """
    Возвращает JSON-сводку по статистическим гипотезам (биномиальный тест, 95% CI Уилсона,
    Out-of-Sample сплит и замер лага по методике lag.ts) для отображения в UI Полигона 5/15m.
    """
    if not os.path.exists(db_path):
        return {"status": "empty", "total_ticks": 0}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM sec_ticks")
    total_ticks = cursor.fetchone()[0]

    # 1. Momentum 4.5
    cursor.execute('''
        SELECT symbol, window_id
        FROM sec_ticks
        WHERE window_type = '5MIN'
        GROUP BY symbol, window_id
        HAVING MAX(seconds_to_expiry) >= 220 AND MIN(seconds_to_expiry) <= 20
        ORDER BY window_id ASC
    ''')
    windows = cursor.fetchall()
    idx_outcomes: List[int] = []
    for win in windows:
        cursor.execute('''
            SELECT seconds_to_expiry, index_price, window_open_index
            FROM sec_ticks
            WHERE symbol = ? AND window_id = ?
            ORDER BY seconds_to_expiry DESC
        ''', (win["symbol"], win["window_id"]))
        ticks = [dict(t) for t in cursor.fetchall()]
        if len(ticks) < 10:
            continue
        early_ticks = [t for t in ticks if 180 <= t["seconds_to_expiry"] <= 240]
        if not early_ticks:
            continue
        open_idx = ticks[0]["window_open_index"] or ticks[0]["index_price"]
        early_idx = early_ticks[0]["index_price"]
        final_idx = ticks[-1]["index_price"]
        if open_idx and open_idx > 0:
            d_early = (early_idx - open_idx) / open_idx * 100.0
            d_final = (final_idx - open_idx) / open_idx * 100.0
            if abs(d_early) >= 0.03:
                idx_outcomes.append(1 if ((d_early > 0 and d_final > 0) or (d_early < 0 and d_final < 0)) else 0)

    n_idx = len(idx_outcomes)
    w_idx = sum(idx_outcomes)
    ci_low, ci_high = wilson_ci_95(w_idx, n_idx)
    p_val = binomial_p_value(w_idx, n_idx, BREAKEVEN_180X)

    # 2. Lag 4.4 (last 15,000 ticks for fast response)
    cursor.execute('''
        SELECT symbol, window_id, seconds_to_expiry, prob_up, poly_up_ask,
               index_price, spot_price, timestamp
        FROM sec_ticks
        WHERE window_type = '5MIN'
        ORDER BY id DESC
        LIMIT 15000
    ''')
    raw_ticks = []
    for r in reversed(cursor.fetchall()):
        d = dict(r)
        d["ts_ms"] = _parse_iso_ms(d["timestamp"])
        raw_ticks.append(d)

    poly_lag = compute_lag_study(raw_ticks, "poly_up_ask", "Polymarket 5M CLOB")
    conn.close()

    return {
        "status": "ok",
        "total_ticks": total_ticks,
        "completed_windows": len(windows),
        "min_sample_size": MIN_SAMPLE_SIZE,
        "momentum_4_5": {
            "n": n_idx,
            "wins": w_idx,
            "win_rate_pct": round((w_idx / n_idx) * 100.0, 2) if n_idx > 0 else 0.0,
            "ci_95": [round(ci_low, 1), round(ci_high, 1)],
            "p_value": round(p_val, 4),
            "is_significant": bool(n_idx >= MIN_SAMPLE_SIZE and p_val < 0.05 and (w_idx / n_idx) > BREAKEVEN_180X),
            "verdict": format_verdict(w_idx, n_idx, BREAKEVEN_180X, MIN_SAMPLE_SIZE),
        },
        "lag_4_4": poly_lag,
    }


if __name__ == "__main__":
    db_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ticks.db")
    analyze_ticks_database(db_file)

