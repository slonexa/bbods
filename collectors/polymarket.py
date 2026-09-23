import requests
import json
import re
from datetime import datetime
from .base import BaseCollector

class PolymarketCollector(BaseCollector):
    platform_name = "polymarket"
    
    def __init__(self):
        # Gamma API for Polymarket markets
        self.api_url = "https://gamma-api.polymarket.com/events"

    def fetch(self):
        results = []
        events_by_id = {}
        
        # Fetch top 400 high-volume events across 4 pages to capture daily BTC, ETH, SOL targets
        # without missing markets pushed down by political/elections events
        for offset in [0, 100, 200, 300]:
            params = {
                "active": "true",
                "closed": "false",
                "limit": 100,
                "order": "volume24hr",
                "ascending": "false",
                "offset": offset
            }
            try:
                response = requests.get(self.api_url, params=params, timeout=12)
                if response.status_code == 200:
                    for ev in response.json():
                        events_by_id[ev.get("id")] = ev
            except Exception as e:
                print(f"Error querying Polymarket (offset {offset}): {e}")

        events = list(events_by_id.values())
        
        for event in events:
            event_slug = event.get("slug", "")
            markets = event.get("markets", [])
            
            for market in markets:
                market_id = market.get("id")
                market_slug = market.get("slug", "")
                question = market.get("question", "")
                description = market.get("description", "")
                
                outcomes = market.get("outcomes", [])
                outcome_prices = market.get("outcomePrices", [])
                
                if not outcomes or not outcome_prices:
                    continue
                    
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                if isinstance(outcome_prices, str):
                    outcome_prices = json.loads(outcome_prices)
                    
                # Build market URL
                if market_slug and market_slug != event_slug:
                    url = f"https://polymarket.com/event/{event_slug}/{market_slug}"
                else:
                    url = f"https://polymarket.com/event/{event_slug}"
                
                end_date = market.get("endDate", event.get("endDate", ""))
                settle_date = end_date[:10] if end_date and len(end_date) >= 10 else ""
                
                # Parse structured crypto target metadata
                q_lower = question.lower()
                asset = None
                if re.search(r'\bbitcoin\b|\bbtc\b', q_lower):
                    asset = "BTC"
                elif re.search(r'\bethereum\b|\beth\b', q_lower):
                    asset = "ETH"
                elif re.search(r'\bsolana\b|\bsol\b', q_lower):
                    asset = "SOL"
                    
                # Classify question contract type and direction
                direction = None
                contract_type = "General"
                strike_price = None
                
                if re.search(r'\babove\b', q_lower):
                    direction = "ABOVE"
                    contract_type = "Target"
                elif re.search(r'\bbelow\b|\bunder\b', q_lower):
                    direction = "BELOW"
                    contract_type = "Target"
                elif re.search(r'\breach\b|\bhit\b', q_lower):
                    direction = "REACH"
                    contract_type = "Touch"  # Touch at any time (not compatible with Bybit close)
                elif re.search(r'\bdip\b', q_lower):
                    direction = "DIP"
                    contract_type = "Touch"
                    
                if contract_type == "Target":
                    m_strike = re.search(r'\$([0-9,]+)', question)
                    if m_strike:
                        try:
                            strike_price = float(m_strike.group(1).replace(",", ""))
                        except ValueError:
                            pass
                            
                resolution_source = "Binance" if "binance" in description.lower() else "Polymarket UMA"
                
                # Create separate entries for each outcome
                for idx, outcome in enumerate(outcomes):
                    if idx < len(outcome_prices):
                        prob = float(outcome_prices[idx])
                        results.append(self._format_event(
                            market_id=f"{market_id}-{outcome}",
                            raw_title=question or event.get("title", "Unknown"),
                            outcome=outcome,
                            implied_probability=prob,
                            expiry=end_date,
                            url=url,
                            asset=asset,
                            contract_type=contract_type,
                            strike_price=strike_price,
                            direction=direction,
                            settle_date=settle_date,
                            settle_time=end_date,
                            resolution_source=resolution_source
                        ))
                        
        return results
