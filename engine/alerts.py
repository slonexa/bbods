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
            is_arb = bool(s.get("is_arb", False) or s.get("is_arb") == 1)
            is_time_risky = bool(s.get("is_arb_time_risky", False) or s.get("is_arb_time_risky") == 1)
            margin_pct = round((s.get("hedge_margin") or 0) * 100, 2)
            spread_pct = round((s.get("spread_after_fees") or 0) * 100, 2)
            event_key = s.get("event_key") or s.get("title", "")

            # Check eligibility
            should_alert = False
            alert_type = ""
            odds_a = s.get("odds_a") or 0.0
            odds_b = s.get("odds_b") or 0.0
            if odds_a <= 1.002 or odds_b <= 1.002:
                continue
            
            if (is_arb or is_time_risky) and self.config.get("notify_on_surebet", True) and margin_pct >= min_margin_pct:
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
                "event_key": event_key,
                "title": s.get("title") or event_key,
                "spread_pct": spread_pct,
                "margin_pct": margin_pct,
                "is_arb": is_arb,
                "odds_a": s.get("odds_a", 0.0),
                "odds_b": s.get("odds_b", 0.0),
                "prob_a": s.get("prob_a", 0.0),
                "prob_b": s.get("prob_b", 0.0),
                "hedge_cost": s.get("hedge_cost", 0.0),
                "stake_a": s.get("stake_pos_pct", 0.5),
                "stake_b": s.get("stake_neg_pct", 0.5),
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
        margin_val = float(alert.get("margin_pct") or 0.0)
        log_line = f"[{alert['timestamp']}] [{alert['type']}] {alert['title']} | Spread: +{alert['spread_pct']}% | Margin: {margin_val:+.2f}%\n"
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

        print(f"\n🔔 [ALERT - {alert['type']}] {alert['title']} -> Spread: +{alert['spread_pct']}% | Margin: {margin_val:+.2f}%")

        # 2. Telegram message
        if self.config.get("telegram_enabled", False):
            token = self.config.get("telegram_bot_token", "").strip()
            chat_id = self.config.get("telegram_chat_id", "").strip()
            if token and chat_id:
                self.send_telegram_alert(token, chat_id, alert)

    def send_telegram_alert(self, token: str, chat_id: str, alert: Dict[str, Any]) -> bool:
        """Send formatted HTML alert to Telegram via Bot API with $100 calculation and localhost deep link."""
        try:
            import re
            from urllib.parse import quote

            is_arb = bool(alert.get("is_arb"))
            spread_pct = float(alert.get("spread_pct") or 0.0)
            margin_pct = float(alert.get("margin_pct") or 0.0)
            odds_a = float(alert.get("odds_a") or 1.80)
            odds_b = float(alert.get("odds_b") or 1.80)
            prob_a_pct = round(float(alert.get("prob_a") or (1.0 / odds_a if odds_a > 0 else 0.5)) * 100, 1)
            prob_b_pct = round(float(alert.get("prob_b") or (1.0 / odds_b if odds_b > 0 else 0.5)) * 100, 1)
            hedge_cost = float(alert.get("hedge_cost") or 0.0)

            # Strip redundant prefixes like "Bybit: " or "Poly: "
            act_a = re.sub(r"^(Bybit|Polymarket|Poly)\s*:\s*", "", str(alert.get("action_a") or ""), flags=re.I)
            act_b = re.sub(r"^(Bybit|Polymarket|Poly)\s*:\s*", "", str(alert.get("action_b") or ""), flags=re.I)

            # Calculate $100 bankroll breakdown
            bank = 100.0
            cost_a = (1.0 / odds_a) if odds_a > 0 else 0.50
            cost_b_opp = max(0.01, hedge_cost - cost_a) if hedge_cost > cost_a else max(0.01, 1.0 - (prob_b_pct / 100.0))
            total_cost = cost_a + cost_b_opp
            odds_b_opp = round(1.0 / cost_b_opp, 2)

            stake_a = round(bank * (cost_a / total_cost), 2) if total_cost > 0 else 50.0
            stake_b = round(bank - stake_a, 2)
            pct_a = round((stake_a / bank) * 100)
            pct_b = 100 - pct_a

            payout_single = round(stake_a * odds_a, 2)
            net_single = round(payout_single - bank, 2)
            payout_double = round(payout_single * 2.0, 2)
            net_double = round(payout_double - bank, 2)

            raw_warn = str(alert.get("time_warning") or "").strip()
            has_corridor = "Коридор 2x" in raw_warn

            # Extract corridor range if present
            corridor_line = ""
            exp_line = ""
            if raw_warn:
                parts = [p.strip() for p in raw_warn.split("|") if p.strip()]
                for p in parts:
                    clean_p = re.sub(r"^⚠️\s*", "", p).strip()
                    if "Коридор" in clean_p:
                        corridor_line = f"\n{clean_p}"
                    else:
                        exp_line = f"\n⚠️ <i>{clean_p}</i>"

            if is_arb:
                type_badge = f"⚡ <b>ГАРАНТИРОВАННАЯ ВИЛКА (+{margin_pct:.2f}%)</b>"
            elif has_corridor:
                type_badge = f"🎯 <b>КОРИДОР 2X + СПРЕД (+{spread_pct:.2f}%)</b>"
            else:
                type_badge = f"🔥 <b>МЕГА-СПРЕД КОТИРОВОК (+{spread_pct:.2f}%)</b>"

            title_str = str(alert.get("title") or "")
            exact_strike_match = bool(
                alert.get("exact_strike_match", True)
                and not has_corridor
                and "/ Poly " not in title_str
                and "Зазор" not in raw_warn
            )

            # Value 1-leg comparison ONLY when strikes match exactly
            if odds_a >= odds_b:
                val_side = f"Bybit дает <b>{odds_a:.2f}x</b> против {odds_b:.2f}x на Poly ($100 ➔ <b>${100*odds_a:.0f}</b>, чистыми <b>+${100*(odds_a-1):.0f}</b>)"
            else:
                val_side = f"Poly дает <b>{odds_b:.2f}x (${1.0/odds_b:.2f})</b> против {odds_a:.2f}x на Bybit ($100 ➔ <b>${100*odds_b:.0f}</b>, чистыми <b>+${100*(odds_b-1):.0f}</b>)"

            # Outcome summary for $100
            if is_arb or net_single >= 0:
                outcome_block = (
                    f"• Любой обычный исход: возврат <b>${payout_single:.2f}</b> "
                    f"(Чистыми: <b>+${net_single:.2f} / {margin_pct:+.2f}%</b>)"
                )
                if has_corridor:
                    outcome_block += (
                        f"\n• 🎯 <b>Бонуска 2x (внутри коридора):</b> возврат <b>${payout_double:.2f}</b> "
                        f"(Чистыми: <b>+${net_double:.2f}!</b>)"
                    )
            elif has_corridor:
                outcome_block = (
                    f"• Обычный исход (1 плечо): возврат <b>${payout_single:.2f}</b> (хедж: <code>${net_single:+.2f}</code>)\n"
                    f"• 🎯 <b>Бонуска 2x (внутри коридора):</b> возврат <b>${payout_double:.2f}</b> "
                    f"(Чистыми: <b>+${net_double:.2f} / +{net_double:.0f}%!</b>)"
                )
            elif exact_strike_match:
                outcome_block = (
                    f"• Полный хедж 2 плеч: возврат <b>${payout_single:.2f}</b> (<code>${net_single:+.2f}</code>)\n"
                    f"• 💎 <b>Value (1 плечо без хеджа):</b> {val_side}"
                )
            else:
                outcome_block = (
                    f"• Полный хедж 2 плеч: возврат <b>${payout_single:.2f}</b> (<code>${net_single:+.2f}</code>)"
                )

            # Build direct localhost calculator link
            ev_key = str(alert.get("event_key") or alert.get("title") or "")
            calc_slug = ev_key.split("::")[0].strip() or ev_key
            calc_url = f"http://127.0.0.1:8000/?calc={quote(calc_slug)}&bank=100"

            text = (
                f"{type_badge}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🎯 <b>Рынок:</b> {alert.get('title')}\n"
                f"📊 <b>Вероятности:</b> Bybit <code>{prob_a_pct}%</code> vs Poly <code>{prob_b_pct}%</code> (Спред: <code>+{spread_pct:.2f}%</code>)\n"
                f"💰 <b>Маржа хеджа:</b> <code>{margin_pct:+.2f}%</code> (Стоимость: <code>${total_cost:.2f}</code>)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💵 <b>РАСЧЕТ НА ПРИМЕРЕ $100:</b>\n"
                f"1️⃣ <b>Bybit — поставить ${stake_a:.2f} ({pct_a}%):</b>\n"
                f"   🟡 {act_a} ➔ выплата <b>${payout_single:.2f}</b>\n"
                f"2️⃣ <b>Polymarket — поставить ${stake_b:.2f} ({pct_b}%):</b>\n"
                f"   🔵 {act_b} ({odds_b_opp:.2f}x) ➔ выплата <b>${payout_single:.2f}</b>\n\n"
                f"📌 <b>Итог со $100:</b>\n"
                f"{outcome_block}"
                f"{corridor_line}"
                f"{exp_line}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🧮 <a href='{calc_url}'>Открыть панель расчета ($100) в терминале</a>\n"
                f"<code>{calc_url}</code>\n"
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
        """Send a test ping with a sample $100 calculation and localhost calculator link."""
        try:
            sample_alert = {
                "is_arb": False,
                "event_key": "ETHUSDT-27SEP26-2690-ABOVE::sample",
                "title": "ETH $2,690 / Poly $2,700 [ABOVE] (2026-09-27)",
                "spread_pct": 10.85,
                "margin_pct": -11.05,
                "odds_a": 2.00,
                "odds_b": 2.55,
                "prob_a": 0.50,
                "prob_b": 0.3915,
                "hedge_cost": 1.11,
                "action_a": "Bybit: Взять ABOVE $2,690 (Выше) @ 2.00x",
                "action_b": "Poly: Купить NO $2,700 (Ниже) @ $0.61",
                "url_a": "https://www.bybit.com/ru-RU/trade/odds/ETHUSDT-27SEP26-2690-ABOVE",
                "url_b": "https://polymarket.com/event/ethereum-above-on-september-27-2026",
                "time_warning": "🎯 Коридор 2x выигрыша: $2,690–$2,700 | ⚠️ Разница экспирации: 8.0ч (bybit_odds 08:00 vs polymarket 16:00 UTC)"
            }
            ok = self.send_telegram_alert(token, chat_id, sample_alert)
            if ok:
                return {"status": "success", "message": "Обновленный тестовый сигнал ($100 расчет + ссылка) успешно отправлен!"}
            return {"status": "error", "error": "Ошибка отправки через Telegram API"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self.recent_alerts[:limit]
