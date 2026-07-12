# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

NextFinUp: a Django app that runs an automated pipeline for Korean stock markets (KOSPI/KOSDAQ) —
sync ticker master data → collect 10y daily OHLCV via yfinance → train per-stock RandomForest
models to predict next-day close/direction/5-day return → scrape finance news and match it to
tickers → auto-publish a combined AI writeup to a Tistory blog. A single dashboard view renders
the latest prediction + news per active stock.

There is one Django app, `articles`, containing all models/views/commands. There is no
`requirements.txt` — installed packages live only in `venv/` (see Environment below).

## Commands

Activate the venv first (or prefix commands with `venv/bin/`):
```
source venv/bin/activate
```

Run the dev server:
```
python manage.py runserver
```

Run tests (Django's test runner; `articles/tests.py` is currently empty scaffolding):
```
python manage.py test
python manage.py test articles.tests.<TestClass>.<test_method>   # single test
```

Migrations:
```
python manage.py makemigrations articles
python manage.py migrate
```

Data pipeline management commands (run in this order for a fresh setup):
```
python manage.py insert_stock_master                                       # load all KOSPI/KOSDAQ tickers via FinanceDataReader
python manage.py sync_index_membership --kospi200 <csv> --kosdaq150 <csv>  # flag is_major_index from KRX-exported CSVs
python manage.py collect_stock_data                                        # bulk-fetch 10y daily bars for is_major_index stocks via yfinance
python manage.py run_stock_prediction                                      # train RandomForest per active stock, write predictions
python manage.py scraped_ai_news                                           # pull 한국경제/매일경제 RSS, match tickers, create AnalyzedArticle rows
python manage.py post_to_tistory                                           # publish unposted AnalyzedArticle entries to Tistory
```
`collect_stock_data_back.py` is an earlier, superseded version of `collect_stock_data` (no
rate-limit backoff/retry) — kept in the tree but not part of the intended pipeline.

Dump/export current package versions (no requirements.txt exists to diff against):
```
venv/bin/pip freeze
```

## Architecture

- `config/` — Django project (settings, root urls, wsgi/asgi). Single app `articles` is installed.
- `articles/models.py` — all domain models:
  - `StockItem` — ticker master (`market_type`: KOSPI/KOSDAQ, `is_active` gates most pipeline
    commands, `is_major_index` gates `collect_stock_data`/prediction scope to KOSPI200/KOSDAQ150).
  - `StockPrediction` — one row per `(stock, date)` (enforced via `unique_together`): raw OHLCV
    plus ML output columns (`pred_next_close`, `pred_5day_return`, `up_probability`,
    `down_probability`, `trading_signal`). Populated first by `collect_stock_data` (prices only),
    then updated in place by `run_stock_prediction` (ML fields).
  - `AnalyzedArticle` — **defined twice in this file** (lines ~4 and ~85); the second definition
    (with the `stock` FK, `applied_template`, etc.) is the one Python actually binds and the one
    used throughout the codebase and admin. Be aware of this when editing — changes to the first
    definition are dead code.
  - `UserSubscription` — 1:1 with Django `User`, tracks premium subscription state; not yet wired
    into any view/command.
- `articles/views.py` — single `main_dashboard_view`: for each active `StockItem`, joins its
  latest `StockPrediction` and latest `AnalyzedArticle`, renders `articles/templates/articles/dashboard.html`.
- `articles/management/commands/` — the actual pipeline logic lives here, not in views/models.
  Each command is a standalone, idempotent step intended to be run on a schedule (e.g. cron):
  data collection is dedup'd against existing DB rows (`existing_dates`, `original_url` uniqueness),
  so commands are safe to re-run.
- Text in models/commands/comments is largely Korean, matching the target market/audience.

## Environment / config notes

- `config/settings.py` has `DEBUG = True` and a hardcoded `SECRET_KEY` plus a hardcoded MySQL
  password — this is dev-only configuration checked into the file directly, not read from env
  vars. Treat any change here as touching production credentials; don't casually commit new
  secrets alongside it.
- Database is MySQL (`nextfinup_db` via `mysqlclient`), not the commented-out sqlite block. The
  `db.sqlite3` file in the repo root is stale/unused given this config.
- `post_to_tistory.py` has `TISTORY_ACCESS_TOKEN`/`TISTORY_BLOG_NAME` as placeholder literals at
  the top of the command — until real values are set, the command runs in a simulation mode that
  still marks articles `is_posted = True` without actually publishing.
- `collect_stock_data` intentionally rate-limits itself (randomized sleep between tickers,
  exponential backoff on failure, abort after N consecutive failures) to avoid yfinance/Yahoo
  IP blocks — preserve this behavior if you touch that command.
- `sync_index_membership` expects KRX-exported CSVs in EUC-KR encoding (see the
  `data_1931_*.csv` / `data_2000_*.csv` files in the repo root as examples).
