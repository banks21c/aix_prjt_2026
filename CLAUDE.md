# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

NextFinUp: a Django site built around an automated pipeline for Korean stock markets
(KOSPI/KOSDAQ) — sync ticker master data → collect 10y daily OHLCV via yfinance → train
per-stock RandomForest models to predict next-day close/direction/5-day return → scrape/collect
finance news and match it to tickers → AI-summarize it → let members auto- or manually-publish a
combined writeup to their own blogs (WordPress/Tistory/Blogger/Naver). On top of the pipeline
there's a member site: email+social (Kakao/Google/Naver) login, per-user preferences and daily
usage limits by member grade, a news board with manual scrape/edit, a stock detail page with
day/minute charts, a KIS-powered realtime ranking/index feed, an OpenAI-backed chatbot widget,
and a daily email newsletter.

There is one Django app, `articles`, containing all models/views/commands. Dependencies are
pinned in `requirements.txt` (generated via `pip freeze` from `venv/`).

## Commands

Activate the venv first (or prefix commands with `venv/bin/`):
```
source venv/bin/activate
```

Copy `.env.example` to `.env` and fill in values before running anything — `config/settings.py`
loads all secrets/config from `.env` via `python-dotenv`; there are no working hardcoded defaults
for `SECRET_KEY`/`DB_PASSWORD`/etc.

Run the dev server:
```
python manage.py runserver
```

