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
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/json"
        })

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
                response = self.session.get(self.api_url, params=params, timeout=10)
                if response.status_code == 200:
                    for ev in response.json():
                        events_by_id[ev.get("id")] = ev
            except Exception as e:
                print(f"[{self.platform_name}] Error querying Gamma API (offset {offset}): {e}")

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
                
                # Extract market liquidity and volume
                try:
                    vol = float(market.get("volume") or event.get("volume") or 0.0)
                except Exception:
                    vol = 0.0
                try:
                    vol24 = float(market.get("volume24hr") or event.get("volume24hr") or 0.0)
                except Exception:
                    vol24 = 0.0
                try:
                    liq = float(market.get("liquidity") or event.get("liquidity") or 0.0)
                except Exception:
                    liq = 0.0

                # Parse structured crypto target metadata
                q_lower = question.lower()
                asset = None
                if re.search(r'\bbitcoin\b|\bbtc\b', q_lower):
                    asset = "BTC"
                elif re.search(r'\bethereum\b|\beth\b', q_lower):
                    asset = "ETH"
                elif re.search(r'\bsolana\b|\bsol\b', q_lower):
                    asset = "SOL"
                elif re.search(r'\bxrp\b|\bripple\b', q_lower):
                    asset = "XRP"
                elif re.search(r'\bdogecoin\b|\bdoge\b', q_lower):
                    asset = "DOGE"
                elif re.search(r'\bbnb\b|\bbinance coin\b', q_lower):
                    asset = "BNB"
                    
                # Classify question contract type and direction
                direction = None
                contract_type = "General"
                strike_price = None
                
                # Exclude range / between markets from single-strike Target
                if "between" in q_lower:
                    contract_type = "Range"
                elif re.search(r'\breach\b|\bhit\b|\btouch\b', q_lower):
                    direction = "REACH"
                    contract_type = "Touch"  # Touch at any time (not compatible with Bybit close)
                elif re.search(r'\bdip\b', q_lower):
                    direction = "DIP"
                    contract_type = "Touch"
                elif re.search(r'\babove\b|\bgreater than\b|\bhigher than\b', q_lower):
                    direction = "ABOVE"
                    contract_type = "Target"
                elif re.search(r'\bbelow\b|\bunder\b|\bless than\b|\blower than\b', q_lower):
                    direction = "BELOW"
                    contract_type = "Target"
                    
                if contract_type == "Target" and asset is not None:
                    # Match strikes like $84,000 or $84k or $84.5k or $2,700
                    m_strike = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)\s*([kK])?', question)
                    if m_strike:
                        try:
                            val = float(m_strike.group(1).replace(",", ""))
                            if m_strike.group(2):
                                val *= 1000
                            strike_price = val
                        except ValueError:
                            pass
                else:
                    if contract_type == "Target" and asset is None:
                        contract_type = "General"
                            
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
                            resolution_source=resolution_source,
                            volume=vol,
                            volume_24h=vol24,
                            liquidity=liq
                        ))

        # Also fetch active 5m and 15m Up/Down markets via deterministic slugs + live CLOB orderbooks
        try:
            updown_results = self._fetch_fast_updown_markets()
            results.extend(updown_results)
        except Exception as e:
            print(f"[{self.platform_name}] Error fetching 5m/15m UpDown markets: {e}")

        return results

    def _fetch_fast_updown_markets(self):
        """
        Fetch active 5-minute and 15-minute Up/Down crypto contracts from Polymarket
        using deterministic window slugs and real-time CLOB orderbook bestAsk prices.
        """
        import time
        now_int = int(time.time())
        w5 = (now_int // 300) * 300
        w15 = (now_int // 900) * 900

        configs = [
            ("BTC", "5MIN", "5m", w5),
            ("BTC", "15MIN", "15m", w15),
            ("ETH", "5MIN", "5m", w5),
            ("ETH", "15MIN", "15m", w15),
            ("SOL", "5MIN", "5m", w5),
            ("SOL", "15MIN", "15m", w15),
        ]

        pending_markets = []
        token_requests = []

        for asset, tf_label, poly_dur, win_id in configs:
            slug = f"{asset.lower()}-updown-{poly_dur}-{win_id}"
            try:
                r = self.session.get(f"{self.api_url}?slug={slug}", timeout=4)
                if r.status_code != 200:
                    continue
                ev_list = r.json()
                if not ev_list or not ev_list[0].get("markets"):
                    continue
                ev = ev_list[0]
                m = ev["markets"][0]
                tids = json.loads(m.get("clobTokenIds", "[]"))
                if len(tids) < 2:
                    continue
                pending_markets.append((asset, tf_label, win_id, slug, ev, m, tids))
                token_requests.append({"token_id": tids[0]})
                token_requests.append({"token_id": tids[1]})
            except Exception:
                continue

        if not pending_markets:
            return []

        books = []
        try:
            rb = self.session.post("https://clob.polymarket.com/books", json=token_requests, timeout=4)
            if rb.status_code == 200:
                books = rb.json()
        except Exception:
            books = []

        updown_events = []
        for idx, (asset, tf_label, win_id, slug, ev, m, tids) in enumerate(pending_markets):
            market_id = m.get("id", slug)
            end_date = m.get("endDate", ev.get("endDate", ""))
            settle_date = end_date[:10] if end_date and len(end_date) >= 10 else ""
            url = f"https://polymarket.com/event/{slug}"
            title = m.get("question") or ev.get("title") or f"{asset} {tf_label} Up or Down"

            try:
                vol = float(m.get("volume") or ev.get("volume") or 0.0)
                liq = float(m.get("liquidity") or ev.get("liquidity") or 0.0)
            except Exception:
                vol, liq = 0.0, 0.0

            # Default to Gamma outcomePrices if CLOB orderbook is empty
            raw_prices = m.get("outcomePrices", '["0.5", "0.5"]')
            if isinstance(raw_prices, str):
                raw_prices = json.loads(raw_prices)
            up_prob = float(raw_prices[0]) if len(raw_prices) > 0 else 0.5
            down_prob = float(raw_prices[1]) if len(raw_prices) > 1 else 0.5

            if idx * 2 + 1 < len(books):
                b_up = books[idx * 2]
                b_down = books[idx * 2 + 1]
                u_asks = b_up.get("asks", [])
                d_asks = b_down.get("asks", [])
                ua = min((float(x["price"]) for x in u_asks), default=0.0)
                da = min((float(x["price"]) for x in d_asks), default=0.0)
                if ua > 0:
                    up_prob = ua
                if da > 0:
                    down_prob = da

            for out_label, prob_val in [("UP", up_prob), ("DOWN", down_prob)]:
                if prob_val <= 0:
                    continue
                odds_val = round(1.0 / prob_val, 4)
                updown_events.append(self._format_event(
                    market_id=f"{market_id}-{out_label}",
                    raw_title=f"{asset} {tf_label} Up/Down ({slug[-4:]})",
                    outcome=out_label,
                    implied_probability=prob_val,
                    expiry=end_date,
                    url=url,
                    odds=odds_val,
                    asset=asset,
                    contract_type="UpDown",
                    timeframe=tf_label,
                    direction=out_label,
                    settle_date=settle_date,
                    settle_time=end_date,
                    resolution_source="Chainlink 60s TWAP",
                    volume=vol,
                    liquidity=liq
                ))

        return updown_events

