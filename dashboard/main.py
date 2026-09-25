from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from engine.db import Database
from engine.alerts import AlertManager, LOG_PATH
import os

app = FastAPI()
db = Database()
alert_mgr = AlertManager()

# Setup static and media files directories
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

media_dir = os.path.join(os.path.dirname(__file__), "..", "media")
if os.path.exists(media_dir):
    app.mount("/media", StaticFiles(directory=media_dir), name="media")

@app.get("/")
def read_root():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/api/spreads")
def get_spreads():
    # Returns latest clean deduplicated live market strikes (1 row per strike, excluding 5m/15m test contracts)
    spreads = db.get_latest_clean_spreads(limit=50)
    return {"data": spreads}

@app.get("/api/updown_spreads")
def get_updown_spreads():
    # Returns latest 5MIN and 15MIN cross-platform Up/Down spreads for the dedicated 5/15m test tab
    spreads = db.get_updown_spreads(limit=30)
    return {"data": spreads}

@app.get("/api/raw_spreads")
def get_raw_spreads():
    # Returns raw chronological tick stream (including all ABOVE/BELOW ticks) for debugging
    raw = db.get_recent_spreads(limit=100)
    return {"data": raw}

@app.get("/api/top_spreads")
def get_top_spreads():
    # Returns the highest spreads recorded
    top = db.get_top_spreads(limit=15)
    return {"data": top}

@app.get("/api/archived_arbs")
def get_archived_arbs():
    # Returns permanent historical surebets and high spreads
    arbs = db.get_archived_arbs(limit=50)
    return {"data": arbs}

@app.get("/api/spread_history")
def get_spread_history(event_key: str = "", title: str = "", hours: int = 12):
    # Returns chronological spread history points and stats for charts
    return db.get_spread_history(event_key=event_key, title=title, hours=hours)

@app.get("/api/health")
def get_health():
    # Health status and cycle metrics
    import json
    health_file = os.path.join(os.path.dirname(__file__), "..", "engine", ".health.json")
    health_data = {}
    if os.path.exists(health_file):
        try:
            with open(health_file, "r", encoding="utf-8") as f:
                health_data = json.load(f)
        except Exception:
            pass
    db_stats = db.get_db_stats()
    return {
        "status": "ok",
        "scanner": health_data,
        "database": db_stats
    }

@app.get("/api/stats")
def get_stats():
    return db.get_db_stats()

@app.post("/api/vacuum")
def trigger_vacuum():
    # Manually trigger DB vacuum
    res = db.vacuum_db()
    return res

class AlertConfigUpdate(BaseModel):
    enabled: bool | None = None
    telegram_enabled: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    min_spread_alert_pct: float | None = None
    min_margin_alert_pct: float | None = None
    cooldown_minutes: int | None = None
    notify_on_surebet: bool | None = None
    notify_on_high_spread: bool | None = None

class TelegramTestRequest(BaseModel):
    token: str
    chat_id: str

@app.get("/api/alerts/config")
def get_alert_config():
    cfg = alert_mgr.load_config()
    safe_cfg = dict(cfg)
    token = safe_cfg.get("telegram_bot_token", "")
    if token and len(token) > 10:
        safe_cfg["telegram_bot_token_masked"] = token[:4] + "..." + token[-4:]
    else:
        safe_cfg["telegram_bot_token_masked"] = ""
    return safe_cfg

@app.post("/api/alerts/config")
def update_alert_config(update: AlertConfigUpdate):
    data = {k: v for k, v in update.model_dump().items() if v is not None}
    success = alert_mgr.save_config(data)
    return {"status": "ok" if success else "error", "config": alert_mgr.config}

@app.post("/api/alerts/test_telegram")
def test_telegram(req: TelegramTestRequest):
    return alert_mgr.send_test_telegram(req.token, req.chat_id)

@app.get("/api/alerts/history")
def get_alerts_history(limit: int = 30):
    alerts = []
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in reversed(lines[-limit:]):
                    alerts.append(line.strip())
        except Exception:
            pass
    return {"alerts": alerts, "recent": alert_mgr.get_recent_alerts(limit)}

# ─── High-Frequency Tick Stats & Auto Paper Trading APIs ───

