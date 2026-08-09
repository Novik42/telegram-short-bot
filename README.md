# Margin Anomaly Monitor

Асинхронний Python-сервіс для дослідницького збору snapshot-ів маржинальних
Borrow/Repay та незалежних публічних ринкових даних Binance. Проєкт не відкриває
угод, не приймає торгових API-ключів і не використовує браузерну автоматизацію.

## Поточний обсяг: живий BBM + Binance + Telegram monitoring

Реалізовано:

- конфігурацію через Pydantic Settings і `.env`;
- повну початкову PostgreSQL-схему та Alembic migration;
- `BorrowDataProvider`, живий `BbmBorrowProvider` і fixture replay для тестів;
- server-rendered HTML-збір із `https://bbm.iflint.pro/` без браузерної автоматизації;
- дедуплікацію за timestamp BBM, перевірку свіжості та fail-closed при зміні HTML;
- незалежний `BinanceMarketDataProvider` на публічних Spot endpoint-ах;
- batch-запити price та 24h ticker, кеш exchange info, semaphore, retry/backoff і 429;
- перевірку існування `ASSETUSDT` до запитів candles;
- транзакційний collector з deduplication Borrow snapshot-ів;
- asyncio scheduler із `max_instances=1`;
- JSON-логування, Dockerfile, Docker Compose та unit tests.
- 5m candles, ΔBOR-вікна 5/15/30/60/240/1440 хвилин і anomaly score;
- точний час бази, першого BOR-стрибка та підтвердження другим snapshot-ом;
- price/volume context і сценарії `NO_PUMP`, `DURING_PUMP_BORROW`,
  `POST_PUMP_BORROW`;
- Telegram `/start`, `/status`, `/recent`, `/stats` та автоматичні anomaly alerts;
- автоматичну оцінку кожного живого сигналу через 15 хв, 1 год, 4 год і 24 год;
- збереження return, максимального руху на користь short (MFE), максимального руху
  проти short (MAE) та часу до локальних min/max.

Ще не реалізовано: generic JSON/manual Borrow providers, Futures provider та
статистичне переналаштування порогів на достатній вибірці. Поточне живе
BOR/REP-джерело — BBM.

BBM формує новий server-side snapshot приблизно раз на три хвилини. Сервіс опитує
сторінку кожні 30 секунд, але обробляє лише новий source timestamp. Тому типовий час
виявлення нового кадру — до 30 секунд після його публікації, а точність часу самої
аномалії обмежена інтервалом між двома BBM snapshot-ами.

## Структура

```text
margin-anomaly-monitor/
├── app/
│   ├── config.py, logging.py, runtime.py, main.py, cli.py
│   ├── models/          # SQLAlchemy 2.x models
│   ├── providers/       # ABC, BBM HTML, Fixture і Binance Spot
│   ├── repositories/    # PostgreSQL persistence
│   ├── services/        # Collector
│   └── utils/
├── migrations/versions/0001_initial_schema.py
├── fixtures/borrow_snapshots.json
├── tests/unit/
├── .env.example
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Схема бази

| Таблиця | Призначення | Ключова дедуплікація/індекс |
|---|---|---|
| `borrow_snapshots` | BOR/REP snapshot-и | unique `(source_name, symbol, source_timestamp)` |
| `market_snapshots` | ціна й 24h market metrics | index `(symbol, captured_at)` |
| `candles` | OHLCV | unique `(symbol, market_type, interval, open_time)` |
| `anomaly_events` | події наступного етапу | index `(symbol, detected_at)` |
| `event_outcomes` | реакції ціни через 15m/1h/4h/24h | unique `(anomaly_event_id, horizon_minutes)` |
| `notification_log` | журнал майбутніх сповіщень | index `(anomaly_event_id, sent_at)` |

Усі часові колонки мають `timezone=True`; application timestamps нормалізуються в UTC.

## Швидкий запуск через Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Контейнер застосунку очікує readiness PostgreSQL, запускає `alembic upgrade head`,
виконує перший збір одразу, а далі — кожні 5 хвилин. Для безперервного
прискореного replay запустіть:

```bash
docker compose run --rm app sh -c \
  "alembic upgrade head && python -m app.cli replay-fixture"
