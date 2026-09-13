# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

NextFinUp: a Django site built around an automated pipeline for Korean stock markets
(KOSPI/KOSDAQ) — sync ticker master data → collect 10y daily OHLCV via yfinance → train
per-stock RandomForest models to predict next-day close/direction/5-day return → scrape/collect
finance news and match it to tickers → AI-summarize it → let members auto- or manually-publish a
combined writeup to their own blogs (WordPress/Blogger). On top of the pipeline
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
python manage.py collect_stock_data                                        # fetch 10y daily bars (first run) for is_major_index stocks via yfinance
python manage.py run_stock_prediction                                      # train ensemble model per active stock, write predictions
```
`collect_stock_data_back.py` is an earlier, superseded version of `collect_stock_data` (no
rate-limit backoff/retry) — kept in the tree but not part of the intended pipeline.

Recurring jobs are defined in `deploy/crontab` (source of truth, checked into the repo — the
live server crontab can drift if edited directly with `crontab -e`; see the file header for how
to sync in either direction). Also viewable read-only at `/admin-tools/cron/` via
`cron_status_view`. **The server's system clock and Django's `TIME_ZONE` are both UTC, and cron
has no per-job TZ override, so every hour below is UTC — the comment on each line gives the
actual Korea-time (KST = UTC+9) equivalent that the hour was chosen to hit:**
```
*/5 * * * *  collect_keyword_news            # RSS scrape by NewsKeyword, AI-summarize, create AnalyzedArticle rows
*/5 * * * *  collect_market_index            # KOSPI/KOSDAQ index snapshot (MarketIndex)
*/5 * * * *  collect_kis_news                # 종합 시황/공시 headlines via KIS API
*/5 * * * *  collect_stock_realtime_price    # KIS realtime price (StockRealtimePrice)
*/5 * * * *  collect_fluctuation_ranking     # KIS 등락률 순위 (RankedMover; feeds dashboard 특징종목)
*/5 * * * *  collect_global_market_data      # KIS 해외지수/국제환율/금리 (GlobalMarketQuote; feeds header index ticker)
*/30 * * * * collect_exchange_rate_fixing    # 한국수출입은행 API 환전 고시 환율 (daily-quoted, so 30min not 5min)
0 17 * * *   collect_stock_data --all        # KST 02:00 — incremental (only new trading days) full-universe OHLCV pull
30 19 * * *  run_stock_prediction --all      # KST 04:30 — full-universe ensemble retrain, ~30min after collect_stock_data starts
0 4 * * 1-5  generate_featured_stock_briefing --session=midday  # KST 13:00 — AI 특징주 브리핑 (AnalyzedArticle, source_type=AI_BRIEFING)
40 6 * * 1-5 generate_featured_stock_briefing --session=close   # KST 15:40 — 마감 후 AI 특징주 브리핑
5  4 * * 1-5 post_to_wordpress --limit 1     # KST 13:05 — publish that session's newest ai_generated candidate per enabled account
5  4 * * 1-5 post_to_blogger --limit 1       # KST 13:05
45 6 * * 1-5 post_to_wordpress --limit 1     # KST 15:45
45 6 * * 1-5 post_to_blogger --limit 1       # KST 15:45
0 7 * * 1-5  generate_newsletter_draft       # KST 16:00 — dress that day's close-session briefing as a NewsletterIssue, status=READY (no manual review)
0 12 * * *   send_newsletter                 # KST 21:00 — email READY issues to NewsletterSubscriber list as HTML, mark SENT
0 1 * * 6    generate_weekly_market_briefing # KST Sat 10:00 — weekly recap (last 5 trading days' KOSPI/KOSDAQ change + weekly top movers among is_major_index stocks), AnalyzedArticle source_type=AI_BRIEFING
5 1 * * 6    post_to_wordpress --limit 1     # KST Sat 10:05
5 1 * * 6    post_to_blogger --limit 1       # KST Sat 10:05
```
`generate_newsletter_draft` depends on the close-session briefing already existing for the day — it
looks up `AnalyzedArticle` by the pseudo-URL `internal://featured-briefing/<date>/close` and skips
(no issue created) if that briefing hasn't run yet, so its cron time must stay after the close
briefing's (KST 15:40 / UTC 6:40).
`generate_weekly_market_briefing` has no holiday check (unlike `generate_featured_stock_briefing`)
since Saturday is always non-trading — it just reads the most recent 6 trading-day dates from
`MarketIndex`/`StockDailyPrice` (1 baseline day + the 5 trading days just completed), so it's
immune to which weekday holidays fell on. Writes `AnalyzedArticle` at the pseudo-URL
`internal://weekly-briefing/<date>`.
`collect_stock_data` defaults to `is_major_index=True` (350 stocks) and only requests, per stock,
the OHLCV since that stock's latest stored date — for a stock with existing data this now goes
through the KIS 국내주식기간별시세 API (`kis_client.get_stock_daily_price`), not yfinance;
yfinance is only used for a brand-new stock's initial full-history pull (or `--full`, which
forces every stock through that yfinance path regardless of existing data). This switch exists
because yfinance lags by days on many non-major-index KOSDAQ small-caps specifically (~1,500 of
~2,780 stocks were stuck days behind when checked on 2026-08-03) while KIS — the same API already
used for realtime price/ranking — stays current. So a daily `--all` run neither re-downloads
years of history it already has nor depends on yfinance's per-ticker freshness. `run_stock_prediction`
defaults the same way regarding `is_major_index`/`--all` (its own model-training logic is
unrelated to either data source); both accept `--all` to cover `is_active=True` instead (used by
the daily cron above and by the admin manual-trigger buttons at `/admin-tools/cron/`).

