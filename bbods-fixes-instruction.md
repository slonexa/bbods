# Задача для Antigravity: 2 фикса в bbods (engine/spread.py, normalizer/mapper.py)

Репозиторий: `slonexa/bbods`. Ниже два конкретных бага в движке расчёта арбитража/хеджа,
оба искажают надёжность `is_arb == True` на дашборде. Правь в указанном порядке.

---

## Фикс 1 (приоритет выше): `is_arb` не учитывает расхождение времени экспирации

**Файл:** `normalizer/mapper.py`, метод `auto_match_crypto_targets` (строки ~120-136)
**Файл:** `engine/spread.py`, метод `process_matches` (строки ~150-154)

**Проблема:** `mapper.py` уже правильно считает `diff_hours` — разницу времени экспирации между
Bybit Target-контрактом и совпавшим Polymarket-рынком (например Bybit рассчитывается в 08:00 UTC,
а Polymarket — в 16:00 UTC того же дня), и пишет `time_warning` в текст. Но это сейчас только
косметика: в `spread.py` `is_cross_arb = hedge_margin > 0` считается без учёта этой разницы.
Если экспирации расходятся на несколько часов — это **не настоящий surebet**: цена BTC вполне
может быть выше страйка в 08:00 и уйти ниже к 16:00, обе ноги проиграют одновременно, хотя
движок покажет "гарантированный" хедж.

**Что сделать:**

1. В `engine/spread.py`, метод `process_matches`, добавить константу порога (вынести в конфиг
   модуля или параметр `__init__` `SpreadEngine`, не хардкодить в середине метода):
   ```python
   MAX_TIME_DIFF_HOURS = 0.5  # события с большей разницей не считаются истинным surebet
   ```
2. Заменить текущий расчёт `is_cross_arb`:
   ```python
   is_cross_arb = hedge_margin > 0
   ```
   на:
   ```python
   time_safe = abs(time_diff_h) <= MAX_TIME_DIFF_HOURS
   is_cross_arb = hedge_margin > 0 and time_safe
   ```
3. В возвращаемый словарь результата добавить отдельное поле, чтобы не терять информацию о
   потенциально прибыльных, но временно рискованных матчах (для UI и БД):
   ```python
   "is_arb": is_cross_arb,
   "is_arb_time_risky": hedge_margin > 0 and not time_safe,
   ```
4. В `engine/db.py` — добавить колонку `is_arb_time_risky INTEGER DEFAULT 0` в таблицу `spreads`
   (миграция по аналогии с уже существующими `ALTER TABLE` в списке миграций) и сохранять её в
   `save_spreads`.
5. В дашборде (`dashboard/static/index.html`) — если `is_arb_time_risky == 1`, показывать отдельный
   статус, например `⚠️ ВИЛКА (риск времени)` жёлтым, а не `✓ ВИЛКА` зелёным, чтобы визуально не
   путать с настоящим гарантированным хеджем.

---

## Фикс 2: синтетическая цена противоположной стороны Polymarket вместо реальной

**Файл:** `engine/spread.py`, метод `process_matches` (строка ~147)

**Проблема:** сейчас цена противоположного исхода на Polymarket вычисляется как:
```python
cost_poly_opp = max(0.001, round(1.0 - prob_poly, 4))
```
Это предполагает, что Yes + No на Polymarket всегда точно суммируются в 1.0 — то есть ровно та
же ошибка ("assumed 0% bookmaker margin"), которую уже поймали и исправили для Bybit
(см. CHANGELOG.md, "Elimination of Phantom Arbitrage"). Но у нас уже ЕСТЬ реальная цена
противоположного исхода — `collectors/polymarket.py` создаёт отдельную запись результата для
каждого outcome рынка (строки 108-125 в `polymarket.py`), включая No с его собственным реальным
`implied_probability` из `outcomePrices`. Она просто не используется.

**Что сделать:**

1. В `run.py` (или там, где `poly_data` доступен на момент вызова `engine.process_matches`),
   передавать в `process_matches` не только список пар `(ea, eb)`, но и полный `poly_data`
   (список всех outcomes Polymarket этого цикла), либо построить словарь
   `poly_by_market_root_and_outcome` заранее и передать его как дополнительный параметр.
2. В `engine/spread.py`, метод `process_matches`, заменить блок:
   ```python
   cost_poly_opp = max(0.001, round(1.0 - prob_poly, 4))
   odds_poly_opp = round(1.0 / cost_poly_opp, 4)
   ```
   на поиск реальной записи противоположного outcome в `poly_data` по тому же базовому
   `market_id` (тому же рынку Polymarket, у которого `outcome` — это `opp_poly_out`, а не
   `poly_out`). Если такая запись найдена — использовать её `implied_probability` напрямую как
   `cost_poly_opp`. Если не найдена (например outcome с таким именем не пришёл в этом цикле —
   бывает при сетевых обрывах) — оставить текущий fallback `1.0 - prob_poly` **только как
   запасной вариант**, и в этом случае помечать результат флагом `"opp_price_is_synthetic": true`
   в возвращаемом словаре, чтобы дашборд мог показать это отдельно (например мелкой пометкой
   рядом со спредом), а не выдавать за такую же надёжную цифру, как настоящая рыночная цена.
3. Обновить `engine/db.py`: добавить колонку `opp_price_is_synthetic INTEGER DEFAULT 0` и
   сохранять её аналогично `is_arb_time_risky` из Фикса 1.

---

## Порядок работы

1. Сначала Фикс 1 (сильнее искажает надёжность `✓ ВИЛКА`).
2. Потом Фикс 2.
3. После обоих фиксов — прогнать `run.py` на 10-15 минут вживую и вручную сверить 2-3 записи
   из `spreads.db` (`SELECT * FROM spreads WHERE is_arb=1 ORDER BY detected_at DESC LIMIT 5`),
   что `time_diff_hours` и `opp_price_is_synthetic` действительно отражают то, что видно на
   реальных страницах Bybit/Polymarket в браузере — прежде чем доверять цифрам на дашборде
   и тем более думать об автотрейде.
