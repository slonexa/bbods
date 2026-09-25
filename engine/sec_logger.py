import collections
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
import websocket
from curl_cffi import requests
from .auto_paper import AutoPaperTrader

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


class HighFrequencyTickLogger:
    """
    1-second High-Frequency Logger for Bybit Odds & Polymarket 5MIN / 15MIN Up/Down contracts.
    Simultaneously tracks:
      1. Index Price (ecIndexPrice — official Bybit Odds settlement benchmark)
      2. Spot Price (Bybit Spot BTCUSDT / ETHUSDT lastPrice)
      3. Futures Price (Bybit Linear Perp BTCUSDT / ETHUSDT lastPrice & markPrice)
      4. Rolling 60s TWAP (proxy for Polymarket's Chainlink 60s TWAP stream)
      5. Polymarket Live CLOB Orderbook (bestAsk / odds for Up & Down on 5m and 15m windows)
    """

    def __init__(self, db_path: str = "ticks.db"):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(self.base_dir, db_path)
        self.health_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sec_logger_health.json")
        self.auto_trades_db = os.path.join(self.base_dir, "spreads.db")
        self.paper_trader = AutoPaperTrader(self.auto_trades_db)

        self._lock = threading.Lock()
        self.coins = ["BTC", "ETH"]

        # Price states per coin: { 'BTC': {'spot': 0.0, 'futures': 0.0, 'index': 0.0, 'mark': 0.0, 'updated_at': 0.0} }
        self.prices = {
            c: {"spot": 0.0, "futures": 0.0, "index": 0.0, "mark": 0.0, "updated_at": 0.0}
            for c in self.coins
        }

        # Rolling 60-second history of (timestamp, index_price) for Chainlink 60s TWAP calculation
        self.price_history_60s = {
            c: collections.deque(maxlen=120) for c in self.coins
        }

        # Track window open prices: (symbol, window_start_epoch) -> {'index': x, 'spot': y, 'futures': z, 'twap': w}
        self.window_opens = {}

        # Polymarket 5m/15m token cache: (coin, wtype, window_id) -> {'up_token': str, 'down_token': str, 'accepting': int, 'slug': str}
        self.poly_tokens_cache = {}
        # Polymarket live CLOB books: (coin, wtype, window_id) -> {'up_ask': float, 'down_ask': float, 'up_bid': float, 'down_bid': float, 'odds_up': float, 'odds_down': float, 'accepting': int, 'slug': str, 'updated_at': float}
        self.poly_books = {}

        # Persistent HTTP sessions
        self.session = requests.Session()
        self.poly_session = requests.Session()

        self.total_ticks_logged = 0
        self.started_at = datetime.now(timezone.utc).isoformat()

        self.init_db()
        self._start_spot_ws()
        self._start_linear_ws()
        self._start_poly_clob_poller()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sec_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                coin TEXT,
                window_type TEXT,
                window_id INTEGER,
                odds_up REAL,
                odds_down REAL,
                prob_up REAL,
                prob_down REAL,
                turnover REAL,
                index_price REAL,
                spot_price REAL,
                futures_price REAL,
                mark_price REAL,
                window_open_index REAL,
                window_open_spot REAL,
                spot_delta_pct REAL,
                index_delta_pct REAL,
                seconds_to_expiry REAL,
                timestamp TEXT,
                sec_from_start REAL DEFAULT 0,
                twap_60s REAL DEFAULT 0,
                poly_up_ask REAL DEFAULT 0,
                poly_down_ask REAL DEFAULT 0,
                poly_odds_up REAL DEFAULT 0,
                poly_odds_down REAL DEFAULT 0,
                poly_accepting INTEGER DEFAULT 0,
                cross_hedge_cost REAL DEFAULT 0
            )
        ''')

        # Safe migrations for existing ticks.db
        new_cols = [
            ("sec_from_start", "REAL DEFAULT 0"),
            ("twap_60s", "REAL DEFAULT 0"),
            ("poly_up_ask", "REAL DEFAULT 0"),
            ("poly_down_ask", "REAL DEFAULT 0"),
            ("poly_odds_up", "REAL DEFAULT 0"),
            ("poly_odds_down", "REAL DEFAULT 0"),
            ("poly_accepting", "INTEGER DEFAULT 0"),
            ("cross_hedge_cost", "REAL DEFAULT 0"),
        ]
        for col_name, col_def in new_cols:
            try:
                cursor.execute(f"ALTER TABLE sec_ticks ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sec_ticks_sym_ts ON sec_ticks(symbol, timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sec_ticks_win ON sec_ticks(symbol, window_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sec_ticks_exp ON sec_ticks(seconds_to_expiry);")

        cursor.execute("SELECT COUNT(*) FROM sec_ticks")
        self.total_ticks_logged = cursor.fetchone()[0] or 0

        conn.commit()
        conn.close()
        print(f"[SecLogger] Initialized ticks.db (existing rows: {self.total_ticks_logged:,})")

    # ─── WebSocket 1: Bybit Spot (0ms Spot & USD Index Price) ───
    def _start_spot_ws(self):
        def run():
            while True:
                try:
                    ws = websocket.WebSocketApp(
                        "wss://stream.bybit.com/v5/public/spot",
                        on_open=lambda w: w.send(json.dumps({
                            "op": "subscribe",
                            "args": [f"tickers.{c}USDT" for c in self.coins]
                        })),
                        on_message=self._on_spot_message
                    )
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    print(f"[SecLogger] Spot WS error: {e}")
                time.sleep(3)

        t = threading.Thread(target=run, daemon=True)
        t.start()

    def _on_spot_message(self, ws, message):
        try:
            data = json.loads(message)
            topic = data.get("topic", "")
            if not topic.startswith("tickers."):
                return
            payload = data.get("data", {})
            symbol = payload.get("symbol", "")
            coin = symbol.replace("USDT", "")
            if coin in self.prices:
                with self._lock:
                    if payload.get("lastPrice"):
                        self.prices[coin]["spot"] = float(payload["lastPrice"])
                    if payload.get("usdIndexPrice") and self.prices[coin]["index"] <= 0:
                        self.prices[coin]["index"] = float(payload["usdIndexPrice"])
                    self.prices[coin]["updated_at"] = time.time()
        except Exception:
            pass

    # ─── WebSocket 2: Bybit Linear Perp (0ms Futures, Mark & Index Price) ───
    def _start_linear_ws(self):
        def run():
            while True:
                try:
                    ws = websocket.WebSocketApp(
                        "wss://stream.bybit.com/v5/public/linear",
                        on_open=lambda w: w.send(json.dumps({
                            "op": "subscribe",
                            "args": [f"tickers.{c}USDT" for c in self.coins]
                        })),
                        on_message=self._on_linear_message
                    )
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception as e:
                    print(f"[SecLogger] Linear WS error: {e}")
                time.sleep(3)

        t = threading.Thread(target=run, daemon=True)
        t.start()

    def _on_linear_message(self, ws, message):
        try:
            data = json.loads(message)
            topic = data.get("topic", "")
            if not topic.startswith("tickers."):
                return
            payload = data.get("data", {})
            symbol = payload.get("symbol", "")
            coin = symbol.replace("USDT", "")
            if coin in self.prices:
                with self._lock:
                    if payload.get("lastPrice"):
                        self.prices[coin]["futures"] = float(payload["lastPrice"])
                    if payload.get("indexPrice"):
                        self.prices[coin]["index"] = float(payload["indexPrice"])
                    if payload.get("markPrice"):
                        self.prices[coin]["mark"] = float(payload["markPrice"])
                    self.prices[coin]["updated_at"] = time.time()
        except Exception:
            pass

    # ─── Background Poller 3: Polymarket 5m & 15m Live CLOB Orderbooks ───
    def _start_poly_clob_poller(self):
        def run():
            windows_cfg = [
                ("BTC", "5MIN", "5m", 300),
                ("BTC", "15MIN", "15m", 900),
                ("ETH", "5MIN", "5m", 300),
                ("ETH", "15MIN", "15m", 900),
            ]
            while True:
                t0 = time.time()
                try:
                    now_int = int(t0)
                    active_keys = []
                    token_requests = []

                    for coin, wtype, poly_dur, dur_sec in windows_cfg:
                        win_id = (now_int // dur_sec) * dur_sec
                        key = (coin, wtype, win_id)
                        slug = f"{coin.lower()}-updown-{poly_dur}-{win_id}"

                        if key not in self.poly_tokens_cache:
                            try:
                                r = self.poly_session.get(
                                    f"https://gamma-api.polymarket.com/events?slug={slug}",
                                    timeout=3
                                )
                                if r.status_code == 200:
                                    ev_list = r.json()
                                    if ev_list and ev_list[0].get("markets"):
                                        m = ev_list[0]["markets"][0]
                                        tids = json.loads(m.get("clobTokenIds", "[]"))
                                        if len(tids) >= 2:
                                            self.poly_tokens_cache[key] = {
                                                "up_token": tids[0],
                                                "down_token": tids[1],
                                                "accepting": 1 if m.get("acceptingOrders") else 0,
                                                "slug": slug
                                            }
                            except Exception:
                                pass

                        # Prune old token cache entries
                        if len(self.poly_tokens_cache) > 24:
                            oldest = sorted(self.poly_tokens_cache.keys(), key=lambda k: k[2])[:8]
                            for ok in oldest:
                                self.poly_tokens_cache.pop(ok, None)
                                self.poly_books.pop(ok, None)

                        t_info = self.poly_tokens_cache.get(key)
                        if t_info:
                            active_keys.append((key, t_info))
                            token_requests.append({"token_id": t_info["up_token"]})
                            token_requests.append({"token_id": t_info["down_token"]})

                    if token_requests:
                        rb = self.poly_session.post(
                            "https://clob.polymarket.com/books",
                            json=token_requests,
                            timeout=3
                        )
                        if rb.status_code == 200:
                            books = rb.json()
                            now_book_ts = time.time()
                            with self._lock:
                                for idx, (key, t_info) in enumerate(active_keys):
                                    if idx * 2 + 1 >= len(books):
                                        break
                                    b_up = books[idx * 2]
                                    b_down = books[idx * 2 + 1]
                                    up_asks = b_up.get("asks", [])
                                    down_asks = b_down.get("asks", [])
                                    up_bids = b_up.get("bids", [])
                                    down_bids = b_down.get("bids", [])

                                    ua = min((float(x["price"]) for x in up_asks), default=0.0)
                                    da = min((float(x["price"]) for x in down_asks), default=0.0)
                                    ub = max((float(x["price"]) for x in up_bids), default=0.0)
                                    db = max((float(x["price"]) for x in down_bids), default=0.0)

                                    odds_u = round(1.0 / ua, 4) if ua > 0 else 0.0
                                    odds_d = round(1.0 / da, 4) if da > 0 else 0.0
                                    is_acc = 1 if (t_info["accepting"] and (ua > 0 or da > 0)) else 0

                                    self.poly_books[key] = {
                                        "up_ask": ua,
                                        "down_ask": da,
                                        "up_bid": ub,
                                        "down_bid": db,
                                        "odds_up": odds_u,
                                        "odds_down": odds_d,
                                        "accepting": is_acc,
                                        "slug": t_info["slug"],
                                        "updated_at": now_book_ts
                                    }
                except Exception:
                    pass

                elapsed = time.time() - t0
                time.sleep(max(0.15, 1.0 - elapsed))

        t = threading.Thread(target=run, daemon=True)
        t.start()

    def fetch_odds_updown(self) -> dict:
        """Fetch live Up/Down 5MIN and 15MIN tickers from Bybit Odds API."""
        endpoint = "https://www.bybit.com/x-api/option/event/webapi/public/ticker_all"
        try:
            r = self.session.get(endpoint, impersonate="chrome", timeout=4)
            data = r.json()
            if data.get("ret_code") == 0:
                items = data.get("result", [])
                result = {}
                for item in items:
                    sym = item.get("symbol", "")
                    if "-5MIN-" in sym or "-15MIN-" in sym:
                        result[sym] = item
                return result
        except Exception as e:
            print(f"[SecLogger] Odds fetch error: {e}")
        return {}

    def save_batch(self, batch: list):
        if not batch:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT INTO sec_ticks (
                    symbol, coin, window_type, window_id,
                    odds_up, odds_down, prob_up, prob_down, turnover,
                    index_price, spot_price, futures_price, mark_price,
                    window_open_index, window_open_spot,
                    spot_delta_pct, index_delta_pct,
                    seconds_to_expiry, timestamp,
                    sec_from_start, twap_60s,
                    poly_up_ask, poly_down_ask, poly_odds_up, poly_odds_down,
                    poly_accepting, cross_hedge_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()
            conn.close()
            self.total_ticks_logged += len(batch)
        except Exception as e:
            print(f"[SecLogger] DB batch write error: {e}")

    def cleanup_old_ticks(self, retention_days: int = 5):
        """Keep database clean over multi-day runs (default: 5 days retention)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sec_ticks WHERE timestamp < datetime('now', ?)", (f"-{retention_days} days",))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def run_forever(self):
        print("[SecLogger] Waiting 2s for Spot, Linear & Polymarket CLOB feeds to warm up...")
        time.sleep(2.0)
        print("[SecLogger] Starting 1-second high-frequency logging loop...")

        batch = []
        last_flush = time.time()
        cycle_idx = 0

        windows_config = [
            ("BTC", "5MIN", 300),
            ("BTC", "15MIN", 900),
            ("ETH", "5MIN", 300),
            ("ETH", "15MIN", 900),
        ]

        while True:
            loop_start = time.time()
            cycle_idx += 1

            try:
                odds_map = self.fetch_odds_updown()
                now_ts = time.time()
                now_iso = datetime.now(timezone.utc).isoformat()

                with self._lock:
                    prices_snap = {c: dict(self.prices[c]) for c in self.coins}
                    poly_snap = dict(self.poly_books)

                # Update rolling 60s TWAP per coin
                twap_map = {}
                for c in self.coins:
                    cp = prices_snap[c].get("index") or prices_snap[c].get("spot") or 0.0
                    if cp > 0:
                        dq = self.price_history_60s[c]
                        dq.append((now_ts, cp))
                        while dq and (now_ts - dq[0][0]) > 60.0:
                            dq.popleft()
                        twap_map[c] = round(sum(x[1] for x in dq) / len(dq), 2)
                    else:
                        twap_map[c] = 0.0

                live_windows_summary = []

                for coin, wtype, duration_sec in windows_config:
                    base_sym = f"{coin}USDT-{wtype}"
                    up_item = odds_map.get(f"{base_sym}-UP")
                    down_item = odds_map.get(f"{base_sym}-DOWN")

                    if not up_item and not down_item:
                        continue

                    window_id = int(now_ts // duration_sec) * duration_sec
                    sec_from_start = round(now_ts - window_id, 2)
                    sec_to_exp = round(duration_sec - (now_ts % duration_sec), 2)

                    c_prices = prices_snap.get(coin, {})
                    idx_p = c_prices.get("index") or c_prices.get("spot") or 0.0
                    spot_p = c_prices.get("spot") or idx_p
                    fut_p = c_prices.get("futures") or idx_p
                    mark_p = c_prices.get("mark") or fut_p
                    twap_60s = twap_map.get(coin) or idx_p

                    if idx_p <= 0 and spot_p <= 0:
                        continue

                    # Record window opening prices on first tick of the window
                    win_key = (base_sym, window_id)
                    if win_key not in self.window_opens:
                        # If restarting mid-window, try to recover S0 from ticks.db
                        db_open_idx, db_open_spot = 0.0, 0.0
                        try:
                            conn_r = sqlite3.connect(self.db_path)
                            cur_r = conn_r.cursor()
                            cur_r.execute(
                                "SELECT window_open_index, window_open_spot FROM sec_ticks WHERE symbol=? AND window_id=? ORDER BY id ASC LIMIT 1",
                                (base_sym, window_id)
                            )
                            row_r = cur_r.fetchone()
                            conn_r.close()
                            if row_r:
                                db_open_idx = float(row_r[0] or 0.0)
                                db_open_spot = float(row_r[1] or 0.0)
                        except Exception:
                            pass

                        self.window_opens[win_key] = {
                            "index": db_open_idx or idx_p,
                            "spot": db_open_spot or spot_p,
                            "futures": fut_p,
                            "twap": twap_60s,
                            "recorded_at": now_ts,
                            "series": []
                        }
                        # Prune old window keys
                        if len(self.window_opens) > 50:
                            oldest = sorted(self.window_opens.keys(), key=lambda k: k[1])[:20]
                            for ok in oldest:
                                self.window_opens.pop(ok, None)

                    open_info = self.window_opens[win_key]
                    open_idx = open_info["index"] or idx_p
                    open_spot = open_info["spot"] or spot_p
                    open_fut = open_info.get("futures") or fut_p

                    spot_delta_pct = round(((spot_p - open_spot) / open_spot) * 100.0, 5) if open_spot > 0 else 0.0
                    index_delta_pct = round(((idx_p - open_idx) / open_idx) * 100.0, 5) if open_idx > 0 else 0.0
                    fut_delta_pct = round(((fut_p - open_fut) / open_fut) * 100.0, 5) if open_fut > 0 else 0.0

                    prob_up = float(up_item.get("wp", 0.0) or 0.0) if up_item else 0.0
                    prob_down = float(down_item.get("wp", 0.0) or 0.0) if down_item else 0.0

                    odds_up_str = up_item.get("pr", "") if up_item else ""
                    odds_down_str = down_item.get("pr", "") if down_item else ""
                    odds_up = float(odds_up_str) if odds_up_str else (round(1.0 / prob_up, 4) if prob_up > 0 else 0.0)
                    odds_down = float(odds_down_str) if odds_down_str else (round(1.0 / prob_down, 4) if prob_down > 0 else 0.0)

                    tv_up = float(up_item.get("tv", 0.0) or 0.0) if up_item else 0.0
                    tv_down = float(down_item.get("tv", 0.0) or 0.0) if down_item else 0.0
                    turnover = round(tv_up + tv_down, 2)

                    # Polymarket live CLOB data for this exact (coin, wtype, window_id)
                    p_book = poly_snap.get((coin, wtype, window_id), {})
                    poly_up_ask = p_book.get("up_ask", 0.0)
                    poly_down_ask = p_book.get("down_ask", 0.0)
                    poly_odds_up = p_book.get("odds_up", 0.0)
                    poly_odds_down = p_book.get("odds_down", 0.0)
                    poly_accepting = p_book.get("accepting", 0)
                    poly_slug = p_book.get("slug", "")

                    # Best cross-exchange hedge cost:
                    # Combo 1: Bybit UP (1/odds_up) + Poly DOWN (poly_down_ask)
                    # Combo 2: Bybit DOWN (1/odds_down) + Poly UP (poly_up_ask)
                    cost_c1 = (1.0 / odds_up + poly_down_ask) if (odds_up > 0 and poly_down_ask > 0) else 0.0
                    cost_c2 = (1.0 / odds_down + poly_up_ask) if (odds_down > 0 and poly_up_ask > 0) else 0.0
                    valid_costs = [c for c in (cost_c1, cost_c2) if c > 0]
                    cross_hedge_cost = round(min(valid_costs), 4) if valid_costs else 0.0

                    # Append 1s tick to current window series AND continuous 420s rolling series (for 300s-from-entry Bybit tracking)
                    tick_pt = {
                        "ts": round(now_ts, 1),
                        "s": round(sec_from_start, 1),
                        "idx": idx_p,
                        "spot": spot_p,
                        "fut": fut_p,
                        "d": round(index_delta_pct, 4),
                        "p_up": poly_up_ask,
                        "p_dn": poly_down_ask
                    }
                    series_list = open_info.setdefault("series", [])
                    series_list.append(tick_pt)
                    if len(series_list) > 320:
                        del series_list[:-320]

                    if not hasattr(self, "rolling_series"):
                        self.rolling_series = {}
                    r_list = self.rolling_series.setdefault(base_sym, [])
                    r_list.append(tick_pt)
                    if len(r_list) > 450:
                        del r_list[:-450]

                    batch.append((
                        base_sym, coin, wtype, window_id,
                        odds_up, odds_down, prob_up, prob_down, turnover,
                        idx_p, spot_p, fut_p, mark_p,
                        open_idx, open_spot,
                        spot_delta_pct, index_delta_pct,
                        sec_to_exp, now_iso,
                        sec_from_start, twap_60s,
                        poly_up_ask, poly_down_ask, poly_odds_up, poly_odds_down,
                        poly_accepting, cross_hedge_cost
                    ))

                    live_windows_summary.append({
                        "symbol": base_sym,
                        "coin": coin,
                        "window_type": wtype,
                        "window_id": window_id,
                        "now_ts": round(now_ts, 1),
                        "odds_up": odds_up,
                        "odds_down": odds_down,
                        "prob_up": round(prob_up, 4),
                        "prob_down": round(prob_down, 4),
                        "turnover": turnover,
                        "index_price": idx_p,
                        "spot_price": spot_p,
                        "futures_price": fut_p,
                        "mark_price": mark_p,
                        "twap_60s": twap_60s,
                        "window_open_index": open_idx,
                        "window_open_spot": open_spot,
                        "window_open_futures": open_fut,
                        "index_delta_pct": index_delta_pct,
                        "spot_delta_pct": spot_delta_pct,
                        "fut_delta_pct": fut_delta_pct,
                        "sec_from_start": sec_from_start,
                        "seconds_to_expiry": sec_to_exp,
                        "poly_up_ask": poly_up_ask,
                        "poly_down_ask": poly_down_ask,
                        "poly_odds_up": poly_odds_up,
                        "poly_odds_down": poly_odds_down,
                        "poly_accepting": poly_accepting,
                        "poly_slug": poly_slug,
                        "cross_hedge_cost": cross_hedge_cost,
                        "series": series_list[-300:],
                        "rolling_series": r_list[-420:]
                    })

                # Write health & live 1s HFT snapshot EVERY second (atomically) for real-time manual cockpit
                try:
                    db_size_mb = round(os.path.getsize(self.db_path) / (1024 * 1024), 2) if os.path.exists(self.db_path) else 0.0
                    tmp_health = self.health_file + ".tmp"
                    with open(tmp_health, "w", encoding="utf-8") as hf:
                        json.dump({
                            "status": "running",
                            "started_at": self.started_at,
                            "last_update": now_iso,
                            "total_ticks": self.total_ticks_logged + len(batch),
                            "db_size_mb": db_size_mb,
                            "prices": prices_snap,
                            "windows": live_windows_summary
                        }, hf)
                    os.replace(tmp_health, self.health_file)
                except Exception:
                    pass

                # Run 1-second HFT signal entry & exact +300s/+900s settlement every second
                try:
                    self.paper_trader.run_fast_1s_cycle()
                except Exception:
                    pass

                # Flush batch to SQLite every 5 seconds
                if time.time() - last_flush >= 5.0 and batch:
                    self.save_batch(batch)
                    batch.clear()
                    last_flush = time.time()

                    # Reload config & check daily cross-trades every 5s
                    try:
                        self.paper_trader.run_cycle()
                    except Exception as e:
                        print(f"[SecLogger] AutoPaper cycle error: {e}")

                if cycle_idx % 3600 == 0:
                    self.cleanup_old_ticks(retention_days=5)

            except Exception as e:
                print(f"[SecLogger] Loop error: {e}")

            elapsed = time.time() - loop_start
            sleep_sec = max(0.05, 1.0 - elapsed)
            time.sleep(sleep_sec)


if __name__ == "__main__":
    logger = HighFrequencyTickLogger()
    logger.run_forever()