```

Fixture provider зберігає відносні інтервали timestamp-ів;
`FIXTURE_REPLAY_SPEED=60` перетворює 5 хвилин на 5 секунд.

Для одноразового циклу:

```bash
docker compose run --rm app sh -c "alembic upgrade head && python -m app.cli collect-once"
```

## Локальна розробка

Потрібен Python 3.12+ і доступний PostgreSQL.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# Для запуску з host замініть у DATABASE_URL hostname postgres на localhost.
alembic upgrade head
python -m app.cli collect-once
pytest -q
```

На старішому Mac без Docker для локального fixture-тесту можна використати SQLite,
не змінюючи PostgreSQL-конфігурацію Docker Compose:

```bash
DATABASE_URL=sqlite+aiosqlite:///./margin_monitor.db python -m app.cli init-db
DATABASE_URL=sqlite+aiosqlite:///./margin_monitor.db python -m app.main
```

SQLite є лише локальним режимом перевірки. PostgreSQL залишається цільовою базою
для контейнерного та production-подібного запуску.

Для живого локального моніторингу `.env` має містити:

```dotenv
BORROW_PROVIDER=html
BORROW_HTML_URL=https://bbm.iflint.pro/
COLLECTION_INTERVAL_SECONDS=30
```

Запуск:

```bash
.venv/bin/python -m app.main
```

Перший BBM snapshot створює базову лінію. Починаючи з наступного оновлення сервіс
рахує ΔBOR за 3/5/15/30/60/240/1440 хвилин. Екстремальний одиничний стрибок може
бути повідомлений одразу; звичайний сигнал підтверджується наступним snapshot-ом.

Після сигналу основний цикл автоматично заповнює `event_outcomes`. Поточне
дослідницьке правило успіху short-сценарію: MFE не менше 2% у межах 15m/1h/4h
(5% для 24h) і MAE не більше 4%. Команда `/stats` показує накопичену статистику;
правило є стартовим і має бути перевірене на достатній вибірці.

Примусовий одноразовий перерахунок усіх результатів, строк яких уже настав:

```bash
.venv/bin/python -m app.cli evaluate-outcomes
```

## Render

`render.yaml` описує production-схему з Docker Background Worker у Frankfurt та
керованою PostgreSQL базою. Токен Telegram і chat id позначені `sync: false` та
вводяться лише в Render Dashboard. Звичайний Render connection string автоматично
перетворюється застосунком на `postgresql+asyncpg://`.

Перед запуском у Render код має бути в Git-репозиторії. Під час перенесення треба
зупинити локальний процес: Telegram `getUpdates` не повинен одночасно працювати у
двох копіях. Blueprint використовує платні мінімальні плани `starter` worker і
`basic-256mb` Postgres; створення ресурсів слід підтвердити в Dashboard після
перевірки актуальної вартості.

## Приклад результату

Перший fixture frame дає такі BOR/REP записи:

```text
source_timestamp          source   symbol  borrow_usd  repay_usd  ratio
2026-08-09T06:40:00+00:00 fixture  KAITO   10500       805.6      13.033764...
2026-08-09T06:40:00+00:00 fixture  LISTA   85000       22000      3.863636...
```

Перевірка в PostgreSQL:

```sql
SELECT source_timestamp, source_name, symbol,
       borrow_usd, repay_usd, borrow_repay_ratio
FROM borrow_snapshots
ORDER BY source_timestamp, symbol;
```

Після Borrow commit колектор окремо отримує batch `ticker/price` і `ticker/24hr`.
Якщо Binance недоступний, вже отримані реальні Borrow snapshot-и не втрачаються;
помилка ринку повертається у `CollectionResult` та JSON-лог.

## Безпека

- Не записуйте секрети у `.env.example` або Git.
- Telegram інтеграція починається лише в Етапі 3; токен Етапу 1 не потрібен.
- Binance provider не має параметрів для API key/secret і використовує лише public API.
- Немає Playwright, Selenium, CAPTCHA bypass або торгових операцій.

Порогові значення в `.env.example` є лише стартовими дослідницькими параметрами,
а не перевіреною торговою стратегією.
