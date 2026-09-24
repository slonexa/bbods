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