@app.get("/api/ticks_stats")
def get_ticks_stats():
    """Returns real-time 1s HFT tick collector metrics and Bybit spot/linear/index prices."""
    import json
    health_file = os.path.join(os.path.dirname(__file__), "..", "engine", ".sec_logger_health.json")
    if os.path.exists(health_file):
        try:
            with open(health_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "starting", "total_ticks": 0, "db_size_mb": 0.0, "prices": {}, "windows": []}

_hyp_cache = {"ts": 0.0, "data": None}

@app.get("/api/hypotheses_stats")
def get_hypotheses_stats():
    """Returns live Binomial test, 95% Wilson CI, and lag.ts p90 lag metrics from ticks.db."""
    import time
    now = time.time()
    if _hyp_cache["data"] is not None and (now - _hyp_cache["ts"]) < 20.0:
        return _hyp_cache["data"]
    try:
        from scripts.analyze_hypotheses import get_hypotheses_summary
        ticks_db_path = os.path.join(os.path.dirname(__file__), "..", "ticks.db")
        res = get_hypotheses_summary(ticks_db_path)
        _hyp_cache["ts"] = now
        _hyp_cache["data"] = res
        return res
    except Exception as e:
        return {"status": "error", "error": str(e)}


from engine.auto_paper import AutoPaperTrader
paper_trader = AutoPaperTrader()

class PaperConfigUpdate(BaseModel):
    enabled: bool | None = None
    default_stake: float | None = None
    min_spread_pct: float | None = None
    auto_cross_arbs: bool | None = None
    auto_5min_momentum: bool | None = None
    momentum_min_delta_pct: float | None = None

@app.get("/api/paper/trades")
def get_paper_trades(limit: int = 100, mode: str = "all"):
    """Returns simulated paper trades (mode='main' for daily targets, mode='fast' for 5/15m test polygon)."""
    trades = paper_trader.get_trades(limit=limit, mode=mode)
    return {"data": trades}

@app.get("/api/paper/config")
def get_paper_config():
    return paper_trader.load_config()

@app.post("/api/paper/config")
def update_paper_config(update: PaperConfigUpdate):
    cfg = paper_trader.load_config()
    for k, v in update.model_dump().items():
        if v is not None:
            cfg[k] = v
    paper_trader.save_config(cfg)
    return {"status": "ok", "config": cfg}

@app.post("/api/paper/clear")
def clear_paper_trades(mode: str = "all"):
    paper_trader.clear_trades(mode=mode)
    return {"status": "ok", "message": f"Cleared paper trades (mode={mode})"}


# ─── Section 8: Manual Trade & Attempt Logger (manual_trades_log.csv) ───

import csv
import time
from datetime import datetime, timezone

MANUAL_LOG_CSV = os.path.join(os.path.dirname(__file__), "..", "manual_trades_log.csv")
MANUAL_LOG_HEADERS = [
    "timestamp", "sec_from_start", "platform_a", "platform_b",
    "requested_odds", "filled_odds", "outcome", "pnl",
    "bybit_index_price", "poly_ask", "note"
]

class ManualLogEntry(BaseModel):
    platform_a: str = "bybit_odds"
    platform_b: str = "polymarket"
    requested_odds: float = 1.80
    filled_odds: float = 0.0
    outcome: str = "win"  # win / loss / missed_timing / odds_moved_before_fill
    pnl: float = 0.0
    note: str = ""

