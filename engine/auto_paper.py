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
        self.ticks_db_path = os.path.join(self.base_dir, "ticks.db")
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
            "auto_corridor_2x": True,
            "corridor_max_cost": 1.15,
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
                deal_type TEXT,            -- Main: cross_arb, corridor_2x, cross_value | 5/15m Test: sync_start_arb, two_step_hedge, poly_48s_lag, updown_5m
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
                strike_b REAL DEFAULT 0,
                resolution_note TEXT DEFAULT '',
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
            ("strike_price", "REAL DEFAULT 0"),
            ("strike_b", "REAL DEFAULT 0"),
            ("resolution_note", "TEXT DEFAULT ''")
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

    @staticmethod
    def parse_both_strikes_from_title(title: str) -> tuple:
        """Extract (strike_bybit, strike_poly) from title like 'BTC $83,500 / Poly $84,000 [ABOVE]'."""
        matches = re.findall(r'\$([0-9,]+(?:\.[0-9]+)?)', title or "")
        vals = []
        for m in matches:
            try:
                vals.append(float(m.replace(",", "")))
            except Exception:
                pass
        if len(vals) >= 2:
            return vals[0], vals[1]
        elif len(vals) == 1:
            return vals[0], vals[0]
        return 0.0, 0.0

    def check_and_place_cross_trades(self):
        """
        Scan latest daily target spreads for:
          1. `corridor_2x`: 🎯 Бонуска «Коридор 2x» (when strikes form a winning corridor [S_low, S_high] and hedge_cost <= corridor_max_cost)
          2. `cross_arb`: 🔥 Чистая вилка / 2-плечевой хедж в плюс (hedge_cost < 1.0 and no strike gap risk)
          3. `cross_value`: 💎 Value 1-плечо (ONLY when strikes match 100% 1-to-1!)
        Strictly blocks any deal with 'Зазор страйков' (strike gap risk where both legs can lose).
        """
        if not self.config.get("auto_cross_arbs", True) and not self.config.get("auto_corridor_2x", True):
            return

        active_cnt = self.get_active_trades_count()
        max_trades = self.config.get("max_active_trades", 60)
        if active_cnt >= max_trades:
            return

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        min_spread = self.config.get("min_spread_pct", 3.5) / 100.0
        corridor_max_cost = float(self.config.get("corridor_max_cost", 1.15))
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        cursor.execute('''
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY detected_at DESC) as rn
                FROM spreads
                WHERE platform_a != platform_b
                  AND event_key NOT LIKE '%5MIN%'
                  AND event_key NOT LIKE '%15MIN%'
                  AND odds_a > 1.002 AND odds_b > 1.002
                  AND (is_arb = 1 OR hedge_margin > 0 OR time_warning LIKE '%Коридор 2x%' OR spread_after_fees >= ?)
                  AND detected_at >= datetime('now', '-15 minutes')
            ) WHERE rn = 1
            ORDER BY
                CASE WHEN time_warning LIKE '%Коридор 2x%' THEN 1 ELSE 0 END DESC,
                is_arb DESC,
                hedge_margin DESC,
                spread_after_fees DESC
            LIMIT 12
        ''', (min_spread,))

        rows = cursor.fetchall()

        for row_obj in rows:
            if active_cnt >= max_trades:
                break

            r = dict(row_obj)
            event_key = r["event_key"]
            title = r.get("title") or event_key
            time_warn = r.get("time_warning") or ""

            # NEVER enter a deal with Strike Gap Risk (where both legs can lose if price lands in the gap)
            if "Зазор страйков" in time_warn:
                continue

            exp_time = self.parse_expiry_from_title(title, event_key)
            if exp_time <= now_iso:
                continue

            cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ? OR title = ?", (event_key, title))
            if cursor.fetchone()[0] > 0:
                continue

            bank = float(self.config.get("default_stake", 5.0))
            hedge_cost = float(r.get("hedge_cost") or 0.0)
            is_arb = int(r.get("is_arb") or 0)
            spread_val = float(r.get("spread_after_fees") or 0.0)

            strike_a, strike_b = self.parse_both_strikes_from_title(title)
            has_corridor = ("Коридор 2x" in time_warn) or (
                strike_a > 0 and strike_b > 0 and abs(strike_a - strike_b) > 1e-6 and (
                    ("ABOVE" in event_key and strike_a < strike_b) or
                    ("BELOW" in event_key and strike_a > strike_b)
                )
            )
            exact_strike = (strike_a > 0 and abs(strike_a - strike_b) <= 1e-6 and ("/ Poly" not in title) and (not has_corridor))

            # Determine deal_type according to strict methodological rules:
            if has_corridor:
                if not self.config.get("auto_corridor_2x", True):
                    continue
                if hedge_cost <= 0 or hedge_cost > corridor_max_cost:
                    continue
                deal_type = "corridor_2x"
            elif is_arb == 1 or (0 < hedge_cost < 1.0):
                if not self.config.get("auto_cross_arbs", True):
                    continue
                deal_type = "cross_arb"
            elif exact_strike and spread_val >= max(min_spread, 0.08):
                # Value 1-leg is ONLY allowed when Bybit and Polymarket strikes match 1-to-1!
                if not self.config.get("auto_cross_arbs", True):
                    continue
                deal_type = "cross_value"
            else:
                continue

            odds_a_val = float(r.get("odds_a") or 1.8)
            cost_a_val = (1.0 / odds_a_val) if odds_a_val > 0 else 0.5
            if hedge_cost > cost_a_val:
                cost_b_opp = hedge_cost - cost_a_val
                odds_b_hedge = round(1.0 / cost_b_opp, 4)
                split_a = cost_a_val / hedge_cost
            else:
                odds_b_hedge = float(r.get("odds_b") or 1.8)
                split_a = r.get("stake_a") if (r.get("stake_a") and r.get("stake_a") > 0) else 0.5

            stake_a = round(bank * split_a, 2)
            stake_b = round(bank - stake_a, 2)

            coin = "BTC"
            for c in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]:
                if c in title:
                    coin = c
                    break

            direction = "ABOVE" if "ABOVE" in event_key else "BELOW"
            s_low, s_high = min(strike_a, strike_b), max(strike_a, strike_b)
            res_note = f"🎯 Коридор 2x: ${s_low:,.0f}–${s_high:,.0f}" if deal_type == "corridor_2x" else (
                "Чистая вилка 2п" if deal_type == "cross_arb" else "Value 1-плечо (страйки 1-в-1)"
            )

            cursor.execute('''
                INSERT INTO paper_trades (
                    deal_type, event_key, title, coin, direction,
                    platform_a, platform_b, action_a, action_b,
                    odds_a, odds_b, stake_a, stake_b, total_stake,
                    hedge_cost, strike_price, strike_b, resolution_note,
                    status, entry_time, expiry_time, entry_price, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 'auto')
            ''', (
                deal_type, event_key, title, coin, direction,
                r.get("platform_a", ""), r.get("platform_b", ""),
                r.get("action_a", ""), r.get("action_b", ""),
                odds_a_val, odds_b_hedge if deal_type in ("corridor_2x", "cross_arb") else float(r.get("odds_b") or 0.0),
                stake_a, stake_b, bank,
                hedge_cost, strike_a, strike_b, res_note,
                now_iso, exp_time, r.get("prob_a", 0.0)
            ))
            active_cnt += 1
            print(f"[AutoPaper] Placed {deal_type} demo trade: {title} (${bank}, Cost={hedge_cost:.3f}, Exp: {exp_time[:16]})")

        conn.commit()
        conn.close()

    def check_and_place_hft_signals(self):
        """
        Evaluate live 1s HFT windows from sec_logger for the 5/15m Test Polygon:
          1. `sync_start_arb`: Simultaneous entry in first 0..20s when timers & strikes have zero gap.
          2. `two_step_hedge`: Cross-Platform (Bybit + Polymarket) No-Gap 2-Step Hedge:
             - Step 1 (0.5..12s): Enter Leg 1 on Bybit Odds ONLY when Bybit strike forms a Corridor (zero strike gap!) against Polymarket S_0.
             - Step 2 (15..dur-20s): Lock in Leg 2 on Polymarket when opposite outcome drops so (1/bb_odds + ask2) <= 0.92.
             - Spins real Bybit Odds volume for the $12,000 Promo Leaderboard with zero look-ahead bias!
          3. `poly_48s_lag`: Polymarket 42–68s window opening TWAP lag test.
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
        prices_map = health.get("prices", {})
        now_ts = time.time()
        btc_upd = float(prices_map.get("BTC", {}).get("updated_at") or 0.0)
        if btc_upd > 0 and (now_ts - btc_upd) > 30.0:
            # Internet / live feed is stale — do not place new HFT trades
            return
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

            # ─── 1. Synchronized Start Arb (0..20s, strictly NO strike gap!) ───
            if 0.0 <= sec_from_start <= 20.0 and poly_accepting and abs(delta_pct) <= 0.025:
                for bb_dir, p_dir, p_ask, p_odds in [
                    ("UP", "DOWN", poly_down_ask, poly_odds_down),
                    ("DOWN", "UP", poly_up_ask, poly_odds_up)
                ]:
                    # Block if Bybit strike at click (idx_p) creates a Strike Gap against Polymarket S_0 (open_idx)
                    has_gap = (bb_dir == "UP" and idx_p > open_idx * 1.00005) or (bb_dir == "DOWN" and idx_p < open_idx * 0.99995)
                    if has_gap:
                        continue
                    if 0.15 <= p_ask <= 0.41:
                        h_cost = round((1.0 / bb_odds) + p_ask, 4)
                        if h_cost <= 0.965:
                            ev_key = f"SYNCSTART-{sym}-{win_id}"
                            cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ?", (ev_key,))
                            if cursor.fetchone()[0] == 0:
                                stake_a = round(stake * ((1.0 / bb_odds) / h_cost), 2)
                                stake_b = round(stake - stake_a, 2)
                                exp_epoch = int(round(now_ts)) + dur_sec
                                exp_iso = datetime.fromtimestamp(exp_epoch, timezone.utc).isoformat()
                                cursor.execute('''
                                    INSERT INTO paper_trades (
                                        deal_type, event_key, title, coin, direction,
                                        platform_a, platform_b, action_a, action_b,
                                        odds_a, odds_b, stake_a, stake_b, total_stake,
                                        hedge_cost, strike_price, strike_b,
                                        status, entry_time, expiry_time, entry_price, created_by
                                    ) VALUES (?, ?, ?, ?, ?, 'bybit_odds', 'polymarket', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 'auto')
                                ''', (
                                    "sync_start_arb", ev_key,
                                    f"🔒 Синхр. Старт ({sec_from_start:.0f}с): {coin} {wtype} (Cost: {h_cost:.2f})",
                                    coin, f"SYNC:{bb_dir}+{p_dir}",
                                    f"Bybit (+{sec_from_start:.0f}с): {bb_dir} @ {bb_odds:.2f}x (Страйк ${idx_p:,.1f})",
                                    f"Poly (0с): {p_dir} @ ${p_ask:.2f} ({p_odds:.2f}x, Страйк ${open_idx:,.1f})",
                                    bb_odds, p_odds, stake_a, stake_b, stake,
                                    h_cost, idx_p, open_idx,
                                    now_iso, exp_iso, idx_p
                                ))
                                active_cnt += 1
                                print(f"[AutoPaper] Placed Sync Start Arb: {coin} {wtype} (Cost={h_cost:.3f})")
                            break

            # ─── 2. Honest Cross-Platform Two-Step Hedge: Bybit + Polymarket (No-Gap Corridor & Zero Look-Ahead!) ───
            # Step 1 (0.5..12s): Enter Leg 1 in real time on Bybit Odds (spinning Bybit Promo volume!) ONLY when
            #                    idx_p <= open_idx (for UP) or idx_p >= open_idx (for DOWN) — zero Strike Gap!
            # Step 2 (15..dur-20s): If opposite Polymarket outcome drops so (1/bb_odds + ask2) <= 0.92, lock in Step 2!
            # If Step 2 never triggers, Step 1 remains unhedged on Bybit and settles honestly (WIN or LOSS) at +300s/+900s!
            if self.config.get("auto_two_step_hedge", True) and poly_accepting:
                ev_key_hedge = f"LOCKHEDGE-{sym}-{win_id}"
                if 0.5 <= sec_from_start <= 12.0:
                    chosen_dir1 = None
                    if poly_up_ask >= 0.52 and idx_p <= open_idx * 1.00002:
                        chosen_dir1 = "UP"
                    elif poly_down_ask >= 0.52 and idx_p >= open_idx * 0.99998:
                        chosen_dir1 = "DOWN"

                    if chosen_dir1:
                        odds1 = win.get("odds_up", 1.8) if chosen_dir1 == "UP" else win.get("odds_down", 1.8)
                        cost1 = round(1.0 / odds1, 4) if odds1 > 0 else 0.5556
                        cursor.execute("SELECT COUNT(*) FROM paper_trades WHERE event_key = ?", (ev_key_hedge,))
                        if cursor.fetchone()[0] == 0:
                            exp_epoch = int(round(now_ts)) + dur_sec
                            exp_iso = datetime.fromtimestamp(exp_epoch, timezone.utc).isoformat()
                            cursor.execute('''
                                INSERT INTO paper_trades (
                                    deal_type, event_key, title, coin, direction,
                                    platform_a, platform_b, action_a, action_b,
                                    odds_a, odds_b, stake_a, stake_b, total_stake,
                                    hedge_cost, strike_price, strike_b, resolution_note,
                                    status, entry_time, expiry_time, entry_price, created_by
                                ) VALUES (?, ?, ?, ?, ?, 'bybit_odds', 'polymarket', ?, ?, ?, 0, ?, 0, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 'auto')
                            ''', (
                                "two_step_hedge", ev_key_hedge,
                                f"🛡️ 2-Шаговый Хедж (Шаг 1/2): {coin} {wtype} (Bybit {chosen_dir1} @ {odds1:.2f}x)",
                                coin, f"STEP1:{chosen_dir1}",
                                f"Шаг 1 (+{sec_from_start:.0f}с): Bybit {chosen_dir1} @ {odds1:.2f}x (Страйк ${idx_p:,.1f})",
                                "⏳ Ожидание Шага 2 на Polymarket (замок <= 0.92)...",
                                odds1, stake, stake,
                                cost1, idx_p, open_idx, "Шаг 1 открыт на Bybit (без зазора страйков, ожидание замка Poly)",
                                now_iso, exp_iso, idx_p
                            ))
                            active_cnt += 1
                            print(f"[AutoPaper] Opened Step 1 of Bybit+Poly 2-Step Hedge: {coin} {wtype} (Bybit {chosen_dir1} @ {odds1:.2f}x, Strike=${idx_p:,.1f}, S0=${open_idx:,.1f})")

                elif 15.0 <= sec_from_start <= (dur_sec - 20):
                    cursor.execute(
                        "SELECT id, direction, odds_a, hedge_cost, strike_price, strike_b FROM paper_trades WHERE event_key = ? AND status = 'OPEN'",
                        (ev_key_hedge,)
                    )
                    step1_row = cursor.fetchone()
                    if step1_row and str(step1_row[1]).startswith("STEP1:"):
                        t_id_h = step1_row[0]
                        dir1 = str(step1_row[1]).split(":")[1]
                        odds1 = float(step1_row[2] or 1.8)
                        cost1 = float(step1_row[3] or (1.0 / odds1))
                        s_a = float(step1_row[4] or idx_p)
                        s0 = float(step1_row[5] or open_idx)
                        dir2 = "DOWN" if dir1 == "UP" else "UP"
                        ask2 = poly_down_ask if dir2 == "DOWN" else poly_up_ask
                        odds2 = poly_odds_down if dir2 == "DOWN" else poly_odds_up
                        total_h_cost = round(cost1 + ask2, 4)

                        if 0.08 <= ask2 <= 0.42 and total_h_cost <= 0.92:
                            stake_a = round(stake * (cost1 / total_h_cost), 2)
                            stake_b = round(stake - stake_a, 2)
                            cursor.execute('''
                                UPDATE paper_trades
                                SET title = ?, direction = ?, action_b = ?, odds_b = ?,
                                    stake_a = ?, stake_b = ?, hedge_cost = ?, resolution_note = ?
                                WHERE id = ?
                            ''', (
                                f"🛡️ 2-Шаговый Хедж (Замок 🔒): {coin} {wtype} (Cost: {total_h_cost:.2f})",
                                f"LOCK:{dir1}+{dir2}",
                                f"Шаг 2 (+{sec_from_start:.0f}с): Poly {dir2} @ ${ask2:.2f} ({odds2:.2f}x, S₀=${s0:,.1f})",
                                odds2, stake_a, stake_b, total_h_cost,
                                f"🔒 Замок Bybit+Poly закрыт на +{sec_from_start:.0f}с (Cost={total_h_cost:.2f})",
                                t_id_h
                            ))
                            print(f"[AutoPaper] Locked Step 2 of Bybit+Poly 2-Step Hedge: {coin} {wtype} ({dir1}+{dir2}, Cost={total_h_cost:.3f})")

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

    def fetch_historical_index_price(self, coin: str, expiry_iso: str) -> float:
        """
        Fetch exact historical Bybit Index Price at `expiry_iso` from V5 `/v5/market/index-price-kline`.
        Used when settling daily 08:00 UTC contracts or recovering after an internet outage.
        """
        try:
            import urllib.request
            dt = datetime.fromisoformat(str(expiry_iso).replace("Z", "+00:00"))
            ts_ms = int(dt.timestamp() * 1000)
            sym = f"{coin}USDT"
            url = (
                f"https://api.bybit.com/v5/market/index-price-kline"
                f"?category=linear&symbol={sym}&interval=1&start={ts_ms - 60000}&end={ts_ms + 60000}&limit=5"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            klines = data.get("result", {}).get("list", [])
            best_p = 0.0
            best_diff = 1e18
            for k in klines:
                k_ts = int(k[0])
                diff = abs(k_ts - ts_ms)
                if diff < best_diff:
                    best_diff = diff
                    best_p = float(k[1])
            return best_p
        except Exception:
            return 0.0

    def settle_expired_trades(self):
        """Check all OPEN trades past their expiry_time and calculate simulated P&L."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        now_ts = now_dt.timestamp()
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

        hist_cache = {}

        for row_obj in expired:
            trade = dict(row_obj)
            t_id = trade["id"]
            deal_type = trade["deal_type"]
            coin = trade.get("coin") or "BTC"
            stake = float(trade.get("total_stake") or 5.0)
            exp_str = str(trade.get("expiry_time") or "")
            c_data = latest_prices.get(coin, {})
            updated_at = float(c_data.get("updated_at") or 0.0)
            is_live_fresh = (updated_at == 0.0) or ((now_ts - updated_at) <= 45.0)

            delay_sec = 0.0
            try:
                exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                delay_sec = (now_dt - exp_dt).total_seconds()
            except Exception:
                pass

            live_idx_price = (c_data.get("index") or c_data.get("spot") or 0.0) if is_live_fresh else 0.0

            # If trade expired > 45s ago (e.g. during internet outage) or live feed is stale, query official Bybit historical kline at expiry_time
            if delay_sec > 45.0 or not live_idx_price:
                cache_key = f"{coin}_{exp_str[:16]}"
                if cache_key not in hist_cache:
                    hist_cache[cache_key] = self.fetch_historical_index_price(coin, exp_str)
                hist_price = hist_cache.get(cache_key) or 0.0
                if hist_price > 0:
                    live_idx_price = hist_price

            if not live_idx_price and is_live_fresh and os.path.exists(self.ticks_db_path):
                try:
                    t_conn = sqlite3.connect(self.ticks_db_path)
                    t_cur = t_conn.cursor()
                    t_cur.execute("SELECT index_price FROM sec_ticks WHERE coin = ? ORDER BY id DESC LIMIT 1", (coin,))
                    t_row = t_cur.fetchone()
                    t_conn.close()
                    if t_row and t_row[0]:
                        live_idx_price = float(t_row[0])
                except Exception:
                    pass

            # If internet is currently offline (no fresh live price AND historical API unreachable), wait until connection returns!
            if not live_idx_price and updated_at > 0 and not is_live_fresh:
                continue

            live_twap = (twap_by_coin.get(coin) if (is_live_fresh and delay_sec <= 45.0) else None) or live_idx_price

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

            # 3. Honest Settlement of Synchronized Start Arb & 2-Step Hedge (per-leg evaluation with exact Bybit vs Poly timer check!)
            elif deal_type in ("two_step_hedge", "sync_start_arb", "cross_5m_arb"):
                settle_idx_bybit = live_idx_price or float(trade.get("strike_price") or 0.0)
                settle_idx_poly = settle_idx_bybit

                # Look up exact candle-end price for Polymarket leg (win_id + dur_sec) if Bybit expired 1..12s after candle close
                ev_k = str(trade.get("event_key") or "")
                m_win = re.search(r'-(\d{10})$', ev_k)
                if m_win and os.path.exists(self.ticks_db_path):
                    try:
                        win_id_int = int(m_win.group(1))
                        sym_str = f"{coin}USDT-{'15MIN' if '15MIN' in ev_k else '5MIN'}"
                        t_conn = sqlite3.connect(self.ticks_db_path)
                        t_cur = t_conn.cursor()
                        t_cur.execute(
                            "SELECT index_price FROM sec_ticks WHERE symbol = ? AND window_id = ? ORDER BY id DESC LIMIT 1",
                            (sym_str, win_id_int)
                        )
                        t_row = t_cur.fetchone()
                        t_conn.close()
                        if t_row and t_row[0]:
                            settle_idx_poly = float(t_row[0])
                    except Exception:
                        pass

                dir_str = str(trade.get("direction") or "")
                s_a = float(trade.get("strike_price") or trade.get("entry_price") or settle_idx_bybit)
                s_b = float(trade.get("strike_b") or s_a)
                odds_a = float(trade.get("odds_a") or 1.8)
                odds_b = float(trade.get("odds_b") or 0.0)
                stake_a = float(trade.get("stake_a") or stake)
                stake_b = float(trade.get("stake_b") or 0.0)
                plat_a = str(trade.get("platform_a") or "bybit_odds")

                if dir_str.startswith("STEP1:"):
                    # Step 2 never locked! Settle as unhedged 1-leg trade on platform_a (Bybit or Poly)
                    d1 = dir_str.split(":")[1]
                    if plat_a == "bybit_odds":
                        won1 = (d1 == "UP" and settle_idx_bybit > s_a) or (d1 == "DOWN" and settle_idx_bybit < s_a)
                    else:
                        won1 = (d1 == "UP" and settle_idx_poly >= s_a) or (d1 == "DOWN" and settle_idx_poly < s_a)
                    if won1:
                        gross = stake * odds_a
                        fee = max(0.0, (gross - stake) * 0.02) if plat_a == "polymarket" else 0.0
                        payout = round(gross - fee, 2)
                        net_profit = round(payout - stake, 2)
                        status = "WON"
                        res_note = f"Шаг 2 не сработал, но Шаг 1 ({d1}) выиграл соло"
                    else:
                        payout = 0.0
                        net_profit = -stake
                        status = "LOST"
                        res_note = f"✕ Замок Шага 2 не закрылся, Шаг 1 ({d1}) сгорел"
                else:
                    # 2-leg locked trade (LOCK:UP+DOWN or SYNC:UP+DOWN)
                    clean_dirs = dir_str.replace("LOCK:", "").replace("SYNC:", "").split("+")
                    d_a = clean_dirs[0] if len(clean_dirs) >= 1 else "UP"
                    d_b = clean_dirs[1] if len(clean_dirs) >= 2 else ("DOWN" if d_a == "UP" else "UP")

                    if plat_a == "bybit_odds":
                        won_a = (d_a == "UP" and settle_idx_bybit > s_a) or (d_a == "DOWN" and settle_idx_bybit < s_a)
                    else:
                        won_a = (d_a == "UP" and settle_idx_poly >= s_a) or (d_a == "DOWN" and settle_idx_poly < s_a)
                    won_b = (d_b == "UP" and settle_idx_poly >= s_b) or (d_b == "DOWN" and settle_idx_poly < s_b)

                    payout = 0.0
                    if won_a:
                        gross_a = stake_a * odds_a
                        fee_a = max(0.0, (gross_a - stake_a) * 0.02) if plat_a == "polymarket" else 0.0
                        payout += (gross_a - fee_a)
                    if won_b and stake_b > 0 and odds_b > 0:
                        gross_b = stake_b * odds_b
                        fee_b = max(0.0, (gross_b - stake_b) * 0.02)
                        payout += (gross_b - fee_b)

                    payout = round(payout, 2)
                    net_profit = round(payout - stake, 2)
                    status = "WON" if net_profit >= 0 else "LOST"
                    if won_a and won_b:
                        res_note = "🎯 2X ДЖЕКПОТ! Оба плеча выиграли"
                    elif won_a or won_b:
                        res_note = "🔒 Замок сработал (1 плечо из 2)"
                    else:
                        res_note = "✕ Разница таймеров/страйков: оба плеча проиграли!"

                roi_pct = round((net_profit / stake) * 100.0, 2)
                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?, resolution_note = ?
                    WHERE id = ?
                ''', (status, settle_idx_bybit, payout, net_profit, roi_pct, res_note, t_id))
                print(f"[AutoPaper] Settled {deal_type} {trade['title']}: {status} ({res_note}, Profit=${net_profit:+.2f})")

            # 4. Settle Daily Cross-Platform Arbitrage (with optional 2x Corridor Jackpot!)
            elif deal_type == "cross_arb":
                hedge_cost = float(trade.get("hedge_cost") or 0.96)
                s_a = float(trade.get("strike_price") or 0.0)
                s_b = float(trade.get("strike_b") or 0.0)
                if not s_a or not s_b:
                    p_sa, p_sb = self.parse_both_strikes_from_title(trade.get("title", ""))
                    s_a = s_a or p_sa
                    s_b = s_b or p_sb
                s_low = min(s_a, s_b) if (s_a > 0 and s_b > 0) else 0.0
                s_high = max(s_a, s_b) if (s_a > 0 and s_b > 0) else 0.0
                settle_price = live_idx_price or s_a

                if s_low > 0 and s_low < s_high and settle_price > 0 and (s_low <= settle_price <= s_high):
                    payout = round(2.0 * (stake / hedge_cost), 2) if hedge_cost > 0 else round(stake * 2.0, 2)
                    res_note = f"🎯 2X ДЖЕКПОТ! В коридоре ${s_low:,.0f}–${s_high:,.0f}"
                else:
                    payout = round(stake / hedge_cost, 2) if 0 < hedge_cost < 1.0 else round(stake * 1.02, 2)
                    res_note = "Гарант. вилка (1 плечо)"

                net_profit = round(payout - stake, 2)
                status = "WON" if net_profit >= 0 else "LOST"
                roi_pct = round((net_profit / stake) * 100.0, 2)

                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?, resolution_note = ?
                    WHERE id = ?
                ''', (status, settle_price, payout, net_profit, roi_pct, res_note, t_id))
                print(f"[AutoPaper] Settled Cross Arb {trade['title']}: {status} ({res_note}, Profit: ${net_profit:+.2f})")

            # 4b. Settle 2x Corridor Deal (🎯 Коридор 2x выигрыша)
            elif deal_type == "corridor_2x":
                hedge_cost = float(trade.get("hedge_cost") or 1.05)
                if hedge_cost <= 0:
                    hedge_cost = 1.05
                s_a = float(trade.get("strike_price") or 0.0)
                s_b = float(trade.get("strike_b") or 0.0)
                if not s_a or not s_b:
                    p_sa, p_sb = self.parse_both_strikes_from_title(trade.get("title", ""))
                    s_a = s_a or p_sa
                    s_b = s_b or p_sb
                s_low = min(s_a, s_b)
                s_high = max(s_a, s_b)
                settle_price = live_idx_price or s_a

                # Check if settle_price landed INSIDE the 2x Corridor [s_low, s_high]
                in_corridor = (s_low > 0 and s_high > s_low and settle_price > 0 and (s_low <= settle_price <= s_high))
                if in_corridor:
                    # Both legs win! Payout = 2 * (stake / hedge_cost)
                    payout = round(2.0 * (stake / hedge_cost), 2)
                    net_profit = round(payout - stake, 2)
                    status = "WON"
                    res_note = f"🎯 2X ДЖЕКПОТ! Финиш ${settle_price:,.0f} в коридоре ${s_low:,.0f}–${s_high:,.0f}"
                else:
                    # Outside corridor: 1 of 2 legs always wins! Payout = 1 * (stake / hedge_cost)
                    payout = round(stake / hedge_cost, 2)
                    net_profit = round(payout - stake, 2)
                    status = "WON" if net_profit >= 0 else "LOST"
                    res_note = f"1 плечо из 2 (финиш ${settle_price:,.0f} вне коридора ${s_low:,.0f}–${s_high:,.0f})"

                roi_pct = round((net_profit / stake) * 100.0, 2)
                cursor.execute('''
                    UPDATE paper_trades
                    SET status = ?, settle_price = ?, payout = ?, net_profit = ?, roi_pct = ?, resolution_note = ?
                    WHERE id = ?
                ''', (status, settle_price, payout, net_profit, roi_pct, res_note, t_id))
                print(f"[AutoPaper] Settled Corridor 2x {trade['title']}: {status} ({res_note}, PnL=${net_profit:+.2f})")

            # 5. Settle Daily Cross-Platform Value Bet (Exact strike 1-leg only)
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
        mode='main': only daily Target trades (cross_arb, corridor_2x, cross_value)
        mode='fast': only 5/15m Test Polygon trades (sync_start_arb, two_step_hedge, poly_48s_lag, updown_5m)
        mode='all': all trades
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if mode == "main":
            cursor.execute('''
                SELECT * FROM paper_trades
                WHERE deal_type IN ('cross_arb', 'corridor_2x', 'cross_value')
                ORDER BY id DESC LIMIT ?
            ''', (limit,))
        elif mode == "fast":
            cursor.execute('''
                SELECT * FROM paper_trades
                WHERE deal_type NOT IN ('cross_arb', 'corridor_2x', 'cross_value')
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
            cursor.execute("DELETE FROM paper_trades WHERE deal_type IN ('cross_arb', 'corridor_2x', 'cross_value')")
        elif mode == "fast":
            cursor.execute("DELETE FROM paper_trades WHERE deal_type NOT IN ('cross_arb', 'corridor_2x', 'cross_value')")
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
