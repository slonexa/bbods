import json
import threading
import time
from datetime import datetime
import websocket
from curl_cffi import requests
from .base import BaseCollector

class BybitOddsCollector(BaseCollector):
    platform_name = "bybit_odds"

    # Bybit Odds page base URL (used for deep links)
    BASE_URL = "https://www.bybit.com/ru-RU/trade/odds"

    def __init__(self, coins=["BTC", "ETH", "SOL"], contract_types=["UpDown", "Target", "Range", "InOut", "OneTouch"]):
        # Keep track of latest ticker data and contract definitions
        self.tickers = {}  # symbol -> { wp, pr, ... }
        self.contracts = {} # symbol -> { settleTime, targetPrice, upperBound, lowerBound, ... }
        self._lock = threading.Lock()  # protects tickers & contracts from concurrent access
        
        self.coins = coins
        self.contract_types = contract_types
        
        # Persistent HTTP session with connection pooling for sub-100ms live ticker fetches
        self.session = requests.Session()
        
        # Seed initial ticker data via REST
        self._refresh_tickers_from_rest()
        print(f"[{self.platform_name}] Seeded {len(self.tickers)} symbols.")
        
        self.ws_url = "wss://stream.bybit.com/v5/public/event"
        self.ws = None
        self.wst = None
        self.connected = False
        
        self._connect_ws()

    def _refresh_tickers_from_rest(self):
        """Fetch live ticker prices and odds directly from Bybit REST API."""
        endpoint = "https://www.bybit.com/x-api/option/event/webapi/public/ticker_all"
        try:
            r = self.session.get(
                endpoint,
                impersonate="chrome",
                timeout=6
            )
            data = r.json()
            if data.get("ret_code") == 0:
                items = data.get("result", [])
                now_ts = time.time()
                new_tickers = {}
                for item in items:
                    symbol = item.get("symbol")
                    if symbol:
                        item["_fetched_at"] = now_ts
                        new_tickers[symbol] = item
                with self._lock:
                    self.tickers = new_tickers
                return True
            else:
                print(f"[{self.platform_name}] REST error: {data.get('ret_msg')}")
        except Exception as e:
            print(f"[{self.platform_name}] REST request error: {e}")
        return False

    def _connect_ws(self):
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self.wst = threading.Thread(target=self.ws.run_forever)
        self.wst.daemon = True
        self.wst.start()

    def _on_open(self, ws):
        self.connected = True
        print(f"[{self.platform_name}] WebSocket connected.")
        
        # Subscribe to topics
        args = []
        for coin in self.coins:
            for ctype in self.contract_types:
                topic_suffix = f"{coin}.{ctype}"
                args.append(f"event.ticker.all.{topic_suffix}")
                args.append(f"event.contract.symbol.{topic_suffix}")
                
        sub_msg = {"op": "subscribe", "args": args}
        ws.send(json.dumps(sub_msg))

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            if "topic" not in data:
                return
                
            topic = data["topic"]
            payload = data.get("data", [])
            
            with self._lock:
                # Handle contract definitions (settleTime, bounds, etc.)
                if topic.startswith("event.contract.symbol"):
                    items = payload if isinstance(payload, list) else [payload]
                    for item in items:
                        symbol = item.get("symbol")
                        if symbol:
                            self.contracts[symbol] = item
                            
                # Handle ticker updates (wp, pr, etc.)
                elif topic.startswith("event.ticker.all"):
                    items = payload if isinstance(payload, list) else [payload]
                    for item in items:
                        symbol = item.get("symbol")
                        if symbol:
                            if symbol not in self.tickers:
                                self.tickers[symbol] = {}
                            self.tickers[symbol].update(item)
                        
        except Exception as e:
            print(f"[{self.platform_name}] WS message error: {e}")

    def _on_error(self, ws, error):
        print(f"[{self.platform_name}] WS error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        print(f"[{self.platform_name}] WS closed. Reconnecting in 5s...")
        time.sleep(5)
        self._connect_ws()

    @staticmethod
    def parse_symbol(symbol: str) -> dict:
        """
        Parse Bybit Odds symbol into structured components.
        Examples:
            BTCUSDT-5MIN-UP      -> {coin: BTC, pair: BTCUSDT, timeframe: 5MIN, direction: UP, type: UpDown}
            BTCUSDT-15MIN-DOWN   -> {coin: BTC, pair: BTCUSDT, timeframe: 15MIN, direction: DOWN, type: UpDown}
            BTCUSDT-25SEP26-83500-85000-IN  -> {coin: BTC, pair: BTCUSDT, expiry_tag: 25SEP26, lower: 83500, upper: 85000, direction: IN, type: Range}
            BTCUSDT-25SEP26-83500-85000-OUT -> {coin: BTC, pair: BTCUSDT, expiry_tag: 25SEP26, lower: 83500, upper: 85000, direction: OUT, type: Range}
        """
        parts = symbol.split("-")
        info = {"symbol": symbol, "pair": parts[0]}
        
        # Extract coin from pair (BTCUSDT -> BTC)
        for coin in ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]:
            if parts[0].startswith(coin):
                info["coin"] = coin
                break
        else:
            info["coin"] = parts[0].replace("USDT", "")
        
        direction = parts[-1]  # Last part is always direction: UP/DOWN/IN/OUT
        info["direction"] = direction
        
        if len(parts) == 3:
            # Format: PAIR-TIMEFRAME-DIRECTION (e.g. BTCUSDT-5MIN-UP)
            info["timeframe"] = parts[1]
            info["type"] = "UpDown"
            # Complementary direction
            info["complement_direction"] = "DOWN" if direction == "UP" else "UP"
            # Key for matching complementary pairs
            info["match_key"] = f"{parts[0]}-{parts[1]}"
        elif len(parts) == 5:
            # Format: PAIR-EXPIRY-LOWER-UPPER-DIRECTION (e.g. BTCUSDT-25SEP26-83500-85000-IN)
            info["expiry_tag"] = parts[1]
            info["lower_bound"] = parts[2]
            info["upper_bound"] = parts[3]
            info["type"] = "Range"
            info["complement_direction"] = "OUT" if direction == "IN" else "IN"
            info["match_key"] = f"{parts[0]}-{parts[1]}-{parts[2]}-{parts[3]}"
        elif len(parts) == 4:
            # Format: PAIR-EXPIRY-TARGET-DIRECTION (e.g. BTCUSDT-23SEP26-86250-ABOVE)
            info["expiry_tag"] = parts[1]
            info["target_price"] = parts[2]
            info["type"] = "Target"
            info["complement_direction"] = "BELOW" if direction == "ABOVE" else "ABOVE"
            info["match_key"] = f"{parts[0]}-{parts[1]}-{parts[2]}"
        else:
            info["type"] = "Unknown"
            info["match_key"] = symbol
            
        return info

    def _build_url(self, symbol: str) -> str:
        """Build a deep link URL for a Bybit Odds contract."""
        # Bybit Odds URL pattern: /trade/odds/BTCUSDT-5MIN-UP
        return f"{self.BASE_URL}/{symbol}"

    def _build_title(self, symbol: str, contract: dict) -> str:
        """Build a human-readable title for the contract."""
        info = self.parse_symbol(symbol)
        coin = info.get("coin", "?")
        direction = info.get("direction", "?")
        
        if info["type"] == "UpDown":
            timeframe = info.get("timeframe", "?")
            return f"{coin} {timeframe} {direction}"
        elif info["type"] == "Range":
            lower = info.get("lower_bound", "?")
            upper = info.get("upper_bound", "?")
            expiry_tag = info.get("expiry_tag", "?")
            return f"{coin} {expiry_tag} Range {lower}-{upper} {direction}"
        elif info["type"] == "Target":
            target = info.get("target_price", "?")
            expiry_tag = info.get("expiry_tag", "?")
            return f"{coin} {expiry_tag} ${target} {direction}"
        else:
            return f"Bybit {symbol}"

    @staticmethod
    def parse_expiry_date(expiry_tag: str) -> str:
        """Parse Bybit expiry tag like 23SEP26 into ISO date 2026-09-23."""
        months = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
                  "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}
        if len(expiry_tag) >= 7:
            try:
                day = expiry_tag[:2]
                mon = expiry_tag[2:5].upper()
                yr = "20" + expiry_tag[5:]
                if mon in months:
                    return f"{yr}-{months[mon]}-{day}"
            except Exception:
                pass
        return ""

    def fetch(self):
        # Refresh live tickers from Bybit REST on each cycle (connection pooling takes ~80-120ms)
        self._refresh_tickers_from_rest()

        results = []
        now_ts = time.time()
        
        # Take a snapshot under lock to avoid concurrent modification
        with self._lock:
            tickers_snapshot = dict(self.tickers)
            contracts_snapshot = dict(self.contracts)
        
        for symbol, ticker in tickers_snapshot.items():
            # Discard stale tickers older than 180s if a contract expired/disappeared
            if now_ts - ticker.get("_fetched_at", now_ts) > 180:
                continue

            wp_str = ticker.get("wp", "0")
            wp = float(wp_str) if wp_str else 0.0
            if wp <= 0 or wp >= 0.999:
                continue
                
            contract = contracts_snapshot.get(symbol, {})
            
            # Parse the symbol to get structured info
            info = self.parse_symbol(symbol)
            direction = info.get("direction", "")
            
            # Extract payout ratio (odds/coefficient) from Bybit
            pr_str = ticker.get("pr", "")
            pr = float(pr_str) if pr_str else 0.0
            if pr <= 0 and wp > 0:
                pr = round(1.0 / wp, 4)
            if pr <= 1.001:
                continue
            
            # Parse date from expiry_tag if Target or Range
            expiry_tag = info.get("expiry_tag", "")
            settle_date = self.parse_expiry_date(expiry_tag)
            
            # Extract settleTime if available
            settle_time_ms = contract.get("settleTime")
            if settle_time_ms:
                if (int(settle_time_ms) / 1000.0) <= now_ts:
                    continue
                dt = datetime.utcfromtimestamp(int(settle_time_ms) / 1000.0)
                expiry = dt.isoformat() + "Z"
            elif settle_date:
                # Bybit daily options and target events settle at 08:00 UTC
                expiry = f"{settle_date}T08:00:00Z"
                try:
                    exp_dt = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ")
                    if (exp_dt - datetime(1970, 1, 1)).total_seconds() <= now_ts:
                        continue
                except Exception:
                    pass
            else:
                expiry = "unknown"
                
            title = self._build_title(symbol, contract)
            url = self._build_url(symbol)
            
            strike_price = float(info.get("target_price", 0)) if info.get("target_price") else None
            
            # Extract volume if present
            try:
                vol = float(ticker.get("v24") or ticker.get("turnover24h") or ticker.get("v") or 0.0)
            except Exception:
                vol = 0.0

            results.append(self._format_event(
                market_id=symbol,
                raw_title=title,
                outcome=direction,
                implied_probability=wp,
                expiry=expiry,
                url=url,
                odds=pr,  # Use Bybit's own payout ratio as odds
                asset=info.get("coin", ""),
                contract_type=info.get("type", ""),
                timeframe=info.get("timeframe", ""),
                strike_price=strike_price,
                direction=direction,
                settle_date=settle_date,
                settle_time=expiry,
                expiry_tag=expiry_tag,
                volume=vol
            ))
            
        return results
