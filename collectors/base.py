from typing import List, Dict, Any
from datetime import datetime

class BaseCollector:
    platform_name: str = "base"

    def fetch(self) -> List[Dict[str, Any]]:
        """
        Fetches data from the platform and returns a list of dictionaries 
        with the unified format.
        """
        raise NotImplementedError

    def _format_event(
        self,
        market_id: str,
        raw_title: str,
        outcome: str,
        implied_probability: float,
        expiry: str,
        url: str = "",
        odds: float = 0.0,
        fetched_at: str = None,
        **extra
    ) -> Dict[str, Any]:
        # Calculate odds (coefficient) from probability if not provided
        if odds <= 0 and implied_probability > 0:
            odds = round(1.0 / implied_probability, 4)
        data = {
            "platform": self.platform_name,
            "market_id": market_id,
            "raw_title": raw_title,
            "outcome": outcome,
            "implied_probability": implied_probability,
            "odds": odds,
            "expiry": expiry,
            "url": url,
            "fetched_at": fetched_at or datetime.utcnow().isoformat() + "Z"
        }
        data.update(extra)
        return data