Run tests (Django's test runner; `articles/tests.py` covers auth flow, core public views, and
grade-based daily-limit logic — not yet the pipeline commands or OAuth views, see file header):
```
python manage.py test
python manage.py test articles.tests.<TestClass>.<test_method>   # single test
```
`.github/workflows/ci.yml` runs the same `manage.py test` on every push/PR against a `mysql:8.0`
service container (not sqlite — the app is MySQL-only, see below). Requires a MySQL user with
`CREATE`/`DROP` privileges on `test_<DB_NAME>` to let Django spin up the ephemeral test DB (the
workflow uses the container's root user for this).

Migrations:
```
python manage.py makemigrations articles
python manage.py migrate
```

### Pipeline management commands

One-time / occasional setup (run in this order for a fresh setup):
```
python manage.py insert_stock_master                                       # load all KOSPI/KOSDAQ tickers via FinanceDataReader
python manage.py sync_index_membership --kospi200 <csv> --kosdaq150 <csv>  # flag is_major_index from KRX-exported CSVs
python manage.py collect_stock_data                                        # bulk-fetch 10y daily bars for is_major_index stocks via yfinance
python manage.py run_stock_prediction                                      # train RandomForest per active stock, write predictions
```
`collect_stock_data_back.py` is an earlier, superseded version of `collect_stock_data` (no
rate-limit backoff/retry) — kept in the tree but not part of the intended pipeline.

Recurring jobs are defined in `deploy/crontab` (source of truth, checked into the repo — the
live server crontab can drift if edited directly with `crontab -e`; see the file header for how
to sync in either direction). Also viewable read-only at `/admin-tools/cron/` via
`cron_status_view`:
```
*/5 * * * *  collect_keyword_news            # RSS scrape by NewsKeyword, AI-summarize, create AnalyzedArticle rows
*/5 * * * *  collect_market_index            # KOSPI/KOSDAQ index snapshot (MarketIndex)
*/5 * * * *  collect_kis_news                # 종합 시황/공시 headlines via KIS API
*/5 * * * *  collect_stock_realtime_price    # KIS realtime price/ranking (StockRealtimePrice, RankedMover)
0  8 * * *   generate_newsletter_draft       # build the day's NewsletterIssue draft
0 18 * * *   send_newsletter                 # email the draft to NewsletterSubscriber list
```
`collect_stock_data` and `run_stock_prediction` are heavy (full yfinance re-pull / model
retraining) and are run manually/less frequently, not on the 5-minute cron cadence.

Publishing (per-user, driven by each member's `BlogPostingAccount` + `UserPreference`, not global
config — see Architecture):
```
python manage.py post_to_tistory     # real Tistory Open API publish
python manage.py post_to_wordpress   # real WordPress REST API publish
python manage.py post_to_blogger     # real Blogger v3 API publish (OAuth refresh token per account)
python manage.py post_to_naver       # simulation only — Naver has no public blog-post API, builds content but never actually posts
```
Members can also publish manually/selectively from the news board (`post_articles_view`,
`news/post/`) using the same `articles/blog_posting.py` logic.

Legacy/one-off collectors not on the cron schedule: `collect_fluctuation_ranking.py`,
`scraped_ai_news.py` (original 한국경제/매일경제 RSS-only collector, superseded by
`collect_keyword_news` for most flows but still functional).

Install/refresh dependencies:
```
venv/bin/pip install -r requirements.txt
venv/bin/pip freeze > requirements.txt   # after installing/upgrading a package, re-pin
```

## Architecture

- `config/` — Django project (settings, root urls, wsgi/asgi). Single app `articles` is installed.
- `articles/models/` — package of domain models, split by group (was a single `models.py`; every
  name is re-exported from `articles/models/__init__.py` so `from .models import X` /
  `from articles.models import X` work unchanged everywhere else in the codebase):
  - **`market.py`**: `StockItem` (ticker master; `is_active` gates most pipeline commands,
    `is_major_index` gates `collect_stock_data`/prediction scope to KOSPI200/KOSDAQ150),
    `StockPrediction` (one row per `(stock, date)`, unique together; raw OHLCV populated by
    `collect_stock_data`, then ML fields — `pred_next_close`, `pred_5day_return`,
    `up_probability`, `down_probability`, `trading_signal` — updated in place by
    `run_stock_prediction`), `MarketIndex`, `MarketHoliday`, `KisAccessToken` (cached KIS OAuth
    token, see `kis_client.get_access_token`), `RankedMover`, `StockRealtimePrice`.
  - **`news.py`**: `NewsSource`, `NewsKeyword`, `AnalyzedArticle` (scraped article + AI
    summary/analysis/blog draft + optional `stock`/`matched_keyword` FK; `scraped_by` is set only
    for member-submitted URLs and drives per-grade daily scrape limits), `PostedArticle` (records
    of what's been published where, keyed by `(blog_account, article)` to prevent double-posting;
    imports `StockItem` from `.market` and `BlogPostingAccount` from `.members`).
  - **`members.py`**: Django `User` plus `UserSubscription` (premium flag), `MemberGrade`
    (admin-defined tiers with `daily_scrape_limit`/`daily_post_limit`, NULL = unlimited),
    `UserPreference` (1:1, holds `interested_keywords`/`post_all_articles`/
    `auto_posting_enabled`/grade FK), `BlogPostingAccount` (per-user, per-platform credentials —
    WordPress/Tistory/Blogger/Naver; replaces the old single global blog config),
    `SocialAccount` (Kakao/Google/Naver login links), `LoginLog`, `MenuAccessLog` (written by
    `MenuAccessLogMiddleware`), `ChatMessage`.
  - **`content.py`**: `NewsletterSubscriber`, `NewsletterIssue`, `Menu` (admin-editable nav,
    surfaced via `context_processors.menu_items`), `ConsultRequest`, `FinancialConsultSheet`.
- `articles/views/` — package of view functions, split by domain (was a single `views.py`; every
  name `config/urls.py` imports is re-exported from `articles/views/__init__.py`, so `urls.py`
  needed no changes):
  - **`public.py`**: `landing_page_view`, `main_dashboard_view` (+ `_build_index_chart`),
    newsletter subscribe/unsubscribe, privacy/terms/insurance static pages, `consult_request_view`,
    `header_fragment_view`.
  - **`admin_tools.py`**: `cron_status_view` (staff-only, reads live crontab), the financial
    consult sheet view/save pair.
  - **`news.py`**: news board CRUD — `news_board_view`, `news_detail_view`, `news_scrape_view`
    (member URL-submit → scrape → AI draft), `news_edit_view` (staff or original-submitter only),
    `post_articles_view` (manual publish).
  - **`stocks.py`**: `stock_detail_view` + `stock_minute_chart_view`/
    `market_index_minute_chart_view` on-demand chart APIs.
  - **`chatbot.py`**: `chatbot_ask_view`.
  - **`auth.py`**: signup/login/logout/delete-account/email-verify plus `kakao_*`/`google_*`/
    `naver_*` login+callback pairs, and the shared `_log_login`/`_get_or_create_social_user`
    helpers.
  - **`mypage.py`**: `my_page_view` (preferences, blog account connections) and
    `blogger_connect_view`/`blogger_callback_view` OAuth (Blogger auto-posting connect, distinct
    from the Google *login* flow in `auth.py`).
- `articles/management/commands/` — the actual pipeline/collector logic lives here, not in
  views/models. Each command is a standalone, idempotent step intended to run on a schedule
  (dedup'd against existing DB rows — `existing_dates`, `original_url` uniqueness — so safe to
  re-run).
- `articles/ml/features.py` — the single source of truth for feature engineering
  (`add_features_for_one_stock`: returns, MA gaps, RSI, MACD, Bollinger %, volume ratio) and
  labels (`target_next_return`, `target_5day_return`, `target_up`). Both `run_stock_prediction`
  and the stock detail page's indicator display (`compute_display_indicators`) import from here
  so train-time and inference-time/display-time calculations never drift apart.
  `build_feature_dataframe_for_stock` processes one stock at a time by design (not all ~350
  stocks joined into one DataFrame) to keep peak memory bounded on a small server.
- `articles/blog_posting.py` — shared logic between the `post_to_*` cron commands and the manual
  "publish from news board" view: `select_candidates` (per-account, filtered by
  `UserPreference.interested_keywords`/`post_all_articles`), `build_post_content` (merges
  `AnalyzedArticle` + latest `StockPrediction` into one HTML post), and one `publish_to_*`
  function per platform dispatched via the `PUBLISHERS` dict.
- `articles/kis_client.py` — 한국투자증권 (KIS) Open API client: OAuth token caching
  (`KisAccessToken`, refreshed only when near expiry per KIS's 1-token-per-day policy),
  fluctuation ranking, used by the realtime collector commands.
- `articles/article_ai.py` / `articles/chatbot_client.py` — both wrap OpenAI calls with the same
  fallback pattern: if `OPENAI_API_KEY` is unset or still the placeholder
  `YOUR_OPENAI_API_KEY_HERE`, return a canned "시뮬레이션 모드" response instead of calling the
  API, so scrape→draft and the chatbot widget keep working end-to-end without a real key.
- `articles/middleware.py` — `MenuAccessLogMiddleware` logs one `MenuAccessLog` row per GET to
  URL names listed in `MENU_URL_NAMES`; add new trackable pages there.
- `articles/context_processors.py` — `menu_items` feeds the admin-editable `Menu` model into
  every template's nav.
- Text in models/commands/comments is largely Korean, matching the target market/audience.

## Environment / config notes

- `config/settings.py` loads everything from `.env` via `python-dotenv` (`SECRET_KEY`, DB
  creds, `DEBUG`, Kakao/Google/Naver OAuth keys, `OPENAI_API_KEY`, `KIS_APP_KEY`/`KIS_APP_SECRET`,
  Gmail SMTP creds). `DEBUG` defaults to `False` if unset — safe-by-default, opposite of Django's
  usual scaffold. Copy `.env.example` to get the full key list; `.env` itself is gitignored.
- Database is MySQL (`nextfinup_db` via `mysqlclient`), not the commented-out sqlite block. The
  `db.sqlite3` file in the repo root is stale/unused given this config.
- `AUTHENTICATION_BACKENDS` is overridden to `AllowAllUsersModelBackend` specifically so
  `LoginForm.confirm_login_allowed()` runs even for `is_active=False` accounts, letting it show a
  precise "email not verified" message instead of Django's default silent-reject "wrong
  credentials" behavior.
- Blog publishing credentials are no longer global/hardcoded — each member registers their own
  `BlogPostingAccount` (per platform) from `/mypage/`; `post_to_naver` remains simulation-only
  because Naver has no public blog-posting API.
- `collect_stock_data` intentionally rate-limits itself (randomized sleep between tickers,
  exponential backoff on failure, abort after N consecutive failures) to avoid yfinance/Yahoo IP
  blocks — preserve this behavior if you touch that command.
- `sync_index_membership` expects KRX-exported CSVs in EUC-KR encoding (see the
  `data_1931_*.csv` / `data_2000_*.csv` files in the repo root as examples).
- KIS access tokens are capped at roughly one issuance per day by KIS policy —
  `kis_client.get_access_token` caches in `KisAccessToken` and only refreshes within
  `EXPIRY_BUFFER` of expiry; don't add code paths that force-refresh on every call.
