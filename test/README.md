# Статус папки `test/` (Единый стек — Python)

Два экспериментальных прототипа на Next.js/TypeScript (`prediction-market-quote-comparison` и `prediction-market-quote-comparison (2`), находившихся ранее в этой папке:
1. **Не содержали Фикса 1** (`MAX_TIME_DIFF_HOURS = 0.5` для блокировки ложных `is_arb` при расхождении времени экспирации) и **Фикса 2** (приоритет реальной цены противоположного исхода Polymarket CLOB над синтетической `1 - p`).
2. Единственный уникальный модуль из TS-прототипа — лаборатория замера лага `src/lib/engine/lag.ts` — **полностью перенесен в основной Python-стек**:
   - [`scripts/analyze_hypotheses.py`](../scripts/analyze_hypotheses.py) (`compute_lag_study` + строгий биномиальный тест, 95% доверительный интервал Уилсона и фильтр `N >= 100`)
   - Эндпоинт `GET /api/hypotheses_stats` в [`dashboard/main.py`](../dashboard/main.py) и вкладка **`⚡ 5/15m Полигон`** в основном дашборде (`http://localhost:8000`).

Чтобы исключить риск случайного запуска непроверенного TS-дашборда, обе папки перенесены в локальный архив `backups/legacy_ts_prototype/`. Единственный рабочий стек проекта — **Python (`launcher.py` / `run.py` / `dashboard/main.py`)**.