@app.get("/api/manual_logs")
def get_manual_logs():
    if not os.path.exists(MANUAL_LOG_CSV):
        return {"data": []}
    rows = []
    try:
        with open(MANUAL_LOG_CSV, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception:
        pass
    return {"data": list(reversed(rows[-50:]))}

@app.post("/api/manual_logs")
def add_manual_log(entry: ManualLogEntry):
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sec_from_start = round(now_ts % 300, 1)

    bybit_idx = 0.0
    poly_ask = 0.0
    health_file = os.path.join(os.path.dirname(__file__), "..", "engine", ".sec_logger_health.json")
    if os.path.exists(health_file):
        try:
            import json
            with open(health_file, "r", encoding="utf-8") as hf:
                h = json.load(hf)
                bybit_idx = h.get("prices", {}).get("BTC", {}).get("index", 0.0)
                for w in h.get("windows", []):
                    if w.get("symbol") == "BTCUSDT-5MIN":
                        sec_from_start = w.get("sec_from_start", sec_from_start)
                        poly_ask = w.get("poly_up_ask") or w.get("poly_down_ask") or 0.0
                        break
        except Exception:
            pass

    file_exists = os.path.exists(MANUAL_LOG_CSV)
    with open(MANUAL_LOG_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANUAL_LOG_HEADERS)
        if not file_exists:
            writer.writeheader()
        row_dict = {
            "timestamp": now_iso,
            "sec_from_start": sec_from_start,
            "platform_a": entry.platform_a,
            "platform_b": entry.platform_b,
            "requested_odds": entry.requested_odds,
            "filled_odds": entry.filled_odds,
            "outcome": entry.outcome,
            "pnl": entry.pnl,
            "bybit_index_price": bybit_idx,
            "poly_ask": poly_ask,
            "note": entry.note
        }
        writer.writerow(row_dict)

    return {"status": "ok", "entry": row_dict}


# ─── Dedicated Scheme 1 (Momentum 60-90s) Pair Cockpit API & Page ───

@app.get("/momentum")
def read_momentum_page():
    """Serves dashboard in standalone Scheme 1 Pair Cockpit mode."""
    return FileResponse(os.path.join(static_dir, "index.html"))

_pair_history_cache = {}

@app.get("/api/pair_detail")
def get_pair_detail(symbol: str = "BTCUSDT-5MIN"):
    """
    Returns 1-second live state, current window 1s tick trajectory, and last 15 completed windows
    for the selected pair (e.g., BTCUSDT-5MIN, ETHUSDT-5MIN, BTCUSDT-15MIN, ETHUSDT-15MIN).
    """
    import json
    import sqlite3

    symbol = (symbol or "BTCUSDT-5MIN").upper().strip()
    is_15m = "15MIN" in symbol
    duration_sec = 900 if is_15m else 300
    now_ts = time.time()
    current_win_id = int(now_ts // duration_sec) * duration_sec

    health_file = os.path.join(os.path.dirname(__file__), "..", "engine", ".sec_logger_health.json")
    ticks_db_path = os.path.join(os.path.dirname(__file__), "..", "ticks.db")

    live_win = None
    all_windows = []
    prices = {}

    if os.path.exists(health_file):
        try:
            with open(health_file, "r", encoding="utf-8") as hf:
                h = json.load(hf)
                prices = h.get("prices", {})
                all_windows = h.get("windows", [])
                for w in all_windows:
                    if w.get("symbol") == symbol:
                        live_win = dict(w)
                        break
        except Exception:
            pass

    # Supplement current window series and rolling 420s series from SQLite if needed
    series = live_win.get("series", []) if live_win else []
    rolling_series = live_win.get("rolling_series", []) if live_win else []
    if (len(series) < 15 or len(rolling_series) < 30) and os.path.exists(ticks_db_path):
        try:
            conn = sqlite3.connect(ticks_db_path)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT window_id, sec_from_start, index_price, spot_price, futures_price,
                       index_delta_pct, poly_up_ask, poly_down_ask, window_open_index
                FROM sec_ticks
                WHERE symbol = ? AND window_id >= ?
                ORDER BY id ASC
                """,
                (symbol, current_win_id - duration_sec)
            )
            db_rows = cur.fetchall()
            conn.close()
            if db_rows:
                merged_win = {}
                merged_roll = {}
                for r in db_rows:
                    w_id = int(r[0] or current_win_id)
                    sec_f = float(r[1] or 0.0)
                    ts_val = round(w_id + sec_f, 1)
                    pt_obj = {
                        "ts": ts_val,
                        "s": round(sec_f, 1),
                        "idx": float(r[2] or 0),
                        "spot": float(r[3] or 0),
                        "fut": float(r[4] or 0),
                        "d": round(float(r[5] or 0), 4),
                        "p_up": float(r[6] or 0),
                        "p_dn": float(r[7] or 0),
                    }
                    merged_roll[int(round(ts_val))] = pt_obj
                    if w_id == current_win_id:
                        merged_win[int(round(sec_f))] = pt_obj

                for pt in series:
                    sec_k = int(round(float(pt.get("s", 0))))
                    if "ts" not in pt:
                        pt["ts"] = round(current_win_id + float(pt.get("s", 0)), 1)
                    merged_win[sec_k] = pt
                    merged_roll[int(round(pt["ts"]))] = pt

                for pt in rolling_series:
                    if pt.get("ts"):
                        merged_roll[int(round(float(pt["ts"])))] = pt

                if live_win is not None:
                    live_win["series"] = [merged_win[k] for k in sorted(merged_win.keys())]
                    live_win["rolling_series"] = [merged_roll[k] for k in sorted(merged_roll.keys())][-420:]
        except Exception:
            pass

    # Query recent completed windows & pair stats (cached for 8s per symbol)
    cache_entry = _pair_history_cache.get(symbol)
    if cache_entry and (now_ts - cache_entry["ts"]) < 8.0 and cache_entry.get("win_id") == current_win_id:
        recent_windows = cache_entry["recent_windows"]
        pair_stats = cache_entry["pair_stats"]
    else:
        recent_windows = []
        pair_stats = {
            "total_windows": 0,
            "signals_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "net_pnl_10usd": 0.0
        }
        if os.path.exists(ticks_db_path):
            try:
                conn = sqlite3.connect(ticks_db_path)
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT DISTINCT window_id
                    FROM sec_ticks
                    WHERE symbol = ? AND window_id < ?
                    ORDER BY window_id DESC
                    LIMIT 45
                    """,
                    (symbol, current_win_id)
                )
                win_ids = [r[0] for r in cur.fetchall() if r[0]]

                sig_min_s = 150.0 if is_15m else 58.0
                sig_max_s = 260.0 if is_15m else 105.0

                wins_total = 0
                losses_total = 0
                signals_total = 0
                candle_wins = 0
                candle_losses = 0

                for wid in win_ids:
                    cur.execute(
                        """
                        SELECT sec_from_start, seconds_to_expiry, window_open_index,
                               index_price, index_delta_pct, spot_delta_pct,
                               poly_up_ask, poly_down_ask, spot_price
                        FROM sec_ticks
                        WHERE symbol = ? AND window_id = ?
                        ORDER BY id ASC
                        """,
                        (symbol, wid)
                    )
                    w_rows = cur.fetchall()
                    if len(w_rows) < 10:
                        continue

                    min_rem = min((float(x[1] or 999) for x in w_rows))
                    if min_rem > 40:
                        continue

                    s0 = float(w_rows[0][2] or w_rows[0][3] or 0.0)
                    close_p = float(w_rows[-1][3] or 0.0)
                    close_delta = float(w_rows[-1][4] or 0.0)
                    if s0 <= 0 or close_p <= 0:
                        continue

                    # Find tick in entry zone (58-105s for 5MIN, 150-260s for 15MIN) where all 3 checklist conditions trigger
                    sig_ticks = []
                    valid_triggers = []
                    for x in w_rows:
                        sec_s = float(x[0] or 0.0)
                        if sec_s <= 0:
                            sec_s = duration_sec - float(x[1] or duration_sec)
                        if sig_min_s <= sec_s <= sig_max_s:
                            idx_p_val = float(x[3] or 0.0)
                            idx_d_val = float(x[4] or 0.0)
                            spot_d_val = float(x[5] or 0.0)
                            spot_p_val = float(x[8] or idx_p_val)
                            sig_ticks.append((sec_s, idx_p_val, idx_d_val))
                            if 0.03 <= abs(idx_d_val) <= 0.35:
                                if (idx_d_val > 0 and spot_d_val > 0 and spot_p_val >= idx_p_val * 0.99995) or (
                                    idx_d_val < 0 and spot_d_val < 0 and spot_p_val <= idx_p_val * 1.00005
                                ):
                                    valid_triggers.append((sec_s, idx_p_val, idx_d_val))

                    if not sig_ticks:
                        continue

                    if valid_triggers:
                        sig_sec, sig_price, sig_delta = valid_triggers[0]
                        signal_dir = "UP" if sig_delta > 0 else "DOWN"
                    else:
                        target_s = 180.0 if is_15m else 65.0
                        best_sig = min(sig_ticks, key=lambda item: abs(item[0] - target_s))
                        sig_sec, sig_price, sig_delta = best_sig
                        signal_dir = "SKIP"

                    # True Bybit mechanics: settle at t_entry + 300s/900s (which is sec_from_start ≈ sig_sec in next window wid + duration_sec)
                    bybit_settle_p = close_p
                    bybit_is_rolling = False
                    cur.execute(
                        """
                        SELECT sec_from_start, seconds_to_expiry, index_price
                        FROM sec_ticks
                        WHERE symbol = ? AND window_id = ?
                        ORDER BY id ASC
                        """,
                        (symbol, wid + duration_sec)
                    )
                    next_rows = cur.fetchall()
                    if next_rows:
                        best_next = min(
                            next_rows,
                            key=lambda nr: abs((float(nr[0]) if float(nr[0] or 0) > 0 else (duration_sec - float(nr[1] or duration_sec))) - sig_sec)
                        )
                        n_sec = float(best_next[0]) if float(best_next[0] or 0) > 0 else (duration_sec - float(best_next[1] or duration_sec))
                        if abs(n_sec - sig_sec) <= 25.0 and float(best_next[2] or 0) > 0:
                            bybit_settle_p = float(best_next[2])
                            bybit_is_rolling = True

                    # 1. Bybit Rolling (+300s/+900s from entry vs sig_price = Strike at entry click)
                    bybit_move_pct = ((bybit_settle_p - sig_price) / sig_price) * 100.0 if sig_price > 0 else 0.0
                    final_dir = "UP" if bybit_settle_p > sig_price else ("DOWN" if bybit_settle_p < sig_price else "FLAT")

                    # 2. Fixed Candle (Candle close vs S0)
                    candle_dir = "UP" if close_p > s0 else ("DOWN" if close_p < s0 else "FLAT")
                    candle_move_pct = ((close_p - s0) / s0) * 100.0 if s0 > 0 else 0.0

                    if signal_dir in ("UP", "DOWN") and final_dir in ("UP", "DOWN"):
                        signals_total += 1
                        if signal_dir == final_dir:
                            res_str = "WIN"
                            wins_total += 1
                        else:
                            res_str = "LOSS"
                            losses_total += 1

                        if signal_dir == candle_dir:
                            candle_res_str = "WIN"
                            candle_wins += 1
                        else:
                            candle_res_str = "LOSS"
                            candle_losses += 1
                    else:
                        res_str = "SKIP"
                        candle_res_str = "SKIP"

                    if len(recent_windows) < 15:
                        t_entry_str = datetime.fromtimestamp(wid + int(round(sig_sec)), tz=timezone.utc).strftime("%H:%M:%S")
                        t_exp_str = datetime.fromtimestamp(wid + duration_sec + int(round(sig_sec)), tz=timezone.utc).strftime("%H:%M:%S")
                        recent_windows.append({
                            "window_id": wid,
                            "time_label": f"{t_entry_str} ➔ {t_exp_str}",
                            "strike_s0": round(s0, 2),
                            "sig_sec": int(round(sig_sec)),
                            "sig_price": round(sig_price, 2),
                            "sig_delta_pct": round(sig_delta, 4),
                            "signal": signal_dir,
                            "close_price": round(bybit_settle_p, 2),
                            "close_delta_pct": round(bybit_move_pct, 4),
                            "candle_close_price": round(close_p, 2),
                            "candle_delta_pct": round(candle_move_pct, 4),
                            "candle_result": candle_res_str,
                            "is_rolling_300s": bybit_is_rolling,
                            "final_dir": final_dir,
                            "result": res_str
                        })

                conn.close()
                wr = round((wins_total / signals_total) * 100.0, 1) if signals_total > 0 else 0.0
                net_pnl = round(wins_total * 4.0 - losses_total * 5.0, 2)
                c_tot = candle_wins + candle_losses
                c_wr = round((candle_wins / c_tot) * 100.0, 1) if c_tot > 0 else 0.0
                c_pnl = round(candle_wins * 4.0 - candle_losses * 5.0, 2)
                pair_stats = {
                    "total_windows": len(win_ids),
                    "signals_count": signals_total,
                    "wins": wins_total,
                    "losses": losses_total,
                    "win_rate_pct": wr,
                    "net_pnl_10usd": net_pnl,
                    "candle_wins": candle_wins,
                    "candle_losses": candle_losses,
                    "candle_win_rate_pct": c_wr,
                    "candle_net_pnl_10usd": c_pnl
                }
                _pair_history_cache[symbol] = {
                    "ts": now_ts,
                    "win_id": current_win_id,
                    "recent_windows": recent_windows,
                    "pair_stats": pair_stats
                }
            except Exception as e:
                print(f"[PairDetail] SQLite error: {e}")

    # Live Auto-Paper stats across all 4 markets (BTC/ETH 5M & 15M)
    markets_overview = []
    spreads_db_path = os.path.join(os.path.dirname(__file__), "..", "spreads.db")
    if os.path.exists(spreads_db_path):
        try:
            conn_p = sqlite3.connect(spreads_db_path)
            cur_p = conn_p.cursor()
            cur_p.execute(
                """
                SELECT event_key, title, coin, status, net_profit, deal_type
                FROM paper_trades
                WHERE deal_type IN ('updown_5m', 'updown_15m', 'poly_48s_lag')
                """
            )
            p_rows = cur_p.fetchall()
            conn_p.close()

            for m_sym in ["BTCUSDT-5MIN", "ETHUSDT-5MIN", "BTCUSDT-15MIN", "ETHUSDT-15MIN"]:
                m_coin = "BTC" if "BTC" in m_sym else "ETH"
                m_wtype = "15MIN" if "15MIN" in m_sym else "5MIN"
                bb_w, bb_l, bb_open, bb_pnl = 0, 0, 0, 0.0
                poly_w, poly_l, poly_open, poly_pnl = 0, 0, 0, 0.0
                for ek, ttl, c, st, np_val, dt in p_rows:
                    ek_str = str(ek or "")
                    ttl_str = str(ttl or "")
                    matches_sym = (m_sym in ek_str) or (m_sym in ttl_str) or (
                        c == m_coin and m_wtype == "5MIN" and "15MIN" not in ek_str and "15MIN" not in ttl_str
                    )
                    if not matches_sym:
                        continue
                    if dt in ("updown_5m", "updown_15m"):
                        if st == "WON":
                            bb_w += 1
                            bb_pnl += float(np_val or 0.0)
                        elif st == "LOST":
                            bb_l += 1
                            bb_pnl += float(np_val or 0.0)
                        elif st == "OPEN":
                            bb_open += 1
                    elif dt == "poly_48s_lag":
                        if st == "WON":
                            poly_w += 1
                            poly_pnl += float(np_val or 0.0)
                        elif st == "LOST":
                            poly_l += 1
                            poly_pnl += float(np_val or 0.0)
                        elif st == "OPEN":
                            poly_open += 1

                bb_tot = bb_w + bb_l
                bb_wr = round((bb_w / bb_tot) * 100.0, 1) if bb_tot > 0 else 0.0
                poly_tot = poly_w + poly_l
                poly_wr = round((poly_w / poly_tot) * 100.0, 1) if poly_tot > 0 else 0.0
                markets_overview.append({
                    "symbol": m_sym,
                    "bybit_won": bb_w,
                    "bybit_lost": bb_l,
                    "bybit_open": bb_open,
                    "bybit_wr": bb_wr,
                    "bybit_pnl": round(bb_pnl, 2),
                    "poly_won": poly_w,
                    "poly_lost": poly_l,
                    "poly_open": poly_open,
                    "poly_wr": poly_wr,
                    "poly_pnl": round(poly_pnl, 2)
                })
        except Exception:
            pass

    return {
        "status": "ok",
        "symbol": symbol,
        "live": live_win,
        "all_windows": [
            {
                "symbol": w.get("symbol"),
                "coin": w.get("coin"),
                "window_type": w.get("window_type"),
                "sec_from_start": w.get("sec_from_start"),
                "index_delta_pct": w.get("index_delta_pct"),
                "index_price": w.get("index_price"),
                "window_open_index": w.get("window_open_index")
            }
            for w in all_windows
        ],
        "prices": prices,
        "recent_windows": recent_windows,
        "pair_stats": pair_stats,
        "markets_overview": markets_overview
    }



