import json
import os
from typing import List, Dict, Any, Tuple

class Normalizer:
    def __init__(self, map_file: str = "event_map.json"):
        self.map_file = os.path.join(os.path.dirname(__file__), map_file)
        self.mapping = self._load_mapping()
        # reverse mapping for bidirectional lookup
        self.reverse_mapping = {v: k for k, v in self.mapping.items()}

    def _load_mapping(self) -> Dict[str, str]:
        if not os.path.exists(self.map_file):
            return {}
        try:
            with open(self.map_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading event map: {e}")
            return {}

    def match_events(self, events_a: List[Dict[str, Any]], events_b: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict]]:
        """
        Takes two lists of events from different platforms.
        Returns a list of tuples (event_a, event_b) that represent the same market outcome.
        Uses manual event_map.json for cross-platform matching.
        """
        matched = []
        
        # Build dict for faster lookup for events_b
        b_by_id = {e["market_id"]: e for e in events_b}
        
        for ea in events_a:
            ea_id = ea["market_id"]
            
            # Check if ea_id is in mapping (ea -> eb)
            if ea_id in self.mapping:
                target_id = self.mapping[ea_id]
                if target_id in b_by_id:
                    matched.append((ea, b_by_id[target_id]))
            
            # Check if ea_id is in reverse mapping (eb -> ea)
            elif ea_id in self.reverse_mapping:
                target_id = self.reverse_mapping[ea_id]
                if target_id in b_by_id:
                    matched.append((ea, b_by_id[target_id]))
                    
        return matched

    @staticmethod
    def auto_match_crypto_targets(events_a: List[Dict[str, Any]], events_b: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict]]:
        """
        Automatically match crypto Target contracts across platforms
        (e.g. Bybit Target vs Polymarket Daily Above/Below).
        
        Matching criteria:
        1. Both must have contract_type == 'Target'
        2. Same asset (BTC, ETH, etc.)
        3. Exact same strike price (e.g. $86,000 == $86,000)
        4. Same settlement date (e.g. 2026-09-23)
        5. Compatible direction:
           - Bybit ABOVE ↔ Polymarket Yes (on 'above' question)
           - Bybit BELOW ↔ Polymarket No (on 'above' question)
        6. Accurately tracks and flags settlement time discrepancy (e.g. Bybit 08:00 UTC vs Polymarket 16:00 UTC).
        """
        matched = []
        from datetime import datetime
        
        # Filter target events from both platforms
        targets_a = [e for e in events_a if e.get("contract_type") == "Target" and e.get("strike_price")]
        targets_b = [e for e in events_b if e.get("contract_type") == "Target" and e.get("strike_price")]
        
        for ea in targets_a:
            for eb in targets_b:
                # 1. Asset check (BTC, ETH)
                asset_a = ea.get("asset")
                asset_b = eb.get("asset")
                if not asset_a or not asset_b or asset_a != asset_b:
                    continue
                    
                # 2. Strike price check
                if ea.get("strike_price") != eb.get("strike_price"):
                    continue
                    
                # 3. Settlement date check
                date_a = ea.get("settle_date")
                date_b = eb.get("settle_date")
                if not date_a or not date_b or date_a != date_b:
                    continue
                    
                # 4. Direction compatibility
                # Identify which is Bybit and which is Polymarket
                bybit_ev = ea if ea.get("platform") == "bybit_odds" else (eb if eb.get("platform") == "bybit_odds" else None)
                poly_ev = eb if eb.get("platform") == "polymarket" else (ea if ea.get("platform") == "polymarket" else None)
                
                if bybit_ev and poly_ev:
                    bybit_dir = bybit_ev.get("direction", "") # ABOVE / BELOW
                    poly_outcome = poly_ev.get("outcome", "") # Yes / No
                    poly_dir = poly_ev.get("direction", "")   # ABOVE / BELOW
                    
                    is_compatible = False
                    if poly_dir == "ABOVE":
                        if bybit_dir == "ABOVE" and poly_outcome == "Yes":
                            is_compatible = True
                        elif bybit_dir == "BELOW" and poly_outcome == "No":
                            is_compatible = True
                    elif poly_dir == "BELOW":
                        if bybit_dir == "BELOW" and poly_outcome == "Yes":
                            is_compatible = True
                        elif bybit_dir == "ABOVE" and poly_outcome == "No":
                            is_compatible = True
                            
                    if not is_compatible:
                        continue
                else:
                    # Generic fallback: same direction and outcome
                    if ea.get("outcome") != eb.get("outcome"):
                        continue
                        
                # 5. Calculate settlement time difference
                time_a = ea.get("settle_time") or ea.get("expiry") or ""
                time_b = eb.get("settle_time") or eb.get("expiry") or ""
                diff_hours = 0.0
                
                try:
                    dt_a = datetime.fromisoformat(time_a.replace("Z", "+00:00"))
                    dt_b = datetime.fromisoformat(time_b.replace("Z", "+00:00"))
                    diff_hours = (dt_b - dt_a).total_seconds() / 3600.0
                except Exception:
                    diff_hours = 0.0
                    
                time_warning = ""
                if abs(diff_hours) > 0.01:
                    time_warning = f"⚠️ Разница экспирации: {abs(diff_hours):.1f}ч ({ea.get('platform')} {time_a[11:16]} vs {eb.get('platform')} {time_b[11:16]} UTC)"
                else:
                    time_warning = "Синхронизировано (0ч)"
                    
                # Attach match metadata to the event instances
                ea_copy = dict(ea)
                eb_copy = dict(eb)
                ea_copy["time_diff_hours"] = diff_hours
                eb_copy["time_diff_hours"] = diff_hours
                ea_copy["time_warning"] = time_warning
                eb_copy["time_warning"] = time_warning
                
                matched.append((ea_copy, eb_copy))

        # Also match 5MIN and 15MIN Up/Down contracts across platforms (Bybit UpDown ↔ Polymarket UpDown)
        import time
        now_int = int(time.time())
        updown_a = [e for e in events_a if e.get("contract_type") == "UpDown"]
        updown_b = [e for e in events_b if e.get("contract_type") == "UpDown"]

        for ea in updown_a:
            for eb in updown_b:
                if ea.get("asset") != eb.get("asset") or not ea.get("asset"):
                    continue
                tf_a = ea.get("timeframe", "")
                tf_b = eb.get("timeframe", "")
                if not tf_a or tf_a != tf_b:
                    continue
                dir_a = (ea.get("direction") or ea.get("outcome") or "").upper()
                dir_b = (eb.get("direction") or eb.get("outcome") or "").upper()
                if not dir_a or dir_a != dir_b:
                    continue

                dur_sec = 300 if tf_a == "5MIN" else 900
                sec_into_win = now_int % dur_sec
                diff_hours = round(sec_into_win / 3600.0, 4)
                time_warning = f"⚡ Синхр. {tf_a} (+{sec_into_win}с от старта окна)"

                ea_copy = dict(ea)
                eb_copy = dict(eb)
                ea_copy["time_diff_hours"] = diff_hours
                eb_copy["time_diff_hours"] = diff_hours
                ea_copy["time_warning"] = time_warning
                eb_copy["time_warning"] = time_warning
                matched.append((ea_copy, eb_copy))
                
        return matched

    @staticmethod
    def match_complementary_pairs(events: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict]]:
        """
        Match complementary Bybit Odds contracts from a single platform.
        
        Complementary pairs:
        - UP ↔ DOWN (same pair + timeframe, e.g. BTCUSDT-5MIN-UP vs BTCUSDT-5MIN-DOWN)
        - IN ↔ OUT  (same pair + expiry + range, e.g. BTCUSDT-25SEP26-83500-85000-IN vs -OUT)
        
        These are mutually exclusive outcomes: prob(UP) + prob(DOWN) should ≈ 1.0.
        If the sum < 1.0, there's an internal arbitrage opportunity (Dutch Book).
        If the sum > 1.0, the house has an edge (overround).
        
        Returns list of (event_positive, event_negative) tuples where:
        - event_positive is UP or IN
        - event_negative is DOWN or OUT
        """
        matched = []
        
        # Group by match_key: strip the direction suffix to find complementary pairs
        groups: Dict[str, Dict[str, Dict]] = {}  # match_key -> {direction -> event}
        
        for event in events:
            symbol = event["market_id"]
            parts = symbol.split("-")
            direction = parts[-1]  # UP, DOWN, IN, OUT
            
            # Build match key (everything except the direction)
            match_key = "-".join(parts[:-1])
            
            if match_key not in groups:
                groups[match_key] = {}
            groups[match_key][direction] = event
        
        # Find complementary pairs
        for match_key, directions in groups.items():
            # UP ↔ DOWN
            if "UP" in directions and "DOWN" in directions:
                matched.append((directions["UP"], directions["DOWN"]))
            # IN ↔ OUT
            if "IN" in directions and "OUT" in directions:
                matched.append((directions["IN"], directions["OUT"]))
            # ABOVE ↔ BELOW
            if "ABOVE" in directions and "BELOW" in directions:
                matched.append((directions["ABOVE"], directions["BELOW"]))
                
        return matched
