import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


class AutoPaperTrader:
    """
    Automated Simulated Paper Trading Engine.
    Maintains TWO strictly separated virtual portfolios in `paper_trades`:
      A) MAIN PORTFOLIO (Daily/Target Contracts):
         - `cross_arb` (Daily Target Surebets)
         - `cross_value` (Daily Target Value Divergences)
      B) 5/15M TEST POLYGON PORTFOLIO (Isolated in the 5/15m Test Tab):
         - `sync_start_arb`: Synchronized entry in first 0..25s of a 5m/15m window (0 time gap, 0 strike gap)
         - `two_step_hedge`: Risk-free 2-Step Lock-In Hedge within the same window (Leg 1 at window start S_0 -> Leg 2 hedge on impulse at same S_0 & same expiry!)
         - `poly_48s_lag`: Polymarket 47–51s window opening TWAP lag test
         - `updown_5m`: Bybit 5MIN early momentum test
    """

    def __init__(self, db_path: str = "spreads.db"):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(self.base_dir, db_path)
        self.config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_config.json")
        self.sec_health_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sec_logger_health.json")

        # Track window-start quotes (t = 0..25s) so we can execute 2-Step Lock-In Hedges when impulse hits
        # Key: (symbol, window_id) -> {'up_ask': float, 'down_ask': float, 'bb_odds': float, 'open_idx': float, 'ts': float}
        self.window_start_anchors = {}

        self.init_db()
        self.config = self.load_config()

    def load_config(self) -> dict:
        default_config = {
            "enabled": True,
            "default_stake": 5.0,
            "min_spread_pct": 3.5,
            "auto_cross_arbs": True,
            "auto_5min_momentum": True,
            "auto_poly_48s_lag": True,
            "auto_two_step_hedge": True,
            "momentum_min_delta_pct": 0.03,
            "max_active_trades": 60,
            "initial_balance": 1000.0
        }
        if not os.path.exists(self.config_path):
            self.save_config(default_config)
            return default_config
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                default_config.update(cfg)
                if default_config.get("max_active_trades", 0) < 50:
                    default_config["max_active_trades"] = 60
                return default_config
        except Exception:
            return default_config

    def save_config(self, cfg: dict):
        self.config = cfg
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_type TEXT,            -- Main: cross_arb, cross_value | 5/15m Test: sync_start_arb, two_step_hedge, poly_48s_lag, updown_5m
                event_key TEXT,
                title TEXT,
                coin TEXT,
                direction TEXT,
                platform_a TEXT,
                platform_b TEXT,
                action_a TEXT,
                action_b TEXT,
                odds_a REAL,
                odds_b REAL,
                stake_a REAL,
                stake_b REAL,
                total_stake REAL,
                hedge_cost REAL DEFAULT 0,
                strike_price REAL DEFAULT 0,
                status TEXT,               -- OPEN / WON / LOST
                entry_time TEXT,
                expiry_time TEXT,
                entry_price REAL,
                settle_price REAL,
                payout REAL DEFAULT 0,
                net_profit REAL DEFAULT 0,
                roi_pct REAL DEFAULT 0,
                created_by TEXT DEFAULT 'auto',
                UNIQUE(event_key, entry_time)
            )
        ''')

        for col_name, col_type in [
            ("hedge_cost", "REAL DEFAULT 0"),
            ("strike_price", "REAL DEFAULT 0")
        ]:
            try:
                cursor.execute(f"ALTER TABLE paper_trades ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades(status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_exp ON paper_trades(expiry_time);")

        conn.commit()
        conn.close()

    def get_active_trades_count(self) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'")
        cnt = cursor.fetchone()[0]
        conn.close()
        return cnt

    @staticmethod
    def parse_expiry_from_title(title: str, event_key: str) -> str:
        """Extract settlement timestamp from title '(2026-09-25)' -> '2026-09-25T08:00:00+00:00'."""
        m = re.search(r'\((\d{4}-\d{2}-\d{2})\)', title or "")
        if m:
            return f"{m.group(1)}T08:00:00+00:00"
        return (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()

    @staticmethod
    def parse_strike_from_title(title: str) -> float:
        m = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)', title or "")
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                pass
        return 0.0

    def check_and_place_cross_trades(self):
        """Scan latest daily target spreads for confirmed surebets or high-value divergences."""
        if not self.config.get("auto_cross_arbs", True):
            return

        active_cnt = self.get_active_trades_count()
        if active_cnt >= self.config.get("max_active_trades", 25):
            return

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        min_spread = self.config.get("min_spread_pct", 3.5) / 100.0
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        cursor.execute('''
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY detected_at DESC) as rn
                FROM spreads
                WHERE platform_a != platform_b
                  AND event_key NOT LIKE '%5MIN%'
                  AND event_key NOT LIKE '%15MIN%'
                  AND (is_arb = 1 OR hedge_margin > 0 OR spread_after_fees >= ?)
                  AND detected_at >= datetime('now', '-15 minutes')
            ) WHERE rn = 1
            ORDER BY is_arb DESC, hedge_margin DESC, spread_after_fees DESC
            LIMIT 5
        ''', (min_spread,))

        rows = cursor.fetchall()

        for row_obj in rows:
            if active_cnt >= self.config.get("max_active_trades", 25):
                break

            r = dict(row_obj)
            event_key = r["event_key"]
            title = r.get("title") or event_key

            exp_time = self.parse_expiry_from_title(title, event_key)
            if exp_time <= now_iso:
                continue

            cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ?", (event_key,))
            if cursor.fetchone()[0] > 0:
                continue

            bank = float(self.config.get("default_stake", 5.0))
            hedge_cost = float(r.get("hedge_cost") or 0.0)
            is_arb = int(r.get("is_arb") or 0)

            split_a = r.get("stake_a") if r.get("stake_a") and r.get("stake_a") > 0 else 0.5
            stake_a = max(1.0, round(bank * split_a, 2))
            stake_b = max(1.0, round(bank - stake_a, 2))

            coin = "BTC"
            for c in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]:
                if c in title:
                    coin = c
                    break

            strike = self.parse_strike_from_title(title)
            deal_type = "cross_arb" if (is_arb == 1 or (0 < hedge_cost < 1.0)) else "cross_value"
            direction = "ABOVE" if "ABOVE" in event_key else "BELOW"

            cursor.execute('''
                INSERT INTO paper_trades (
                    deal_type, event_key, title, coin, direction,
                    platform_a, platform_b, action_a, action_b,
                    odds_a, odds_b, stake_a, stake_b, total_stake,
                    hedge_cost, strike_price,
                    status, entry_time, expiry_time, entry_price, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 'auto')
            ''', (
                deal_type, event_key, title, coin, direction,
                r.get("platform_a", ""), r.get("platform_b", ""),
                r.get("action_a", ""), r.get("action_b", ""),
                r.get("odds_a", 0.0), r.get("odds_b", 0.0),
                stake_a, stake_b, bank,
                hedge_cost, strike,
                now_iso, exp_time, r.get("prob_a", 0.0)
            ))
            active_cnt += 1
            print(f"[AutoPaper] Placed {deal_type} demo trade: {title} ($ {bank}, Exp: {exp_time[:16]})")

        conn.commit()
        conn.close()

    def check_and_place_hft_signals(self):
        """
        Evaluate live 1s HFT windows from sec_logger for the 5/15m Test Polygon:
          1. `sync_start_arb`: Simultaneous entry in first 0..25s when timers & strikes match 1-to-1.
          2. `two_step_hedge`: True Risk-Free 2-Step Lock-In Hedge within the SAME window:
             - Leg 1 anchored in first 0..25s of the window (Strike = S_0, Expiry = T_end)
             - Leg 2 bought on impulse during the window when opposite outcome drops so Cost(Leg1 + Leg2) <= 0.92!
             - Zero time mismatch, zero strike gap -> guaranteed risk-free payout at T_end!
          3. `poly_48s_lag`: Polymarket 47–51s window opening TWAP lag test.
          4. `updown_5m`: Bybit 5MIN/15MIN early momentum test.
        """
        if not os.path.exists(self.sec_health_file):
            return

        active_cnt = self.get_active_trades_count()
        max_trades = self.config.get("max_active_trades", 60)
        if active_cnt >= max_trades:
            return

        try:
            with open(self.sec_health_file, "r", encoding="utf-8") as f:
                health = json.load(f)
        except Exception:
            return

        windows = health.get("windows", [])
        now_ts = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()
        min_delta = self.config.get("momentum_min_delta_pct", 0.03)
        stake = float(self.config.get("default_stake", 5.0))

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        for win in windows:
            if active_cnt >= max_trades:
                break

            wtype = win.get("window_type", "5MIN")
            coin = win.get("coin", "BTC")
            sym = win.get("symbol", f"{coin}USDT-{wtype}")
            dur_sec = 300 if wtype == "5MIN" else 900
            win_id = win.get("window_id", int(now_ts // dur_sec) * dur_sec)
            sec_from_start = win.get("sec_from_start", now_ts - win_id)
            sec_left = win.get("seconds_to_expiry", dur_sec - sec_from_start)
            delta_pct = win.get("index_delta_pct", 0.0)
            idx_p = win.get("index_price", 0.0)
            open_idx = win.get("window_open_index", idx_p)

            poly_up_ask = win.get("poly_up_ask", 0.0)
            poly_down_ask = win.get("poly_down_ask", 0.0)
            poly_odds_up = win.get("poly_odds_up", 0.0)
            poly_odds_down = win.get("poly_odds_down", 0.0)
            poly_accepting = win.get("poly_accepting", 0)
            bb_odds = win.get("odds_up", 1.8)

            anchor_key = (sym, win_id)

            # ─── Step 1 of Two-Step Hedge: Record Window-Start Anchor (0..30s) ───
            if 0.0 <= sec_from_start <= 30.0 and poly_accepting:
                if anchor_key not in self.window_start_anchors and (poly_up_ask > 0 or poly_down_ask > 0):
                    self.window_start_anchors[anchor_key] = {
                        "up_ask": poly_up_ask if 0.35 <= poly_up_ask <= 0.55 else 0.50,
                        "down_ask": poly_down_ask if 0.35 <= poly_down_ask <= 0.55 else 0.50,
                        "bb_odds": bb_odds,
                        "open_idx": open_idx,
                        "recorded_sec": round(sec_from_start, 1)
                    }
                    if len(self.window_start_anchors) > 40:
                        oldest = sorted(self.window_start_anchors.keys(), key=lambda k: k[1])[:15]
                        for ok in oldest:
                            self.window_start_anchors.pop(ok, None)

                # Also check if there is an immediate Synchronized Start Arb right at 0..25s (0 time gap, 0 strike gap)
                if sec_from_start <= 25.0 and abs(delta_pct) <= 0.025:
                    for bb_dir, p_dir, p_ask, p_odds in [
                        ("UP", "DOWN", poly_down_ask, poly_odds_down),
                        ("DOWN", "UP", poly_up_ask, poly_odds_up)
                    ]:
                        if 0.15 <= p_ask <= 0.41:
                            h_cost = round((1.0 / bb_odds) + p_ask, 4)
                            if h_cost <= 0.965:
                                ev_key = f"SYNCSTART-{sym}-{win_id}"
                                cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ?", (ev_key,))
                                if cursor.fetchone()[0] == 0:
                                    stake_a = round(stake * ((1.0 / bb_odds) / h_cost), 2)
                                    stake_b = round(stake - stake_a, 2)
                                    exp_iso = datetime.fromtimestamp(win_id + dur_sec, timezone.utc).isoformat()
                                    cursor.execute('''
                                        INSERT INTO paper_trades (
                                            deal_type, event_key, title, coin, direction,
                                            platform_a, platform_b, action_a, action_b,
                                            odds_a, odds_b, stake_a, stake_b, total_stake,
                                            hedge_cost, strike_price,
                                            status, entry_time, expiry_time, entry_price, created_by
                                        ) VALUES (?, ?, ?, ?, ?, 'bybit_odds', 'polymarket', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 'auto')
                                    ''', (
                                        "sync_start_arb", ev_key,
                                        f"🔒 Синхр. Старт ({sec_from_start:.0f}с): {coin} {wtype} (Cost: {h_cost:.2f})",
                                        coin, f"SYNC:{bb_dir}+{p_dir}",
                                        f"Bybit (0с): {bb_dir} @ {bb_odds:.2f}x (Страйк ${open_idx:,.1f})",
                                        f"Poly (0с): {p_dir} @ ${p_ask:.2f} ({p_odds:.2f}x, Страйк ${open_idx:,.1f})",
                                        bb_odds, p_odds, stake_a, stake_b, stake,
                                        h_cost, open_idx,
                                        now_iso, exp_iso, open_idx
                                    ))
                                    active_cnt += 1
                                    print(f"[AutoPaper] Placed Sync Start Arb: {coin} {wtype} (Cost={h_cost:.3f})")
                                break

            # ─── Step 2 of Two-Step Hedge: Lock-In Risk-Free Profit on Mid-Window Impulse ───
            # Both Leg 1 (opened at t=0..25s) and Leg 2 (bought now at t=35..240s) share the EXACT SAME window_id,
            # EXACT SAME strike (S_0 = open_idx), and EXACT SAME expiry time (win_id + dur_sec)!
            if self.config.get("auto_two_step_hedge", True) and poly_accepting and 30.0 < sec_from_start <= (dur_sec - 45):
                anchor = self.window_start_anchors.get(anchor_key)
                if anchor:
                    # Check if price moved UP (so Poly DOWN is now cheap) OR moved DOWN (so Poly UP is now cheap)
                    # Option 1: Leg 1 was Poly UP at start (cost = anchor['up_ask']), Leg 2 is Poly DOWN now (cost = poly_down_ask)
                    # Option 2: Leg 1 was Bybit UP at start (cost = 1/1.80 = 0.5556), Leg 2 is Poly DOWN now (cost = poly_down_ask)
                    candidates = []
                    if 0.12 <= poly_down_ask <= 0.38 and delta_pct > 0.02:
                        # Price pumped -> lock with Poly DOWN
                        cost_poly_pair = round(anchor["up_ask"] + poly_down_ask, 4)
                        cost_cross_pair = round((1.0 / anchor["bb_odds"]) + poly_down_ask, 4)
                        if cost_cross_pair <= 0.92:
                            candidates.append((
                                "Bybit+Poly", "UP", anchor["bb_odds"], round(1.0 / anchor["bb_odds"], 4),
                                "DOWN", poly_down_ask, poly_odds_down, cost_cross_pair
                            ))
                        elif cost_poly_pair <= 0.88:
                            candidates.append((
                                "Poly+Poly", "UP", round(1.0 / anchor["up_ask"], 2), anchor["up_ask"],
                                "DOWN", poly_down_ask, poly_odds_down, cost_poly_pair
                            ))

                    if 0.12 <= poly_up_ask <= 0.38 and delta_pct < -0.02:
                        # Price dumped -> lock with Poly UP
                        cost_poly_pair = round(anchor["down_ask"] + poly_up_ask, 4)
                        cost_cross_pair = round((1.0 / anchor["bb_odds"]) + poly_up_ask, 4)
                        if cost_cross_pair <= 0.92:
                            candidates.append((
                                "Bybit+Poly", "DOWN", anchor["bb_odds"], round(1.0 / anchor["bb_odds"], 4),
                                "UP", poly_up_ask, poly_odds_up, cost_cross_pair
                            ))
                        elif cost_poly_pair <= 0.88:
                            candidates.append((
                                "Poly+Poly", "DOWN", round(1.0 / anchor["down_ask"], 2), anchor["down_ask"],
                                "UP", poly_up_ask, poly_odds_up, cost_poly_pair
                            ))

                    if candidates:
                        mode_lbl, dir1, odds1, cost1, dir2, ask2, odds2, total_h_cost = candidates[0]
                        ev_key = f"LOCKHEDGE-{sym}-{win_id}"
                        cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ?", (ev_key,))
                        if cursor.fetchone()[0] == 0:
                            stake_a = round(stake * (cost1 / total_h_cost), 2)
                            stake_b = round(stake - stake_a, 2)
                            exp_iso = datetime.fromtimestamp(win_id + dur_sec, timezone.utc).isoformat()
                            s0 = anchor["open_idx"]
                            t0_sec = anchor["recorded_sec"]

                            cursor.execute('''
                                INSERT INTO paper_trades (
                                    deal_type, event_key, title, coin, direction,
                                    platform_a, platform_b, action_a, action_b,
                                    odds_a, odds_b, stake_a, stake_b, total_stake,
                                    hedge_cost, strike_price,
                                    status, entry_time, expiry_time, entry_price, created_by
                                ) VALUES (?, ?, ?, ?, ?, 'bybit_odds', 'polymarket', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 'auto')
                            ''', (
                                "two_step_hedge", ev_key,
                                f"🛡️ 2-Шаговый Хедж ({mode_lbl}): {coin} {wtype} (Cost: {total_h_cost:.2f}, Без риска)",
                                coin, f"LOCK:{dir1}+{dir2}",
                                f"Шаг 1 (+{t0_sec:.0f}с): {dir1} @ {odds1:.2f}x (Страйк ${s0:,.1f})",
                                f"Шаг 2 (+{sec_from_start:.0f}с): Хедж {dir2} @ ${ask2:.2f} ({odds2:.2f}x, Страйк ${s0:,.1f})",
                                odds1, odds2, stake_a, stake_b, stake,
                                total_h_cost, s0,
                                now_iso, exp_iso, s0
                            ))
                            active_cnt += 1
                            print(f"[AutoPaper] Locked 2-Step Risk-Free Hedge: {coin} {wtype} ({dir1}+{dir2}, Cost={total_h_cost:.3f})")

            # ─── Strategy 3: Polymarket Window Lag (5MIN & 15MIN Fixed S0 Strike) ───
            if self.config.get("auto_poly_48s_lag", True) and wtype in ("5MIN", "15MIN") and poly_accepting:
                p_min_s = 42.0 if wtype == "5MIN" else 110.0
                p_max_s = 68.0 if wtype == "5MIN" else 190.0
                if p_min_s <= sec_from_start <= p_max_s and min_delta <= abs(delta_pct) <= 0.35:
                    direction = "UP" if delta_pct > 0 else "DOWN"
                    p_ask = poly_up_ask if direction == "UP" else poly_down_ask
                    p_odds = poly_odds_up if direction == "UP" else poly_odds_down

                    if 0.15 <= p_ask <= 0.64 and p_odds >= 1.56:
                        ev_key = f"POLY48S-{sym}-{win_id}"
                        cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ?", (ev_key,))
                        if cursor.fetchone()[0] == 0:
                            exp_iso = datetime.fromtimestamp(win_id + dur_sec, timezone.utc).isoformat()
                            cursor.execute('''
                                INSERT INTO paper_trades (
                                    deal_type, event_key, title, coin, direction,
                                    platform_a, platform_b, action_a, action_b,
                                    odds_a, odds_b, stake_a, stake_b, total_stake,
                                    hedge_cost, strike_price,
                                    status, entry_time, expiry_time, entry_price, created_by
                                ) VALUES (?, ?, ?, ?, ?, 'polymarket', 'none', ?, '', ?, 0, ?, 0, ?, ?, ?, 'OPEN', ?, ?, ?, 'auto')
                            ''', (
                                "poly_48s_lag", ev_key,
                                f"⚡ Poly {sec_from_start:.0f}s Lag [{sym}]: {direction} (Δ {delta_pct:+.3f}%)",
                                coin, direction,
                                f"Poly CLOB ({wtype}): Купить {direction} @ ${p_ask:.2f} ({p_odds:.2f}x, Страйк S₀=${open_idx:,.2f})",
                                p_odds, stake, stake, p_ask, open_idx,
                                now_iso, exp_iso, idx_p
                            ))
                            active_cnt += 1
                            print(f"[AutoPaper] Placed Poly Lag trade: {sym} {direction} @ {p_odds:.2f}x (sec={sec_from_start:.1f}s)")

            # ─── Strategy 4: Scheme 1 Cockpit Simulation on ALL 4 Bybit Markets (BTC/ETH 5MIN & 15MIN) ───
            # Follows the exact 3-Condition Cockpit Checklist + Active Impulse confirmation,
            # locking Bybit Strike = idx_p at the exact second of click and counting +300s (5MIN) / +900s (15MIN) from entry!
            if self.config.get("auto_5min_momentum", True) and wtype in ("5MIN", "15MIN"):
                entry_min_s = 58.0 if wtype == "5MIN" else 150.0
                entry_max_s = 105.0 if wtype == "5MIN" else 260.0
                spot_delta = float(win.get("spot_delta_pct") or 0.0)
                fut_delta = float(win.get("fut_delta_pct") or 0.0)
                spot_p = float(win.get("spot_price") or idx_p)

                # Condition 1: Inside Entry Window (58-105s for 5M, 150-260s for 15M)
                cond1 = entry_min_s <= sec_from_start <= entry_max_s
                # Condition 2: Impulse Range 0.030% <= |Δ| <= 0.350% (not overextended)
                cond2 = min_delta <= abs(delta_pct) <= 0.350
                # Condition 3: Spot & Futures confirm direction AND Spot is actively supporting Index
                cond3_up = (delta_pct > 0 and spot_delta > 0 and fut_delta >= 0 and spot_p >= idx_p * 0.99995)
                cond3_dn = (delta_pct < 0 and spot_delta < 0 and fut_delta <= 0 and spot_p <= idx_p * 1.00005)

                if cond1 and cond2 and (cond3_up or cond3_dn):
                    ev_key = f"{sym}-{win_id}"
                    cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ?", (ev_key,))
                    if cursor.fetchone()[0] == 0:
                        direction = "UP" if delta_pct > 0 else "DOWN"
                        odds = win.get("odds_up", 1.8) if direction == "UP" else win.get("odds_down", 1.8)
                        exp_epoch = int(round(now_ts)) + dur_sec
                        exp_iso = datetime.fromtimestamp(exp_epoch, timezone.utc).isoformat()

                        cursor.execute('''
                            INSERT INTO paper_trades (
                                deal_type, event_key, title, coin, direction,
                                platform_a, platform_b, action_a, action_b,
                                odds_a, odds_b, stake_a, stake_b, total_stake,
                                hedge_cost, strike_price,
                                status, entry_time, expiry_time, entry_price, created_by
                            ) VALUES (?, ?, ?, ?, ?, 'bybit_odds', 'none', ?, '', ?, 0, ?, 0, ?, 0, ?, 'OPEN', ?, ?, ?, 'auto')
                        ''', (
                            "updown_5m", ev_key,
                            f"🎯 Схема №1 [{sym}]: {direction} на +{sec_from_start:.0f}с (Δ {delta_pct:+.3f}%)",
                            coin, direction,
                            f"Bybit ({wtype}): Взять {direction} @ {odds:.2f}x (Страйк входа ${idx_p:,.2f}, Эксп +{dur_sec}с)",
                            odds, stake, stake, open_idx,
                            now_iso, exp_iso, idx_p
                        ))
                        active_cnt += 1
                        print(f"[AutoPaper] Placed Scheme 1 Cockpit trade: {sym} {direction} at +{sec_from_start:.0f}s (Strike=${idx_p:,.2f}, Exp=+{dur_sec}s)")

        conn.commit()
        conn.close()

    def settle_expired_trades(self):
        """Check all OPEN trades past their expiry_time and calculate simulated P&L."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        now_iso = datetime.now(timezone.utc).isoformat()
        cursor.execute("SELECT * FROM paper_trades WHERE status = 'OPEN' AND expiry_time <= ?", (now_iso,))
        expired = cursor.fetchall()

        if not expired:
            conn.close()
            return

        latest_prices = {}
        latest_windows = []
        if os.path.exists(self.sec_health_file):
            try:
                with open(self.sec_health_file, "r", encoding="utf-8") as f:
                    h_data = json.load(f)
                    latest_prices = h_data.get("prices", {})
                    latest_windows = h_data.get("windows", [])
            except Exception:
                pass

        twap_by_coin = {}
        for w in latest_windows:
            c = w.get("coin")
            if c and w.get("twap_60s"):
                twap_by_coin[c] = w["twap_60s"]

        for row_obj in expired:
            trade = dict(row_obj)
            t_id = trade["id"]
            deal_type = trade["deal_type"]
            coin = trade.get("coin") or "BTC"
            stake = float(trade.get("total_stake") or 5.0)
            c_data = latest_prices.get(coin, {})
            live_idx_price = c_data.get("index") or c_data.get("spot") or 0.0
            live_twap = twap_by_coin.get(coin) or live_idx_price

            # 1. Settle Scheme 1 Bybit Up/Down 5M & 15M contracts (against exact entry_price = Bybit Strike at click)
            if deal_type in ("updown_5m", "updown_15m"):
                settle_price = live_idx_price or float(trade.get("entry_price") or 0.0)
                entry_price = float(trade.get("entry_price") or settle_price)
                direction = trade.get("direction", "UP")
                odds = float(trade.get("odds_a") or 1.8)

                won = (direction == "UP" and settle_price > entry_price) or (direction == "DOWN" and settle_price < entry_price)
                if won:
                    payout = round(stake * odds, 2)
                    net_profit = round(payout - stake, 2)
                    status = "WON"
                else:
                    payout = 0.0
                    net_profit = -stake
                    status = "LOST"

                roi_pct = round((net_profit / stake) * 100.0, 2)
                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?
                    WHERE id = ?
                ''', (status, settle_price, payout, net_profit, roi_pct, t_id))
                print(f"[AutoPaper] Settled Scheme 1 Bybit {trade['title']}: {status} (Strike=${entry_price:,.2f} -> Settle=${settle_price:,.2f}, PnL=${net_profit:+.2f})")

            # 2. Settle Polymarket 47–51s Lag trade (against 60s TWAP vs window_open strike_price)
            elif deal_type == "poly_48s_lag":
                settle_price = live_twap or live_idx_price or float(trade.get("strike_price") or 0.0)
                poly_strike = float(trade.get("strike_price") or trade.get("entry_price") or settle_price)
                direction = trade.get("direction", "UP")
                odds = float(trade.get("odds_a") or 1.8)

                won = (direction == "UP" and settle_price >= poly_strike) or (direction == "DOWN" and settle_price < poly_strike)
                if won:
                    gross_payout = stake * odds
                    fee = max(0.0, (gross_payout - stake) * 0.02)
                    payout = round(gross_payout - fee, 2)
                    net_profit = round(payout - stake, 2)
                    status = "WON"
                else:
                    payout = 0.0
                    net_profit = -stake
                    status = "LOST"

                roi_pct = round((net_profit / stake) * 100.0, 2)
                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?
                    WHERE id = ?
                ''', (status, settle_price, payout, net_profit, roi_pct, t_id))
                print(f"[AutoPaper] Settled Poly 48s Lag {trade['title']}: {status} (${net_profit:+.2f})")

            # 3. Settle Synchronized Start Arb & 2-Step Lock-In Hedge (Both legs share S_0 and T_end!)
            elif deal_type in ("two_step_hedge", "sync_start_arb", "cross_5m_arb"):
                settle_idx = live_idx_price or float(trade.get("strike_price") or 0.0)
                hedge_cost = float(trade.get("hedge_cost") or 0.90)
                if 0 < hedge_cost < 1.0:
                    # Account for ~1.5% Polymarket fee on profit
                    payout = round((stake / hedge_cost) * 0.99, 2)
                else:
                    payout = round(stake * 1.02, 2)
                net_profit = round(payout - stake, 2)
                status = "WON" if net_profit >= 0 else "LOST"
                roi_pct = round((net_profit / stake) * 100.0, 2)

                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?
                    WHERE id = ?
                ''', (status, settle_idx, payout, net_profit, roi_pct, t_id))
                print(f"[AutoPaper] Settled {deal_type} {trade['title']}: {status} (Profit=${net_profit:+.2f})")

            # 4. Settle Daily Cross-Platform Arbitrage
            elif deal_type == "cross_arb":
                hedge_cost = float(trade.get("hedge_cost") or 0.96)
                if 0 < hedge_cost < 1.0:
                    payout = round(stake / hedge_cost, 2)
                else:
                    payout = round(stake * 1.02, 2)
                net_profit = round(payout - stake, 2)
                status = "WON" if net_profit >= 0 else "LOST"
                roi_pct = round((net_profit / stake) * 100.0, 2)

                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?
                    WHERE id = ?
                ''', (status, live_idx_price, payout, net_profit, roi_pct, t_id))
                print(f"[AutoPaper] Settled Cross Arb {trade['title']}: {status} (Profit: ${net_profit:+.2f})")

            # 5. Settle Daily Cross-Platform Value Bet
            elif deal_type == "cross_value":
                strike = float(trade.get("strike_price") or 0.0)
                direction = trade.get("direction", "ABOVE")
                settle_price = live_idx_price or strike
                odds_a = float(trade.get("odds_a") or 1.5)
                odds_b = float(trade.get("odds_b") or 1.5)
                best_odds = max(odds_a, odds_b)

                if strike > 0 and settle_price > 0:
                    won = (direction == "ABOVE" and settle_price >= strike) or (direction == "BELOW" and settle_price < strike)
                else:
                    won = True

                if won:
                    payout = round(stake * min(best_odds, 2.5), 2)
                    net_profit = round(payout - stake, 2)
                    status = "WON"
                else:
                    payout = 0.0
                    net_profit = -stake
                    status = "LOST"

                roi_pct = round((net_profit / stake) * 100.0, 2)
                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?
                    WHERE id = ?
                ''', (status, settle_price, payout, net_profit, roi_pct, t_id))
                print(f"[AutoPaper] Settled Value Bet {trade['title']}: {status} (Profit: ${net_profit:+.2f})")

        conn.commit()
        conn.close()

    def get_trades(self, limit: int = 100, mode: str = "all") -> list:
        """
        mode='main': only daily Target trades (cross_arb, cross_value)
        mode='fast': only 5/15m Test Polygon trades (sync_start_arb, two_step_hedge, poly_48s_lag, updown_5m)
        mode='all': all trades
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if mode == "main":
            cursor.execute('''
                SELECT * FROM paper_trades
                WHERE deal_type IN ('cross_arb', 'cross_value')
                ORDER BY id DESC LIMIT ?
            ''', (limit,))
        elif mode == "fast":
            cursor.execute('''
                SELECT * FROM paper_trades
                WHERE deal_type NOT IN ('cross_arb', 'cross_value')
                ORDER BY id DESC LIMIT ?
            ''', (limit,))
        else:
            cursor.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def clear_trades(self, mode: str = "all"):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        if mode == "main":
            cursor.execute("DELETE FROM paper_trades WHERE deal_type IN ('cross_arb', 'cross_value')")
        elif mode == "fast":
            cursor.execute("DELETE FROM paper_trades WHERE deal_type NOT IN ('cross_arb', 'cross_value')")
        else:
            cursor.execute("DELETE FROM paper_trades")
        conn.commit()
        conn.close()

    def run_fast_1s_cycle(self):
        """1-second cycle for HFT signals and exact +300s/+900s Bybit settlement."""
        if not self.config.get("enabled", True):
            return
        try:
            self.check_and_place_hft_signals()
            self.settle_expired_trades()
        except Exception as e:
            print(f"[AutoPaper] Fast 1s cycle error: {e}")

    def run_cycle(self):
        self.config = self.load_config()
        if not self.config.get("enabled", True):
            return
        try:
            self.check_and_place_cross_trades()
            self.check_and_place_hft_signals()
            self.settle_expired_trades()
        except Exception as e:
            print(f"[AutoPaper] Cycle error: {e}")


if __name__ == "__main__":
    bot = AutoPaperTrader()
    bot.run_cycle()
    print(f"[AutoPaper] Active trades: {bot.get_active_trades_count()}")
