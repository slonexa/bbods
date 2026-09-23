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
                hedge_margin REAL DEFAULT 0
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
            ("hedge_margin", "REAL DEFAULT 0")
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

        conn.commit()
        conn.close()

    def cleanup_old_records(self, hours: int = 48):
        """Delete historical records older than `hours` to prevent unbounded DB growth."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM spreads WHERE detected_at < datetime('now', ?)", (f"-{hours} hours",))
            cursor.execute("DELETE FROM price_history WHERE timestamp < datetime('now', ?)", (f"-{hours} hours",))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB] Cleanup error: {e}")

    def save_spreads(self, spreads: List[Dict]):
        if not spreads:
            return
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat() + "Z"
        
        for s in spreads:
            cursor.execute('''
                INSERT INTO spreads (
                    event_key, platform_a, prob_a, odds_a, url_a, 
                    platform_b, prob_b, odds_b, url_b,
                    spread_after_fees, detected_at, title,
                    prob_sum, implied_cost, overround, is_arb, stake_a, stake_b,
                    time_diff_hours, time_warning,
                    action_summary, action_a, action_b,
                    hedge_cost, hedge_margin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                s["spread_after_fees"],
                now,
                s.get("title", ""),
                s.get("prob_sum", 0.0),
                s.get("implied_cost", s.get("prob_sum", 0.0)),
                s.get("overround", 0.0),
                1 if s.get("is_arb", False) else 0,
                s.get("stake_pos_pct", 0.0),
                s.get("stake_neg_pct", 0.0),
                s.get("time_diff_hours", 0.0),
                s.get("time_warning", ""),
                s.get("action_summary", ""),
                s.get("action_a", ""),
                s.get("action_b", ""),
                s.get("hedge_cost", 0.0),
                s.get("hedge_margin", 0.0)
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
        """Fetch latest deduplicated spreads per market strike (clean 1-row-per-strike view), plus latest complementary pairs."""
        import re
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Window query getting the most recent snapshot per cross-platform event_key
        query_cross = '''
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY detected_at DESC) as rn
                FROM spreads
                WHERE platform_a != platform_b
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

    def get_top_spreads(self, limit: int = 15) -> List[Dict]:
        """Fetch the highest profit / spread opportunities recorded in the database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM spreads
            WHERE is_arb = 1 OR spread_after_fees > 0
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
