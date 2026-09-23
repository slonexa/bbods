import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from collectors.polymarket import PolymarketCollector
from collectors.bybit import BybitOddsCollector
from normalizer.mapper import Normalizer
from engine.spread import SpreadEngine
from engine.db import Database

def main():
    print("Initializing collectors...")
    poly_collector = PolymarketCollector()
    bybit_collector = BybitOddsCollector()
    
    print("Initializing normalizer and engine...")
    normalizer = Normalizer()
    # Bybit Odds: ~0 explicit fee (built into odds), Polymarket: 0 fees
    # Slippage estimate: 0.1%
    engine = SpreadEngine(fee_a=0.0, fee_b=0.0, slippage=0.001)
    db = Database()
    
    poll_interval = 10  # seconds
    
    print("Running initial DB cleanup (retention: 48h)...")
    db.cleanup_old_records(hours=48)
    cycle_count = 0
    
    print("Starting data collection loop. Press Ctrl+C to stop.\n")
    
    try:
        while True:
            try:
                cycle_count += 1
                if cycle_count % 300 == 0:
                    print("[DB] Running periodic cleanup of records older than 48h...")
                    db.cleanup_old_records(hours=48)
                
                start_time = time.time()
                
                # 1. Collect data from both platforms
                print("-- Fetching Bybit Odds...")
                bybit_data = bybit_collector.fetch()
                print(f"   Bybit: {len(bybit_data)} outcomes")
                for bd in bybit_data:
                    odds_str = f" (odds={bd['odds']:.3f}x)" if bd.get('odds', 0) > 0 else ""
                    print(f"     {bd['market_id']}: prob={bd['implied_probability']:.4f}{odds_str}")
                
                print("-- Fetching Polymarket...")
                poly_data = poly_collector.fetch()
                print(f"   Polymarket: {len(poly_data)} outcomes")
                
                all_spreads = []
                
                # 2a. Match complementary Bybit pairs (Dutch Book check)
                complementary_pairs = normalizer.match_complementary_pairs(bybit_data)
                print(f"\n-- Complementary pairs found: {len(complementary_pairs)}")
                
                if complementary_pairs:
                    comp_results = engine.process_complementary(complementary_pairs, min_margin=-0.10)
                    for cr in comp_results:
                        status = "ARB" if cr.get("is_arb") else "overround"
                        print(f"   {cr['title']}")
                        print(f"     prob_sum={cr['prob_sum']:.4f}  margin={cr['spread_after_fees']:.4f}  {status}")
                    all_spreads.extend(comp_results)
                
                # 2b. Cross-platform matching: Auto Target Matching + manual event_map.json
                auto_matches = normalizer.auto_match_crypto_targets(bybit_data, poly_data)
                manual_matches = normalizer.match_events(bybit_data, poly_data)
                
                # Combine without duplicate event keys
                seen_keys = set()
                cross_matches = []
                for ea, eb in auto_matches + manual_matches:
                    k = f"{ea['market_id']}::{eb['market_id']}"
                    if k not in seen_keys:
                        seen_keys.add(k)
                        cross_matches.append((ea, eb))
                
                print(f"\n-- Cross-platform matches (Auto Targets + Map): {len(cross_matches)}")
                
                if cross_matches:
                    cross_results = engine.process_matches(cross_matches, min_spread=0.0, poly_data=poly_data)
                    for xr in cross_results:
                        time_info = f" [{xr.get('time_warning', '')}]" if xr.get("time_warning") else ""
                        synth_info = " [SYNTH_PRICE]" if xr.get("opp_price_is_synthetic") else ""
                        arb_status = " [SUREBET!]" if xr.get("is_arb") else (" [TIME-RISKY ARB]" if xr.get("is_arb_time_risky") else "")
                        print(f"   {xr['title']}: spread={xr['spread_after_fees']*100:.2f}% (Bybit={xr['prob_a']*100:.1f}%, Poly={xr['prob_b']*100:.1f}%){time_info}{synth_info}{arb_status}")
                    all_spreads.extend(cross_results)
                
                # 3. Save all to DB
                if all_spreads:
                    db.save_spreads(all_spreads)
                    print(f"\n[OK] Saved {len(all_spreads)} entries to DB.")
                else:
                    print("\n[!] No spreads to save this cycle.")
                    
                # 4. Save raw outcomes for V2 statistical analysis (momentum)
                history_data = list(bybit_data)
                if poly_data:
                    poly_targets = [p for p in poly_data if p.get("contract_type") == "Target"]
                    history_data.extend(poly_targets)
                if history_data:
                    db.save_price_history(history_data)
                    
                elapsed = time.time() - start_time
                sleep_time = max(0, poll_interval - elapsed)
                print(f"-- Sleeping for {sleep_time:.1f}s...\n{'-'*50}\n")
                time.sleep(sleep_time)
                
            except KeyboardInterrupt:
                raise  # let outer handler catch Ctrl+C
            except Exception as e:
                print(f"\n[ERROR] Cycle failed: {type(e).__name__}: {e}")
                print(f"[ERROR] Retrying in 5s...\n{'-'*50}\n")
                time.sleep(5)
            
    except KeyboardInterrupt:
        print("\nStopping scanner.")

if __name__ == "__main__":
    main()
