import sqlite3
import os
from datetime import datetime
from typing import List, Dict

class Database:
    def __init__(self, db_path: str = "spreads.db"):
        self.db_path = os.path.join(os.path.dirname(__file__), "..", db_path)
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Enable WAL mode for high concurrency and resilience during long runs
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS spreads (
                id INTEGER PRIMARY KEY,
                event_key TEXT,
                platform_a TEXT,
                prob_a REAL,
                odds_a REAL,
                url_a TEXT,
                platform_b TEXT,
                prob_b REAL,
                odds_b REAL,
                url_b TEXT,
                spread_after_fees REAL,
                detected_at TEXT,
                title TEXT,
                prob_sum REAL,
                implied_cost REAL DEFAULT 0,
                overround REAL,
                is_arb INTEGER DEFAULT 0,
                stake_a REAL DEFAULT 0,
                stake_b REAL DEFAULT 0,
                time_diff_hours REAL DEFAULT 0,
                time_warning TEXT DEFAULT '',
                action_summary TEXT DEFAULT '',
                action_a TEXT DEFAULT '',
                action_b TEXT DEFAULT '',
                hedge_cost REAL DEFAULT 0,
                hedge_margin REAL DEFAULT 0,
                is_arb_time_risky INTEGER DEFAULT 0,
                opp_price_is_synthetic INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY,
                market_id TEXT,
                platform TEXT,
                title TEXT,
                prob REAL,
                odds REAL,
                timestamp TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS arbs_archive (
                id INTEGER PRIMARY KEY,
                event_key TEXT,
                platform_a TEXT,
                prob_a REAL,
                odds_a REAL,
                url_a TEXT,
                platform_b TEXT,
                prob_b REAL,
                odds_b REAL,
                url_b TEXT,
                spread_after_fees REAL,
                detected_at TEXT,
                title TEXT,
                hedge_cost REAL DEFAULT 0,
                hedge_margin REAL DEFAULT 0,
                is_arb INTEGER DEFAULT 0,
                is_arb_time_risky INTEGER DEFAULT 0,
                action_summary TEXT DEFAULT '',
                action_a TEXT DEFAULT '',
                action_b TEXT DEFAULT '',
                time_warning TEXT DEFAULT '',
                volume_a REAL DEFAULT 0,
                volume_b REAL DEFAULT 0,
                liquidity_b REAL DEFAULT 0,
                archived_at TEXT,
                UNIQUE(event_key, detected_at)
            )
        ''')

        # Migrations for spreads table
        for col_name, col_type in [
            ("odds_a", "REAL DEFAULT 0"),
            ("odds_b", "REAL DEFAULT 0"),
            ("url_a", "TEXT DEFAULT ''"),
            ("url_b", "TEXT DEFAULT ''"),
            ("title", "TEXT DEFAULT ''"),
            ("prob_sum", "REAL DEFAULT 0"),
            ("implied_cost", "REAL DEFAULT 0"),
            ("overround", "REAL DEFAULT 0"),
            ("is_arb", "INTEGER DEFAULT 0"),
            ("stake_a", "REAL DEFAULT 0"),
            ("stake_b", "REAL DEFAULT 0"),
            ("time_diff_hours", "REAL DEFAULT 0"),
            ("time_warning", "TEXT DEFAULT ''"),
            ("action_summary", "TEXT DEFAULT ''"),
            ("action_a", "TEXT DEFAULT ''"),
            ("action_b", "TEXT DEFAULT ''"),
            ("hedge_cost", "REAL DEFAULT 0"),
            ("hedge_margin", "REAL DEFAULT 0"),
            ("is_arb_time_risky", "INTEGER DEFAULT 0"),
            ("opp_price_is_synthetic", "INTEGER DEFAULT 0"),
            ("volume_a", "REAL DEFAULT 0"),
            ("volume_b", "REAL DEFAULT 0"),
            ("liquidity_b", "REAL DEFAULT 0")
        ]:
            try:
                cursor.execute(f"ALTER TABLE spreads ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        
        # Indexes for fast querying as the database grows
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_spreads_detected ON spreads(detected_at DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_spreads_spread ON spreads(spread_after_fees DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_ts ON price_history(timestamp DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_mkt ON price_history(market_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_arbs_archive_det ON arbs_archive(detected_at DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_arbs_archive_margin ON arbs_archive(hedge_margin DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_arbs_archive_spread ON arbs_archive(spread_after_fees DESC);")

        conn.commit()
        conn.close()

    def archive_valuable_arbs(self, min_spread: float = 0.05):
        """Archive any confirmed surebets or high spreads before old records get deleted."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat() + "Z"
            cursor.execute('''
                INSERT OR IGNORE INTO arbs_archive (
                    event_key, platform_a, prob_a, odds_a, url_a,
                    platform_b, prob_b, odds_b, url_b,
                    spread_after_fees, detected_at, title,
                    hedge_cost, hedge_margin, is_arb, is_arb_time_risky,
                    action_summary, action_a, action_b, time_warning,
                    volume_a, volume_b, liquidity_b, archived_at
                )
                SELECT 
                    event_key, platform_a, prob_a, odds_a, url_a,
                    platform_b, prob_b, odds_b, url_b,
                    spread_after_fees, detected_at, title,
                    hedge_cost, hedge_margin, is_arb, is_arb_time_risky,
                    action_summary, action_a, action_b, time_warning,
                    COALESCE(volume_a, 0), COALESCE(volume_b, 0), COALESCE(liquidity_b, 0), ?
                FROM spreads
                WHERE (is_arb = 1 OR hedge_margin > 0 OR spread_after_fees >= ?)
                  AND event_key NOT LIKE '%5MIN%'
                  AND event_key NOT LIKE '%15MIN%'
            ''', (now, min_spread))
            archived_cnt = cursor.rowcount
            conn.commit()
            conn.close()
            return archived_cnt
        except Exception as e:
            print(f"[DB] Archive error: {e}")
            return 0

    def cleanup_old_records(self, hours_spreads: int = 24, hours_history: int = 12):
        """Archive valuable arbs, then prune older data to prevent database bloat."""
        try:
            # 1. Archive valuable opportunities before pruning
            self.archive_valuable_arbs()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM spreads WHERE detected_at < datetime('now', ?)", (f"-{hours_spreads} hours",))
            deleted_spreads = cursor.rowcount
            cursor.execute("DELETE FROM price_history WHERE timestamp < datetime('now', ?)", (f"-{hours_history} hours",))
            deleted_history = cursor.rowcount
            conn.commit()
            conn.close()
            print(f"[DB] Cleaned: -{deleted_spreads} old spreads (>{hours_spreads}h), -{deleted_history} ticks (>{hours_history}h)")
        except Exception as e:
            print(f"[DB] Cleanup error: {e}")

    def vacuum_db(self) -> dict:
        """Checkpoint SQLite WAL and execute VACUUM to reclaim disk space."""
        try:
            size_before = os.path.getsize(self.db_path)
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            cursor.execute("VACUUM;")
            cursor.execute("PRAGMA optimize;")
            conn.commit()
            conn.close()
            size_after = os.path.getsize(self.db_path)
            reclaimed_mb = round((size_before - size_after) / (1024 * 1024), 2)
            return {
                "status": "success",
                "size_before_mb": round(size_before / (1024 * 1024), 2),
                "size_after_mb": round(size_after / (1024 * 1024), 2),
                "reclaimed_mb": reclaimed_mb
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_db_stats(self) -> dict:
        """Get database health, row counts, and disk usage."""
        try:
            size_bytes = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            wal_path = self.db_path + "-wal"
            wal_bytes = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM spreads")
            spreads_cnt = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM price_history")
            history_cnt = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM arbs_archive")
            archive_cnt = cursor.fetchone()[0]
            conn.close()
            
            return {
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "wal_mb": round(wal_bytes / (1024 * 1024), 2),
                "spreads_count": spreads_cnt,
                "history_count": history_cnt,
                "arbs_archive_count": archive_cnt
            }
        except Exception as e:
            return {"error": str(e)}

    def save_spreads(self, spreads: List[Dict]):
        if not spreads:
            return
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat() + "Z"
        
        for s in spreads:
            vol_a = s.get("volume_a", 0.0) or 0.0
            vol_b = s.get("volume_b", 0.0) or 0.0
            liq_b = s.get("liquidity_b", 0.0) or 0.0
            is_arb = 1 if s.get("is_arb", False) else 0
            hedge_margin = s.get("hedge_margin", 0.0) or 0.0
            spread_fee = s.get("spread_after_fees", 0.0) or 0.0

            cursor.execute('''
                INSERT INTO spreads (
                    event_key, platform_a, prob_a, odds_a, url_a, 
                    platform_b, prob_b, odds_b, url_b,
                    spread_after_fees, detected_at, title,
                    prob_sum, implied_cost, overround, is_arb, stake_a, stake_b,
                    time_diff_hours, time_warning,
                    action_summary, action_a, action_b,
                    hedge_cost, hedge_margin,
                    is_arb_time_risky, opp_price_is_synthetic,
                    volume_a, volume_b, liquidity_b
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                s["event_key"],
                s["platform_a"],
                s["prob_a"],
                s.get("odds_a", 0.0),
                s.get("url_a", ""),
                s["platform_b"],
                s["prob_b"],
                s.get("odds_b", 0.0),
                s.get("url_b", ""),
                spread_fee,
                now,
                s.get("title", ""),
                s.get("prob_sum", 0.0),
                s.get("implied_cost", s.get("prob_sum", 0.0)),
                s.get("overround", 0.0),
                is_arb,
                s.get("stake_pos_pct", 0.0),
                s.get("stake_neg_pct", 0.0),
                s.get("time_diff_hours", 0.0),
                s.get("time_warning", ""),
                s.get("action_summary", ""),
                s.get("action_a", ""),
                s.get("action_b", ""),
                s.get("hedge_cost", 0.0),
                hedge_margin,
                1 if s.get("is_arb_time_risky", False) else 0,
                1 if s.get("opp_price_is_synthetic", False) else 0,
                vol_a,
                vol_b,
                liq_b
            ))

            # Auto-archive if true surebet or high profit spread (excluding 5MIN/15MIN test contracts)
            ev_k = s["event_key"].upper()
            if (is_arb == 1 or hedge_margin > 0 or spread_fee >= 0.05) and ("5MIN" not in ev_k and "15MIN" not in ev_k):
                cursor.execute('''
                    INSERT OR IGNORE INTO arbs_archive (
                        event_key, platform_a, prob_a, odds_a, url_a,
                        platform_b, prob_b, odds_b, url_b,
                        spread_after_fees, detected_at, title,
                        hedge_cost, hedge_margin, is_arb, is_arb_time_risky,
                        action_summary, action_a, action_b, time_warning,
                        volume_a, volume_b, liquidity_b, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    s["event_key"],
                    s["platform_a"],
                    s["prob_a"],
                    s.get("odds_a", 0.0),
                    s.get("url_a", ""),
                    s["platform_b"],
                    s["prob_b"],
                    s.get("odds_b", 0.0),
                    s.get("url_b", ""),
                    spread_fee,
                    now,
                    s.get("title", ""),
                    s.get("hedge_cost", 0.0),
                    hedge_margin,
                    is_arb,
                    1 if s.get("is_arb_time_risky", False) else 0,
                    s.get("action_summary", ""),
                    s.get("action_a", ""),
                    s.get("action_b", ""),
                    s.get("time_warning", ""),
                    vol_a,
                    vol_b,
                    liq_b,
                    now
                ))
            
        conn.commit()
        conn.close()

    def get_recent_spreads(self, limit: int = 50) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM spreads
            ORDER BY detected_at DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(r) for r in rows]

    def get_latest_clean_spreads(self, limit: int = 50) -> List[Dict]:
        """Fetch latest deduplicated spreads per market strike (excluding 5MIN/15MIN cross-platform test contracts)."""
        import re
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Window query getting the most recent snapshot per daily/target cross-platform event_key
        query_cross = '''
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY detected_at DESC) as rn
                FROM spreads
                WHERE platform_a != platform_b
                  AND event_key NOT LIKE '%5MIN%'
                  AND event_key NOT LIKE '%15MIN%'
            ) WHERE rn = 1
            ORDER BY spread_after_fees DESC
        '''
        cursor.execute(query_cross)
        cross_rows = cursor.fetchall()
        
        # 2. Window query getting latest complementary pairs
        query_comp = '''
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY detected_at DESC) as rn
                FROM spreads
                WHERE platform_a = platform_b
            ) WHERE rn = 1
            ORDER BY detected_at DESC
            LIMIT 25
        '''
        cursor.execute(query_comp)
        comp_rows = cursor.fetchall()
        conn.close()
        
        # Consolidate complementary ABOVE/BELOW sides of the same strike into 1 clean row
        clean_strikes = {}
        for r in cross_rows:
            d = dict(r)
            title = d.get('title', '')
            clean_title = re.sub(r'\s*\[(ABOVE|BELOW|Yes|No)\]', '', title)
            if clean_title not in clean_strikes:
                d['title'] = clean_title
                clean_strikes[clean_title] = d
            else:
                existing = clean_strikes[clean_title]
                d_arb = d.get('is_arb', 0) or 0
                e_arb = existing.get('is_arb', 0) or 0
                d_margin = d.get('hedge_margin', -999) if d.get('hedge_margin') is not None else -999
                e_margin = existing.get('hedge_margin', -999) if existing.get('hedge_margin') is not None else -999
                if (d_arb > e_arb) or (d_arb == e_arb and d_margin > e_margin):
                    d['title'] = clean_title
                    clean_strikes[clean_title] = d
                
        cross_results = list(clean_strikes.values())
        cross_results.sort(key=lambda x: (x.get("is_arb", 0) or 0, x.get("spread_after_fees", 0) or 0), reverse=True)
        
        # Combine clean cross strikes + latest complementary Bybit pairs
        return cross_results[:limit] + [dict(r) for r in comp_rows]

    def get_updown_spreads(self, limit: int = 25) -> List[Dict]:
        """Fetch latest 5MIN and 15MIN cross-platform Up/Down spreads for the dedicated test tab."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY detected_at DESC) as rn
                FROM spreads
                WHERE platform_a != platform_b
                  AND (event_key LIKE '%5MIN%' OR event_key LIKE '%15MIN%')
            ) WHERE rn = 1
            ORDER BY hedge_margin DESC, spread_after_fees DESC
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_top_spreads(self, limit: int = 15) -> List[Dict]:
        """Fetch the highest profit / spread opportunities recorded in the database (excluding 5m/15m test contracts)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM spreads
            WHERE (is_arb = 1 OR spread_after_fees > 0)
              AND event_key NOT LIKE '%5MIN%'
              AND event_key NOT LIKE '%15MIN%'
            ORDER BY is_arb DESC, hedge_margin DESC, spread_after_fees DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(r) for r in rows]

    def save_price_history(self, outcomes: list):
        """Save raw outcomes/tickers to history table for V2 statistical momentum analysis."""
        if not outcomes:
            return
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        for out in outcomes:
            title = out.get("raw_title") or out.get("title", "")
            cursor.execute('''
                INSERT INTO price_history (
                    market_id, platform, title, prob, odds, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                out["market_id"],
                out["platform"],
                title,
                out["implied_probability"],
                out.get("odds", 0.0),
                now
            ))
            
        conn.commit()
        conn.close()

    def get_archived_arbs(self, limit: int = 50) -> List[Dict]:
        """Fetch permanent historical surebets and high spreads from arbs_archive (excluding 5m/15m test contracts)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM arbs_archive
            WHERE event_key NOT LIKE '%5MIN%'
              AND event_key NOT LIKE '%15MIN%'
            ORDER BY is_arb DESC, hedge_margin DESC, spread_after_fees DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(r) for r in rows]

    def get_spread_history(self, event_key: str, title: str = "", hours: int = 12, max_points: int = 150) -> Dict:
        """Fetch chronological spread history for a market strike to render charts."""
        import re
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        title_prefix = ""
        if title:
            m = re.match(r'([A-Za-z]+\s+\$[0-9,]+)', title)
            if m:
                title_prefix = f"{m.group(1)}%"
            else:
                title_prefix = f"{title.split('(')[0].strip()}%"

        query = '''
            SELECT detected_at, spread_after_fees, hedge_cost, hedge_margin, is_arb, is_arb_time_risky, prob_a, odds_a, prob_b, odds_b
            FROM spreads
            WHERE (event_key = ? OR (length(?) > 0 AND title LIKE ?))
              AND detected_at >= datetime('now', ?)
            ORDER BY detected_at ASC
        '''
        cursor.execute(query, (event_key, title_prefix, title_prefix, f"-{hours} hours"))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return {
                "points": [],
                "stats": {"count": 0, "min_spread": 0, "max_spread": 0, "avg_spread": 0, "max_margin": 0}
            }

        spreads = [r["spread_after_fees"] for r in rows]
        margins = [(r["hedge_margin"] or 0) for r in rows]
        
        # Downsample if too many points for smooth rendering
        step = max(1, len(rows) // max_points)
        sampled = rows[::step]
        if sampled[-1] != rows[-1]:
            sampled.append(rows[-1])

        points = []
        for r in sampled:
            points.append({
                "time": r["detected_at"],
                "spread": round(r["spread_after_fees"] * 100, 2),
                "margin": round((r["hedge_margin"] or 0) * 100, 2),
                "is_arb": 1 if r["is_arb"] else 0,
                "is_arb_time_risky": 1 if r["is_arb_time_risky"] else 0,
                "odds_a": r["odds_a"] or 0,
                "odds_b": r["odds_b"] or 0
            })

        min_sp = round(min(spreads) * 100, 2)
        max_sp = round(max(spreads) * 100, 2)
        avg_sp = round((sum(spreads) / len(spreads)) * 100, 2)
        max_margin = round(max(margins) * 100, 2)

        return {
            "points": points,
            "stats": {
                "count": len(rows),
                "sampled_count": len(points),
                "min_spread": min_sp,
                "max_spread": max_sp,
                "avg_spread": avg_sp,
                "max_margin": max_margin,
                "first_seen": rows[0]["detected_at"],
                "last_seen": rows[-1]["detected_at"]
            }
        }
