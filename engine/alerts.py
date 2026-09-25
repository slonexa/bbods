import time
import os
import json
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "alert_config.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "alerts.log")

DEFAULT_CONFIG = {
    "enabled": True,
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "min_spread_alert_pct": 10.0,      # Alert if net spread >= 10%
    "min_margin_alert_pct": 0.0,       # Alert if net hedge margin > 0% (any surebet)
    "cooldown_minutes": 10,            # Don't alert same event within 10 mins
    "notify_on_surebet": True,
    "notify_on_high_spread": True
}

class AlertManager:
    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self.config = self.load_config()
        self.recent_alerts = []  # In-memory history of last 50 alerts
        self.cooldown_tracker = {}  # event_key -> last_alert_timestamp

    def load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cfg = dict(DEFAULT_CONFIG)
                    cfg.update(data)
                    return cfg
            except Exception as e:
                print(f"[Alerts] Config load error: {e}")
        return dict(DEFAULT_CONFIG)

    def save_config(self, new_config: Dict[str, Any]) -> bool:
        try:
            self.config.update(new_config)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[Alerts] Config save error: {e}")
            return False

    @staticmethod
    def _base_contract_key(title: str, event_key: str) -> str:
        """
        Normalizes '[ABOVE]' and '[BELOW]' of the same strike+date into a single canonical key
        so Telegram never receives two messages (ABOVE + BELOW) for the same contract.
        """
        import re
        base = (title or event_key or "").upper()
        base = re.sub(r"\[(ABOVE|BELOW|UP|DOWN|YES|NO)\]", "", base)
        base = re.sub(r"-(ABOVE|BELOW|UP|DOWN|YES|NO)\b", "", base)
        base = re.sub(r"\s+", " ", base).strip()
        return base

    def process_spreads(self, spreads: List[Dict[str, Any]]):
        """Evaluate spreads and fire alerts for high-value opportunities (deduplicated per strike)."""
        if not self.config.get("enabled", True):
            return

        now_ts = time.time()
        cooldown_sec = self.config.get("cooldown_minutes", 10) * 60
        min_spread_pct = self.config.get("min_spread_alert_pct", 10.0)
        min_margin_pct = self.config.get("min_margin_alert_pct", 0.0)

        # 1. Deduplicate complementary [ABOVE] / [BELOW] pairs of the same strike+date:
        #    Always keep ONLY the direction with the highest hedge_margin.
        best_by_contract: Dict[str, Dict[str, Any]] = {}
        for s in spreads:
            event_key = s.get("event_key") or s.get("title", "")
            title_str = s.get("title") or ""

            # Never trigger Telegram/log alerts for 5MIN / 15MIN test-mode contracts
            if any(tag in event_key.upper() or tag in title_str.upper() for tag in ("5MIN", "15MIN", "UP/DOWN", "UPDOWN")):
                continue

            base_key = self._base_contract_key(title_str, event_key)
            cur_margin = s.get("hedge_margin") if s.get("hedge_margin") is not None else -999.0
            prev = best_by_contract.get(base_key)
            if prev is None:
                best_by_contract[base_key] = s
            else:
                prev_margin = prev.get("hedge_margin") if prev.get("hedge_margin") is not None else -999.0
                if cur_margin > prev_margin:
                    best_by_contract[base_key] = s

        # 2. Evaluate only the single best direction per contract
        for base_key, s in best_by_contract.items():
            is_arb = s.get("is_arb", False) or s.get("is_arb") == 1
            margin_pct = round((s.get("hedge_margin") or 0) * 100, 2)
            spread_pct = round((s.get("spread_after_fees") or 0) * 100, 2)
            event_key = s.get("event_key") or s.get("title", "")

            # Check eligibility
            should_alert = False
            alert_type = ""
            odds_a = s.get("odds_a") or 0.0
            odds_b = s.get("odds_b") or 0.0
            if odds_a <= 1.005 or odds_b <= 1.005:
                continue
            
            if is_arb and self.config.get("notify_on_surebet", True) and margin_pct >= min_margin_pct:
                should_alert = True
                alert_type = "SUREBET"
            elif self.config.get("notify_on_high_spread", True) and spread_pct >= min_spread_pct and margin_pct >= -15.0:
                should_alert = True
                alert_type = "HIGH_SPREAD"

            if not should_alert:
                continue

            # Check cooldown by canonical base_key (covers both ABOVE and BELOW)
            last_alert_time = self.cooldown_tracker.get(base_key, 0)
            if (now_ts - last_alert_time) < cooldown_sec:
                continue

            # Record alert
            self.cooldown_tracker[base_key] = now_ts
            alert_payload = {
                "id": f"{int(now_ts * 1000)}",
                "type": alert_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "title": s.get("title") or event_key,
                "spread_pct": spread_pct,
                "margin_pct": margin_pct,
                "is_arb": is_arb,
                "action_a": s.get("action_a", ""),
                "action_b": s.get("action_b", ""),
                "url_a": s.get("url_a", ""),
                "url_b": s.get("url_b", ""),
                "time_warning": s.get("time_warning", "")
            }

            self.recent_alerts.insert(0, alert_payload)
            if len(self.recent_alerts) > 50:
                self.recent_alerts.pop()

            self._dispatch_alert(alert_payload)

    def _dispatch_alert(self, alert: Dict[str, Any]):
        """Send alert to local log and Telegram if enabled."""
        # 1. Log to file
        log_line = f"[{alert['timestamp']}] [{alert['type']}] {alert['title']} | Spread: +{alert['spread_pct']}% | Margin: +{alert['margin_pct']}%\n"
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

        print(f"\n🔔 [ALERT - {alert['type']}] {alert['title']} -> Spread: +{alert['spread_pct']}% | Margin: +{alert['margin_pct']}%")

        # 2. Telegram message
        if self.config.get("telegram_enabled", False):
            token = self.config.get("telegram_bot_token", "").strip()
            chat_id = self.config.get("telegram_chat_id", "").strip()
            if token and chat_id:
                self.send_telegram_alert(token, chat_id, alert)

    def send_telegram_alert(self, token: str, chat_id: str, alert: Dict[str, Any]) -> bool:
        """Send formatted HTML alert to Telegram via Bot API."""
        try:
            type_badge = "⚡ <b>ГАРАНТИРОВАННАЯ ВИЛКА (SUREBET)!</b>" if alert.get("is_arb") else "🔥 <b>МЕГА-СПРЕД КОТИРОВОК</b>"
            time_warn = f"\n⚠️ <i>{alert['time_warning']}</i>" if alert.get("time_warning") else ""
            
            text = (
                f"{type_badge}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🎯 <b>Рынок:</b> {alert.get('title')}\n"
                f"📈 <b>Чистый спред:</b> <code>+{alert.get('spread_pct')}%</code>\n"
                f"💰 <b>Маржа хеджа:</b> <code>+{alert.get('margin_pct')}%</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🟡 <b>Bybit:</b> {alert.get('action_a')}\n"
                f"🔵 <b>Polymarket:</b> {alert.get('action_b')}"
                f"{time_warn}\n\n"
                f"🔗 <a href='{alert.get('url_a') or '#'}'>Открыть Bybit</a> | "
                f"<a href='{alert.get('url_b') or '#'}'>Открыть Polymarket</a>\n"
                f"⏱ <i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
            )

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            r = requests.post(url, json=payload, timeout=8)
            return r.status_code == 200
        except Exception as e:
            print(f"[Alerts] Telegram error: {e}")
            return False

    def send_test_telegram(self, token: str, chat_id: str) -> Dict[str, Any]:
        """Send a test ping to verify Telegram Bot token and chat ID."""
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": (
                    "🔔 <b>ODDS PULSE — Тестовый сигнал</b>\n\n"
                    "✅ Telegram-бот успешно подключен к терминалу арбитража!\n"
                    "Теперь сюда будут приходить мгновенные пуш-уведомления о найденных вилках и крупных спредах.\n\n"
                    "⏱ <i>Статус: LIVE</i>"
                ),
                "parse_mode": "HTML"
            }
            r = requests.post(url, json=payload, timeout=8)
            res_json = r.json()
            if r.status_code == 200 and res_json.get("ok"):
                return {"status": "success", "message": "Тестовое сообщение успешно отправлено!"}
            else:
                return {"status": "error", "error": res_json.get("description", "Ошибка Telegram API")}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self.recent_alerts[:limit]
