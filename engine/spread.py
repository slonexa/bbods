from typing import Dict, Any, Tuple, List

MAX_TIME_DIFF_HOURS = 0.5  # Events with greater difference are not considered a true surebet

class SpreadEngine:
    def __init__(
        self, 
        fee_a: float = 0.0, 
        fee_b: float = 0.0, 
        slippage: float = 0.0, 
        max_time_diff_hours: float = MAX_TIME_DIFF_HOURS
    ):
        # Polymarket has 0 fees generally (except for limit orders sometimes, or gas fees)
        # Bybit might have trading fees (e.g., 0.1%).
        # Slippage depends on liquidity. We can set defaults for MVP.
        self.fee_a = fee_a
        self.fee_b = fee_b
        self.slippage = slippage
        self.max_time_diff_hours = max_time_diff_hours

    def calculate_spread(self, event_a: Dict[str, Any], event_b: Dict[str, Any]) -> float:
        """
        Calculates the spread between two matched events.
        spread = |prob_A - prob_B| - (fee_A + fee_B + slippage)
        """
        prob_a = event_a.get("implied_probability", 0.0)
        prob_b = event_b.get("implied_probability", 0.0)
        
        if prob_a <= 0 or prob_b <= 0:
            return 0.0
            
        raw_spread = abs(prob_a - prob_b)
        net_spread = raw_spread - (self.fee_a + self.fee_b + self.slippage)
        
        return round(net_spread, 4)

    def calculate_dutch_book(self, event_pos: Dict[str, Any], event_neg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check for Dutch Book (internal arbitrage) on complementary contracts.
        
        Uses ODDS-BASED formula (not probability sum), because platforms like Bybit
        embed their margin in the payout coefficient, not in the implied probability.
        
        For two complementary outcomes with decimal odds O_a and O_b:
          implied_cost = 1/O_a + 1/O_b
          - If < 1.0: arbitrage exists (guaranteed profit by betting both sides)
          - If > 1.0: house has overround (edge)
          
        Example: Bybit UP 1.8x / DOWN 1.8x → 1/1.8 + 1/1.8 = 1.111 → 11.1% overround.
        """
        prob_pos = event_pos.get("implied_probability", 0.0)
        prob_neg = event_neg.get("implied_probability", 0.0)
        
        odds_pos = event_pos.get("odds", 0.0)
        odds_neg = event_neg.get("odds", 0.0)
        
        # Fallback: derive odds from probability if not provided
        if odds_pos <= 0 and prob_pos > 0:
            odds_pos = round(1.0 / prob_pos, 4)
        if odds_neg <= 0 and prob_neg > 0:
            odds_neg = round(1.0 / prob_neg, 4)
        
        # --- Core formula: odds-based implied cost ---
        # This is what you'd actually pay to cover both sides
        implied_inv_pos = (1.0 / odds_pos) if odds_pos > 0 else 0.0
        implied_inv_neg = (1.0 / odds_neg) if odds_neg > 0 else 0.0
        implied_cost = implied_inv_pos + implied_inv_neg  # < 1.0 = arb
        
        # Overround: how much the house takes (positive = house edge)
        overround = implied_cost - 1.0
        
        # Also track raw probability sum for reference
        prob_sum = prob_pos + prob_neg
        
        # Net edge after fees (positive = profitable arb)
        total_fees = self.fee_a + self.fee_b + self.slippage
        net_margin = -overround - total_fees
        
        # Optimal stake split: proportional to 1/odds (guarantees equal payout)
        stake_pos_pct = 0.0
        stake_neg_pct = 0.0
        if implied_cost > 0:
            stake_pos_pct = implied_inv_pos / implied_cost
            stake_neg_pct = implied_inv_neg / implied_cost
            
        return {
            "prob_sum": round(prob_sum, 6),
            "implied_cost": round(implied_cost, 6),
            "overround": round(overround, 6),
            "net_margin": round(net_margin, 6),
            "is_arb": net_margin > 0,
            "odds_pos": odds_pos,
            "odds_neg": odds_neg,
            "stake_pos_pct": round(stake_pos_pct, 4),
            "stake_neg_pct": round(stake_neg_pct, 4)
        }

    def process_matches(
        self, 
        matches: List[Tuple[Dict, Dict]], 
        min_spread: float = 0.0, 
        poly_data: List[Dict] = None
    ) -> List[Dict]:
        """
        Calculates spread and true Dutch Book hedging for cross-platform matches.
        
        True cross-platform hedge logic:
          Leg A: Buy contract on Bybit (odds = O_a, cost = 1/O_a)
          Leg B: Buy complementary contract on Polymarket (price of opp outcome in poly_data)
          Total Hedge Cost = cost_a + cost_b_opp
          - If Total Cost < 1.0 (after fees) AND settlement time matches: TRUE GUARANTEED ARBITRAGE (Surebet)!
          - If time differs > max_time_diff_hours: flagged as is_arb_time_risky (not guaranteed surebet).
        """
        poly_lookup = {}
        if poly_data:
            for item in poly_data:
                mid = str(item.get("market_id", ""))
                out = (item.get("outcome") or "").strip().upper()
                poly_lookup[mid.upper()] = item
                if "-" in mid:
                    base_id = mid.rsplit("-", 1)[0].strip()
                    poly_lookup[(base_id.upper(), out)] = item
                raw_t = item.get("raw_title") or ""
                if raw_t:
                    poly_lookup[(raw_t.strip().lower(), out)] = item

        results = []
        for ea, eb in matches:
            spread = self.calculate_spread(ea, eb)
            if spread >= min_spread:
                # Identify Bybit and Polymarket instances
                is_a_bybit = "bybit" in ea.get("platform", "").lower()
                bybit_ev = ea if is_a_bybit else eb
                poly_ev = eb if is_a_bybit else ea

                # Asset, strike, dates
                asset = bybit_ev.get("asset") or poly_ev.get("asset")
                strike = bybit_ev.get("strike_price") or poly_ev.get("strike_price")
                date = bybit_ev.get("settle_date") or poly_ev.get("settle_date")
                outcome_a = bybit_ev.get("outcome", "")
                outcome_b = poly_ev.get("outcome", "")

                if asset and strike:
                    title = f"{asset} ${int(strike):,} [{outcome_a}] ({date})"
                else:
                    title = bybit_ev.get("raw_title", "") + f" [{outcome_a}]"

                time_diff_h = ea.get("time_diff_hours", eb.get("time_diff_hours", 0.0))
                time_warning = ea.get("time_warning", eb.get("time_warning", ""))
                settle_a = ea.get("settle_time") or ea.get("expiry") or ""
                settle_b = eb.get("settle_time") or eb.get("expiry") or ""

                # --- 1. Leg A (Bybit) ---
                odds_bybit = bybit_ev.get("odds", 0.0)
                prob_bybit = bybit_ev.get("implied_probability", 0.0)
                if odds_bybit <= 0 and prob_bybit > 0:
                    odds_bybit = round(1.0 / prob_bybit, 4)
                cost_bybit = (1.0 / odds_bybit) if odds_bybit > 0 else (prob_bybit if prob_bybit > 0 else 0.5)
                bybit_dir = bybit_ev.get("direction", outcome_a).upper()

                # --- 2. Leg B (Polymarket Complementary Outcome) ---
                prob_poly = poly_ev.get("implied_probability", 0.0)
                odds_poly = poly_ev.get("odds", 0.0)
                if odds_poly <= 0 and prob_poly > 0:
                    odds_poly = round(1.0 / prob_poly, 4)

                poly_out = poly_ev.get("outcome", outcome_b).upper()
                opp_poly_out = "NO" if poly_out == "YES" else "YES"

                opp_item = None
                poly_market_id = str(poly_ev.get("market_id", ""))
                if poly_lookup and poly_market_id:
                    if "-" in poly_market_id:
                        base_id = poly_market_id.rsplit("-", 1)[0].strip()
                        opp_item = poly_lookup.get((base_id.upper(), opp_poly_out)) or poly_lookup.get(f"{base_id}-{opp_poly_out}".upper())
                    if not opp_item:
                        raw_t = poly_ev.get("raw_title") or ""
                        if raw_t:
                            opp_item = poly_lookup.get((raw_t.strip().lower(), opp_poly_out))

                if opp_item and opp_item.get("implied_probability", 0) > 0:
                    cost_poly_opp = float(opp_item["implied_probability"])
                    opp_price_is_synthetic = False
                else:
                    cost_poly_opp = max(0.001, round(1.0 - prob_poly, 4))
                    opp_price_is_synthetic = True

                odds_poly_opp = round(1.0 / cost_poly_opp, 4) if cost_poly_opp > 0 else 0.0

                # --- 3. True Combined Hedge Cost & Margin ---
                real_hedge_cost = round(cost_bybit + cost_poly_opp, 4)
                total_fees = self.fee_a + self.fee_b + self.slippage
                hedge_margin = round(1.0 - real_hedge_cost - total_fees, 4)

                time_safe = abs(time_diff_h) <= self.max_time_diff_hours
                is_cross_arb = (hedge_margin > 0) and time_safe
                is_arb_time_risky = (hedge_margin > 0) and (not time_safe)

                # --- 4. Mathematical Stakes (Guarantees Equal Payout) ---
                stake_bybit_pct = round(cost_bybit / real_hedge_cost, 4) if real_hedge_cost > 0 else 0.5
                stake_poly_pct = round(cost_poly_opp / real_hedge_cost, 4) if real_hedge_cost > 0 else 0.5

                # --- 5. Human-readable action descriptions ---
                bybit_desc = "Выше" if bybit_dir == "ABOVE" else "Ниже"
                poly_desc = "Ниже" if opp_poly_out == "NO" else "Выше"

                action_a = f"Bybit: Взять {bybit_dir} ({bybit_desc}) @ {odds_bybit:.2f}x"
                action_b = f"Poly: Купить {opp_poly_out} ({poly_desc}) @ ${cost_poly_opp:.2f}"
                action_summary = f"Bybit: {bybit_dir} + Poly: {opp_poly_out}"

                # Assign stakes according to which platform is ea vs eb
                stake_a = stake_bybit_pct if is_a_bybit else stake_poly_pct
                stake_b = stake_poly_pct if is_a_bybit else stake_bybit_pct

                results.append({
                    "event_key": f"{ea['market_id']}::{eb['market_id']}",
                    "platform_a": ea["platform"],
                    "prob_a": ea["implied_probability"],
                    "odds_a": odds_bybit if is_a_bybit else odds_poly,
                    "url_a": ea.get("url", ""),
                    "platform_b": eb["platform"],
                    "prob_b": eb["implied_probability"],
                    "odds_b": odds_poly if is_a_bybit else odds_bybit,
                    "url_b": eb.get("url", ""),
                    "spread_after_fees": spread,
                    "title": title,
                    "time_diff_hours": time_diff_h,
                    "time_warning": time_warning,
                    "settle_a": settle_a,
                    "settle_b": settle_b,
                    "hedge_cost": real_hedge_cost,
                    "hedge_margin": hedge_margin,
                    "is_arb": is_cross_arb,
                    "is_arb_time_risky": is_arb_time_risky,
                    "opp_price_is_synthetic": opp_price_is_synthetic,
                    "action_a": action_a,
                    "action_b": action_b,
                    "action_summary": action_summary,
                    "stake_pos_pct": stake_a,
                    "stake_neg_pct": stake_b
                })
        return results

    def process_complementary(self, pairs: List[Tuple[Dict, Dict]], min_margin: float = -0.05) -> List[Dict]:
        """
        Analyse complementary pairs (Bybit UP/DOWN, IN/OUT) for Dutch Book.
        Returns results even if not profitable, for monitoring.
        min_margin: include results where net_margin > this threshold
                    (negative means show slightly unprofitable too, for visibility)
        """
        results = []
        for e_pos, e_neg in pairs:
            analysis = self.calculate_dutch_book(e_pos, e_neg)
            
            if analysis["net_margin"] > min_margin:
                odds_pos = e_pos.get("odds", 0.0)
                odds_neg = e_neg.get("odds", 0.0)
                if odds_pos <= 0 and e_pos.get("implied_probability", 0) > 0:
                    odds_pos = round(1.0 / e_pos["implied_probability"], 4)
                if odds_neg <= 0 and e_neg.get("implied_probability", 0) > 0:
                    odds_neg = round(1.0 / e_neg["implied_probability"], 4)
                
                # Use a combined title
                title_pos = e_pos.get("raw_title", e_pos["market_id"])
                title_neg = e_neg.get("raw_title", e_neg["market_id"])
                title = f"{title_pos} vs {title_neg}"
                
                results.append({
                    "event_key": f"{e_pos['market_id']}::{e_neg['market_id']}",
                    "platform_a": e_pos["platform"],
                    "prob_a": e_pos["implied_probability"],
                    "odds_a": odds_pos,
                    "url_a": e_pos.get("url", ""),
                    "platform_b": e_neg["platform"],
                    "prob_b": e_neg["implied_probability"],
                    "odds_b": odds_neg,
                    "url_b": e_neg.get("url", ""),
                    "spread_after_fees": analysis["net_margin"],
                    "prob_sum": analysis["prob_sum"],
                    "implied_cost": analysis["implied_cost"],
                    "overround": analysis["overround"],
                    "is_arb": analysis["is_arb"],
                    "stake_pos_pct": analysis["stake_pos_pct"],
                    "stake_neg_pct": analysis["stake_neg_pct"],
                    "title": title
                })
        return results