Publishing (per-user, driven by each member's `BlogPostingAccount` + `UserPreference`, not global
config — see Architecture). Live and publishes immediately (not draft):
```
python manage.py post_to_wordpress   # real WordPress REST API publish, publishes live (status=publish)
python manage.py post_to_blogger     # real Blogger v3 API publish (OAuth refresh token per account), publishes live (isDraft=false)
```
Both commands loop every `BlogPostingAccount` with `is_enabled=True` and
`UserPreference.auto_posting_enabled=True`, and publish `select_candidates()` (newest
`ai_generated=True` `AnalyzedArticle` not yet posted to that account, filtered by
`interested_keywords`/`post_all_articles`) — with no `--limit`, that means *every* unposted
matching backlog article, not just the newest one. On the twice-daily cron above, `--limit 1`
keeps each run to just that session's fresh featured-stock briefing.
Tistory and Naver are no longer supported publishing platforms — Tistory shut down its Open API
(post-write endpoint) entirely by February 2024, and Naver never had a public blog-posting API to
begin with. Both `post_to_tistory`/`post_to_naver` commands and the `TISTORY`/`NAVER`
`BlogPostingAccount.PLATFORM_CHOICES` options have been removed accordingly.
Members can also publish manually/selectively from the news board (`post_articles_view`,
`news/post/`) using the same `articles/blog_posting.py` logic.

Legacy/one-off collector not on the cron schedule: `scraped_ai_news.py` (original
한국경제/매일경제 RSS-only collector, superseded by `collect_keyword_news` for most flows but
still functional).

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
    token, see `kis_client.get_access_token`), `RankedMover`, `StockRealtimePrice`,
    `GlobalMarketQuote` (해외지수/국제환율/환전고시환율/금리 cache, feeds the header index ticker),
    `ExchangeRateSnapshot` (daily 매매기준율 history per currency, from the separate Korea Eximbank
    API — used only to compute day-over-day change_pct for `GlobalMarketQuote`'s FX_FIXING rows,
    since that API returns no change field itself).
  - **`news.py`**: `NewsSource`, `NewsKeyword`, `AnalyzedArticle` (scraped article + AI
    summary/analysis/blog draft + optional `stock`/`matched_keyword` FK; `scraped_by` is set only
    for member-submitted URLs and drives per-grade daily scrape limits), `PostedArticle` (records
    of what's been published where, keyed by `(blog_account, article)` to prevent double-posting;
    imports `StockItem` from `.market` and `BlogPostingAccount` from `.members`).
  - **`members.py`**: Django `User` plus `UserSubscription` (premium flag), `MemberGrade`
    (admin-defined tiers with `daily_scrape_limit`/`daily_post_limit`, NULL = unlimited),
    `UserPreference` (1:1, holds `interested_keywords`/`post_all_articles`/
    `auto_posting_enabled`/grade FK), `BlogPostingAccount` (per-user, per-platform credentials —
    WordPress/Blogger; replaces the old single global blog config),
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
  `EXIM_AUTH_KEY` (한국수출입은행 OpenAPI, 환전 고시 환율), Gmail SMTP creds). `DEBUG` defaults to
  `False` if unset — safe-by-default, opposite of Django's
  usual scaffold. Copy `.env.example` to get the full key list; `.env` itself is gitignored.
- Database is MySQL (`nextfinup_db` via `mysqlclient`), not the commented-out sqlite block. The
  `db.sqlite3` file in the repo root is stale/unused given this config.
- `AUTHENTICATION_BACKENDS` is overridden to `AllowAllUsersModelBackend` specifically so
  `LoginForm.confirm_login_allowed()` runs even for `is_active=False` accounts, letting it show a
  precise "email not verified" message instead of Django's default silent-reject "wrong
  credentials" behavior.
- Blog publishing credentials are no longer global/hardcoded — each member registers their own
  `BlogPostingAccount` (WordPress or Blogger) from `/mypage/`. Both publish live (not draft) —
  `WP_POST_STATUS`/`BLOGGER_IS_DRAFT` in `articles/blog_posting.py`.
- `collect_stock_data` intentionally rate-limits itself (randomized sleep between tickers,
  exponential backoff on failure, abort after N consecutive failures) to avoid yfinance/Yahoo IP
  blocks — preserve this behavior if you touch that command.
- `sync_index_membership` expects KRX-exported CSVs in EUC-KR encoding (see the
  `data_1931_*.csv` / `data_2000_*.csv` files in the repo root as examples).
- KIS access tokens are capped at roughly one issuance per day by KIS policy —
  `kis_client.get_access_token` caches in `KisAccessToken` and only refreshes within
  `EXPIRY_BUFFER` of expiry; don't add code paths that force-refresh on every call.
