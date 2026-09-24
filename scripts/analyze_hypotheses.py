import os
import sqlite3
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


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

    print("=" * 70)
    print("🔬 АНАЛИЗ СТАТИСТИЧЕСКИХ ГИПОТЕЗ BYBIT ODDS (HFT 1s TICKS)")
    print("=" * 70)
    print(f"📊 Всего тиков в базе: {total_ticks:,}")
    print(f"⏱️ Период наблюдений:  {min_ts} ➔ {max_ts}\n")

    if total_ticks < 50:
        print("⚠️ Слишком мало данных для анализа (нужно минимум 200+ тиков).")
        print("Оставьте логгер работать на пару часов / дней, затем перезапустите анализ.\n")
        conn.close()
        return

    # ──────────────────────────────────────────────────────────────────────────
    # ГИПОТЕЗА 4.5: МОМЕНТУМ ПЕРВЫХ МИНУТ 5-МИНУТНОГО ОКНА
    # ──────────────────────────────────────────────────────────────────────────
    print("=" * 70)
    print("📈 ГИПОТЕЗА 4.5: Проверка моментума (сохранение тренда первых 60-90 сек)")
    print("=" * 70)
    print("Правило: если за первые 60-90 сек спот/индекс отклонился от старта окна,")
    print("закроется ли всё 5-минутное окно в том же направлении?")
    print("Порог безубыточности при выплате 1.8х: Win Rate > 55.55%\n")

    # Group ticks by (symbol, window_id)
    cursor.execute('''
        SELECT symbol, window_id,
               MIN(seconds_to_expiry) as min_exp,
               MAX(seconds_to_expiry) as max_exp,
               COUNT(*) as tick_count
        FROM sec_ticks
        WHERE window_type = '5MIN'
        GROUP BY symbol, window_id
        HAVING max_exp >= 220 AND min_exp <= 20
    ''')
    windows = cursor.fetchall()
    print(f"Найдено завершенных 5-минутных окон с полной историей: {len(windows)}")

    momentum_wins = {"index": 0, "spot": 0, "futures": 0}
    momentum_losses = {"index": 0, "spot": 0, "futures": 0}
    flat_skips = {"index": 0, "spot": 0, "futures": 0}

    threshold_pct = 0.03  # минимальный импульс в первые минуты (0.03%)

    for win in windows:
        sym = win["symbol"]
        wid = win["window_id"]

        # Fetch ticks at second ~180-240 (early phase) and <= 5 (final settlement phase)
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

        # Find tick around 60-90s after start (seconds_to_expiry between 200 and 240)
        early_ticks = [t for t in ticks if 180 <= t["seconds_to_expiry"] <= 240]
        if not early_ticks:
            continue
        early_tick = early_ticks[0]

        # Check Index Price
        open_idx = start_tick["window_open_index"] or start_tick["index_price"]
        early_idx = early_tick["index_price"]
        final_idx = end_tick["index_price"]

        if open_idx > 0:
            delta_early = (early_idx - open_idx) / open_idx * 100.0
            delta_final = (final_idx - open_idx) / open_idx * 100.0

            if abs(delta_early) >= threshold_pct:
                if (delta_early > 0 and delta_final > 0) or (delta_early < 0 and delta_final < 0):
                    momentum_wins["index"] += 1
                else:
                    momentum_losses["index"] += 1
            else:
                flat_skips["index"] += 1

        # Check Spot Price
        open_spot = start_tick["window_open_spot"] or start_tick["spot_price"]
        early_spot = early_tick["spot_price"]
        final_spot = end_tick["spot_price"]

        if open_spot > 0:
            delta_early_s = (early_spot - open_spot) / open_spot * 100.0
            delta_final_s = (final_spot - open_spot) / open_spot * 100.0

            if abs(delta_early_s) >= threshold_pct:
                if (delta_early_s > 0 and delta_final_s > 0) or (delta_early_s < 0 and delta_final_s < 0):
                    momentum_wins["spot"] += 1
                else:
                    momentum_losses["spot"] += 1
            else:
                flat_skips["spot"] += 1

    total_idx = momentum_wins["index"] + momentum_losses["index"]
    if total_idx > 0:
        wr_idx = (momentum_wins["index"] / total_idx) * 100.0
        print(f"📊 По Bybit Index Price (ecIndexPrice):")
        print(f"   Сделок с импульсом (>±{threshold_pct}%): {total_idx}")
        print(f"   Побед (тренд продолжился): {momentum_wins['index']} | Разворотов: {momentum_losses['index']}")
        print(f"   🎯 Win Rate: {wr_idx:.2f}% (Точка безубыточности: 55.55%)")
        if wr_idx > 55.55:
            print("   🟢 ЭДЖ ОБНАРУЖЕН: Моментум имеет положительное матожидание!")
        else:
            print("   🔴 Эджа нет или рынок находился в флэте/боковике.")
    else:
        print("   Пока недостаточно завершенных импульсных окон для итогового Win Rate.")

    total_spot = momentum_wins["spot"] + momentum_losses["spot"]
    if total_spot > 0:
        wr_spot = (momentum_wins["spot"] / total_spot) * 100.0
        print(f"\n📊 По Bybit Spot Price:")
        print(f"   Сделок: {total_spot} | Win Rate: {wr_spot:.2f}%")

    # ──────────────────────────────────────────────────────────────────────────
    # ГИПОТЕЗА 4.4: ЛАГ ОБНОВЛЕНИЯ КОЭФФИЦИЕНТА ПЕРЕД ЭКСПИРАЦИЕЙ (ПОСЛЕДНИЕ 60 СЕК)
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("⏱️ ГИПОТЕЗА 4.4: Проверка задержки пересчета Odds в последние 60 секунд")
    print("=" * 70)
    print("Изучение поведения odds_up/odds_down при резких тиках цены в последние 60с.\n")

    cursor.execute('''
        SELECT symbol, seconds_to_expiry, odds_up, odds_down, prob_up, prob_down,
               index_price, spot_price, futures_price, timestamp
        FROM sec_ticks
        WHERE window_type = '5MIN' AND seconds_to_expiry <= 60
        ORDER BY symbol, timestamp ASC
    ''')
    late_ticks = cursor.fetchall()

    print(f"Тиков в последние 60 секунд окон: {len(late_ticks)}")
    if len(late_ticks) >= 10:
        odds_changes = 0
        price_moves = 0
        for i in range(1, len(late_ticks)):
            prev = late_ticks[i - 1]
            curr = late_ticks[i]
            if prev["symbol"] != curr["symbol"]:
                continue
            if abs(curr["prob_up"] - prev["prob_up"]) > 0.001 or curr["odds_up"] != prev["odds_up"]:
                odds_changes += 1
            if abs(curr["index_price"] - prev["index_price"]) >= 5.0:
                price_moves += 1

        print(f"Резких движений цены спота/индекса: {price_moves}")
        print(f"Моментов обновления вероятностей Odds в последние 60с: {odds_changes}")
        if odds_changes == 0 and price_moves > 0:
            print("⚡ На Bybit 5M/15M выплата фиксирована (1.80x), а на Polymarket CLOB коэффы динамические!")
        else:
            print("Котировки обновляются параллельно с тиками цен.")

    # ──────────────────────────────────────────────────────────────────────────
    # РАЗДЕЛ 7 и 8: POLYMARKET 5/15M CLOB & ФЕНОМЕН 47-51 СЕКУНДЫ
    # ──────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("🌐 РАЗДЕЛ 7 & 8: Polymarket 5/15m CLOB и окно 47–51 секунды")
    print("=" * 70)
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
    # РЕЗУЛЬТАТЫ АВТО-ДЕМО СДЕЛОК (AUTO PAPER TRADER)
    # ──────────────────────────────────────────────────────────────────────────
    spreads_db = os.path.join(os.path.dirname(db_path), "spreads.db")
    if os.path.exists(spreads_db):
        print("\n" + "=" * 70)
        print("🤖 РЕЗУЛЬТАТЫ ТЕСТОВЫХ АВТО-СДЕЛОК (AUTO PAPER TRADER)")
        print("=" * 70)
        sconn = sqlite3.connect(spreads_db)
        rows = sconn.execute('''
            SELECT deal_type, status, COUNT(*), ROUND(SUM(net_profit), 2), ROUND(AVG(roi_pct), 2)
            FROM paper_trades
            GROUP BY deal_type, status
            ORDER BY deal_type, status
        ''').fetchall()
        for dtype, st, cnt, pnl, roi in rows:
            print(f"   • {dtype:<15} | {st:<6} | Сделок: {cnt:<4} | P&L: ${pnl or 0:+8.2f} | Ср. ROI: {roi or 0:+.2f}%")
        sconn.close()

    print("\n" + "=" * 70)
    print("✅ Анализ завершен.")
    print("=" * 70)


if __name__ == "__main__":
    db_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ticks.db")
    analyze_ticks_database(db_file)

