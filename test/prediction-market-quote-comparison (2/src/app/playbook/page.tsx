import Link from "next/link";

const CARD = "rounded-xl border border-slate-800 bg-slate-900/60 p-5";
const H = "text-sm font-semibold tracking-wide text-slate-100 uppercase";
const LI = "text-sm leading-relaxed text-slate-300";
const CODE = "font-mono text-[12px] text-emerald-300";

export default function PlaybookPage() {
  return (
    <div className="space-y-5">
      <div className={`${CARD}`}>
        <h1 className="text-lg font-semibold text-slate-50">Плейбук: что докручивать и в каком порядке</h1>
        <p className="mt-2 text-sm text-slate-400">
          Это рабочая памятка к скелету. Всё, что помечено «сделано», уже работает в этом приложении — можно смотреть
          на дашборде и в API. Остальное — конкретные шаги с приоритетами.
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div className={CARD}>
          <h2 className={H}>1. Что уже есть в скелете</h2>
          <ul className="mt-3 space-y-2">
            <li className={LI}>
              ✅ <b>Collectors</b>: Polymarket через публичный Gamma API (честно, без обхода); универсальный
              CEX-парсер для Bybit Odds / MEXC / OKX / Gate с перебором эндпоинтов-кандидатов и маппингом полей по
              синонимам; симулятор как fallback, чтобы pipeline не простаивал.
            </li>
            <li className={LI}>
              ✅ <b>Унифицированный формат</b>: <span className={CODE}>platform, market_id, raw_title, outcome,
              market_type (fixed_odds|continuous), odds, implied_probability, expiry, window_start, strike, asset,
              resolution_source, fetched_at, raw</span>.
            </li>
            <li className={LI}>
              ✅ <b>Normalizer</b>: крипта матчится программно по (актив, тип, страйк, окно, экспирация), спорт —
              вручную через БД (аналог <span className={CODE}>event_map.json</span>).
            </li>
            <li className={LI}>
              ✅ <b>Engine</b>: единая Dutch-book математика <span className={CODE}>edge = 1 − (p_A + p_B)</span>,
              где <span className={CODE}>p_fixed = 1/O</span>; вычет комиссий и слиппеджа в bps; сплит ставки с
              равной выплатой; предупреждения про фиксированные коэффициенты и разные оракулы; настраиваемый порог.
            </li>
            <li className={LI}>
              ✅ <b>История</b>: пишутся ВСЕ тики в <span className={CODE}>price_history</span> (не только вилки) —
              база под бэктесты раздела 4.5.
            </li>
            <li className={LI}>
              ✅ <b>Лаборатория лага</b>: оценка задержки обновления коэффициента относительно движения спота —
              дешёвая проверка паттерна 4.4 перед вложениями в low-latency.
            </li>
          </ul>
        </div>

        <div className={CARD}>
          <h2 className={H}>2. Советы по парсингу (самое ценное)</h2>
          <ul className="mt-3 space-y-2">
            <li className={LI}>
              <b>Справочники отдельно от цен.</b> Список рынков/контрактов меняется редко — тянуть раз в 5–15 мин.
              Цены — чаще. Это сразу −90% трафика и меньше риск rate-limit.
            </li>
            <li className={LI}>
              <b>WebSocket &gt; polling.</b> Для 5/15-мин контрактов коэффициент «живой». В DevTools: Network → WS →
              Messages → посмотри исходящее сообщение подписки. Его и повторяешь (у Bybit может быть
              <span className={CODE}> ws-api / trade </span>канал с подписью).
            </li>
            <li className={LI}>
              <b>Приоритет проверки заголовков:</b> <span className={CODE}>Origin / Referer</span>, затем cookie
              (<span className={CODE}>device_id, utm</span>), затем CSRF-токен, и только в конце — подпись
              (<span className={CODE}>X-BA-SIGNATURE</span>). Если запрос подписан — не реверси сигнатуру сразу,
              сначала посмотри, не отдаёт ли тот же фид соседний незащищённый маршрут.
            </li>
            <li className={LI}>
              <b>Не матчить по названию.</b> Названия переписывают; матчи по{" "}
              <span className={CODE}>conditionId / market_id / symbol</span>. Название — только для человека.
            </li>
            <li className={LI}>
              <b>Время — только в UTC</b> и только из поля ответа, не из текста. Для 5-мин окон сверяй{" "}
              <span className={CODE}>window_start</span> секунда в секунду: если окна не совпадают, это не одно
              событие (раздел 8.1).
            </li>
            <li className={LI}>
              <b>Числа: чистить локаль.</b> «1 234,56», «$92.5K», «⩾» — парсить регуляркой и сразу приводить к float;
              хранить исходную строку в <span className={CODE}>raw</span>, чтобы можно было переразобрать.
            </li>
            <li className={LI}>
              <b>Валидация схемы + алерт «0 рынков».</b> Сайт поменял формат → collector отдаёт 0 → это должен быть
              видимый статус на дашборде (уже реализовано), а не тихий пустой массив. Плюс хранить сырой ответ
              последнего запроса — видно, что именно сломалось.
            </li>
            <li className={LI}>
              <b>Джиттер и backoff.</b> Один инстанс на IP, интервал 5–15 с + случайная добавка, после ошибки —
              пауза (реализовано: 10 мин), иначе ловится капча и бан.
            </li>
            <li className={LI}>
              <b>Headless-браузер — последний вариант.</b> Только если данные рендерятся SSR без XHR. Тогда один
              Playwright-инстанс держит страницу открытой и слушает WS/перехватывает ответы, а не «открывает заново»
              на каждый опрос.
            </li>
          </ul>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div className={CARD}>
          <h2 className={H}>3. Матчинг событий</h2>
          <ul className="mt-3 space-y-2">
            <li className={LI}>
              Авто-ключ уже таков, что направление НЕ входит в ключ (направление — свойство «ноги»). Это позволяет
              сравнивать YES одной площадки с NO другой — иначе половина вилок теряется.
            </li>
            <li className={LI}>
              Проверка оракула: если <span className={CODE}>resolution_source</span> различается, движок ставит
              вердикт <span className={CODE}>phantom_oracle</span>. Правило: авто-матчить можно только при одинаковом
              индексе цены и совпадающем времени; иначе — только вручную.
            </li>
            <li className={LI}>
              Fuzzy-match (позже): нормализация текста → токены → скор по пересечению + обязательное совпадение
              даты/страйка. Никогда не матчать автоматически при расхождении даты хотя бы на день.
            </li>
            <li className={LI}>
              Range-контракты: не маппить 1:1. Вне/внутри диапазона = две позиции на continuous-площадке. После MVP.
            </li>
          </ul>
        </div>

        <div className={CARD}>
          <h2 className={H}>4. Engine: что докрутить</h2>
          <ul className="mt-3 space-y-2">
            <li className={LI}>
              Комиссии: у CEX часто «fee от ставки» или «fee с выигрыша» — это разные формулы. Проверь в правилах и
              выставь <span className={CODE}>feeBps</span> в реестре площадок (редактируется через API).
            </li>
            <li className={LI}>
              Ликвидность: спред бессмыслен, если в стакане нет $500. Добавить фильтр по{" "}
              <span className={CODE}>liquidity</span> и по максимальному размеру заявки — иначе ловим «вилки» на
              $20.
            </li>
            <li className={LI}>
              Синхронность: замер отбрасывается, если возраст котировок различается больше{" "}
              <span className={CODE}>maxQuoteAgeDeltaMs</span> (реализовано). Для 5-мин окон лучше 1000–2000 мс.
            </li>
            <li className={LI}>
              Позже: bid/ask вместо mid, частичное исполнение, Kelly-подсказка для EV-ставок (не для арбитража).
            </li>
          </ul>
        </div>
      </div>

      <div className={CARD}>
        <h2 className={H}>5. Какие данные нужны от тебя, чтобы понять следующий шаг</h2>
        <p className="mt-2 text-sm text-slate-400">
          Чем больше пунктов ниже ты пришлёшь, тем точнее я скажу, что писать дальше. Минимум для старта — пункты 1–4.
        </p>
        <ol className="mt-3 space-y-2">
          <li className={LI}>
            <b>1. Network-дамп Bybit Odds</b> (самое важное): открыть{" "}
            <span className={CODE}>bybit.com/.../trade/odds/</span>, DevTools → Network → фильтр Fetch/XHR и WS, и
            прислать для 2–3 запросов: URL, method, request headers, request body, и кусок JSON-ответа (можно
            скриншотом). Отдельно — есть ли WS-канал и что он присылает.
          </li>
          <li className={LI}>
            <b>2. То же для MEXC Prediction</b> (<span className={CODE}>prediction.mexc.com/prediction-markets/up-down</span>)
            и, если хочешь, OKX/Gate — по одному запросу достаточно.
          </li>
          <li className={LI}>
            <b>3. Три ответа по каждому CEX-продукту:</b> (а) какой ценовой индекс объявлен в правилах/UI (mark price
            от какого фида); (б) в какую секунду стартует и заканчивается окно 5/15-мин (совпадает ли между
            площадками); (в) как считается комиссия и есть ли комиссия на выход.
          </li>
          <li className={LI}>
            <b>4. Стакан/ликвидность:</b> на какой максимальный размер реально можно зайти в коэффициент (скрин
            стакана или формы ставки). Без этого любые «вилки» — теоретические.
          </li>
          <li className={LI}>
            <b>5. Ручной список эквивалентных событий</b> — 5–10 пар «Bybit market_id ↔ Polymarket market_id», которые
            ты сам считаешь одним событием (лучше из раздела Target-по-дате). Я залью их в{" "}
            <span className={CODE}>event_matches</span>.
          </li>
          <li className={LI}>
            <b>6. Лог лага</b> (если уже копил): как часто дёргается коэффициент Bybit при движении BTC. Достаточно
            записи на 10–20 минут: время тика BTC и время изменения коэффициента. Дашборд{" "}
            <Link className="text-sky-400" href="/lag">
              /lag
            </Link>{" "}
            делает это сам, пока открыт авто-цикл.
          </li>
          <li className={LI}>
            <b>7. Твои издержки:</b> VIP-уровень/скидки по комиссиям, вывод/депозит, лимиты ставки, валюты расчёта.
          </li>
          <li className={LI}>
            <b>8. Цель:</b> чистый арбитраж (гарантированный плюс) или EV-ставки (статистический эдж)? От этого
            зависит, строить ли хедж-логику или бэктест-модуль.
          </li>
        </ol>
      </div>

      <div className={CARD}>
        <h2 className={H}>6. Порядок работ (моё предложение)</h2>
        <ol className="mt-3 space-y-2">
          <li className={LI}>
            <b>Шаг 1 (сейчас):</b> запустить сбор и копить историю — Polymarket live + симулятор. Уже работает: дашборд
            пишет тики в <span className={CODE}>price_history</span> и вилки в <span className={CODE}>spreads</span>.
          </li>
          <li className={LI}>
            <b>Шаг 2:</b> прислать Network-дампы Bybit/MEXC → я подставляю реальные эндпоинты и синонимы полей в
            CEX-collector (проверка кнопкой «run» + <span className={CODE}>/api/probe</span>).
          </li>
          <li className={LI}>
            <b>Шаг 3:</b> ручной матчинг 5–10 событий Target-по-дате (Bybit ↔ Polymarket) — это единственная пара с
            совпадающей структурой контракта на старте.
          </li>
          <li className={LI}>
            <b>Шаг 4:</b> 1–2 дня без автотрейда. Смотрим: сколько вилок после комиссий, какая медиана, сколько из них
            <span className={CODE}> phantom_oracle</span>. Это и есть решение «идти ли дальше».
          </li>
          <li className={LI}>
            <b>Шаг 5:</b> параллельно копим лаг-сэмплы. Если P90 &gt; 1–3 с — закладываем WebSocket-модуль; если нет —
            закрываем тему лага навсегда и экономим недели.
          </li>
          <li className={LI}>
            <b>Шаг 6 (только при подтверждённой статистике):</b> бэктест моментум-идеи на накопленных тиках и лишь
            потом автотрейд (с лимитами, kill-switch и логом каждой заявки).
          </li>
        </ol>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div className={CARD}>
          <h2 className={H}>7. Чего сознательно не делаем</h2>
          <ul className="mt-3 space-y-2">
            <li className={LI}>❌ Автотрейд до статистики — риск слить депозит на «фантомных» вилках из-за разных оракулов.</li>
            <li className={LI}>❌ Матчинг 5/15-мин окон между разными CEX, пока не подтверждены индекс и границы окна.</li>
            <li className={LI}>❌ Range-контракты как 1:1 хедж (нужно две позиции).</li>
            <li className={LI}>❌ Агрессивная маскировка под официальный клиент и частый polling — бан дороже данных.</li>
            <li className={LI}>❌ Обход ToS там, где есть публичный API (Polymarket/Kalshi) — там работаем честно.</li>
          </ul>
        </div>
        <div className={CARD}>
          <h2 className={H}>8. API скелета (для интеграций)</h2>
          <ul className="mt-3 space-y-1 font-mono text-[12px] text-slate-300">
            <li><span className="text-sky-300">POST /api/cycle</span> — один цикл collect → match → engine</li>
            <li><span className="text-sky-300">GET /api/overview</span> — снимок: настройки, collectors, вилки, тики</li>
            <li><span className="text-sky-300">GET /api/spreads?min=&verdict=</span> — история вилок</li>
            <li><span className="text-sky-300">GET /api/events</span> — события + ноги + несматченные</li>
            <li><span className="text-sky-300">POST /api/events</span> — ручной маппинг</li>
            <li><span className="text-sky-300">GET /api/history?eventKey=</span> — ряд вероятностей</li>
            <li><span className="text-sky-300">GET /api/lag</span> — сводка по лагу коэффициентов</li>
            <li><span className="text-sky-300">POST /api/collectors</span> — запуск/включение collector&apos;а</li>
            <li><span className="text-sky-300">POST /api/probe</span> — проверка гипотезы эндпоинта</li>
            <li><span className="text-sky-300">POST /api/settings</span> — пороги, фи, банк, интервалы</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
