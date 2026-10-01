# douban2simkl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `douban2simkl`, an all-in-one CLI tool that automatically reads Douban login cookies from local browsers, scrapes user movie/TV watch history, calibrates half-star ratings, resolves IMDb and TV multi-season Series IDs, deduplicates against existing Simkl libraries, synchronizes missing items via Simkl REST API, and exports a complete enriched local archive.

**Architecture:** A modular Python package with clear separation of concerns: browser cookie & Douban Rexxar crawler (`douban.py`), local SQLite cache (`storage.py`), half-star rating calibration & comment normalizer (`normalizer.py`), Douban-to-IMDb & TV multi-season resolver with TMDb/OMDb/Cinemeta (`resolver.py`), Simkl PIN-flow auth & sync engine (`simkl.py`), local backup exporter (`exporter.py`), and an interactive Rich terminal console (`cli.py`).

**Tech Stack:** Python 3.10+, `requests`, `rich`, `browser_cookie3`, `sqlite3`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-01-douban2simkl-design.md`

## Global Constraints

- Code and comments must be strictly in English.
- No heavy web frameworks (FastAPI, Redis, Celery, Prometheus); keep dependencies lightweight and portable.
- Store sensitive tokens, cookies, and local database caches in `.env` and `*.db`, which are ignored by `.gitignore`.
- Provide an automatic browser cookie reading flow as default, while allowing fallback to existing files (`douban_archive.jsonl`).
- Always export `douban_full_backup.jsonl` and `long_reviews_archive.md` locally upon completion.

## Review Focus

1. **Douban Half-star Rating Precision**: Comments with explicit half-star ratings (e.g. `3.5`, `3.5星`, `7分`, `4.5/5`) must be calibrated to the corresponding Simkl 1-10 integer score (7, 9, etc.), avoiding false positives on units like `3.5小时` or `3.5寸`.
2. **TV Show Multi-Season Resolution**: When Douban provides an episode-1 IMDb ID for Season 2+ shows, the resolver must extract the season number and resolve to the parent Series IMDb ID.
3. **Simkl Deduplication Robustness**: Items already existing in Simkl's movie/show library must be identified and skipped locally to avoid consuming Simkl API quota.
4. **Memo 140-Character Truncation & Long-review Archiving**: Comments exceeding 140 characters must truncate gracefully with `...` for Simkl's `memo` field, while the full text is saved to `long_reviews_archive.md`.
5. **Simkl PIN Authentication Flow**: Handle polling timeout, user denial, and access token persistence in `config.py` / `.env`.

---

## File Structure

```
douban2simkl/
├── douban2simkl/
│   ├── __init__.py
│   ├── config.py           # Configuration, default keys, .env management
│   ├── storage.py          # SQLite database schema, caching, state persistence
│   ├── normalizer.py       # Rating calibration (half-stars) and comment truncation
│   ├── resolver.py         # Douban ID -> IMDb ID and TV Series ID resolver
│   ├── douban.py           # Browser cookie reader and Douban Rexxar crawler
│   ├── simkl.py            # Simkl PIN authorization, library diffing, batch pusher
│   ├── exporter.py         # Full JSONL and markdown review exporter
│   └── cli.py              # Interactive Rich terminal UI and wizard controller
├── tests/
│   ├── test_normalizer.py
│   ├── test_storage.py
│   ├── test_resolver.py
│   └── test_simkl.py
├── run.py                  # CLI entrypoint executable
├── pyproject.toml          # Project metadata and dependencies
└── README.md               # User guide and documentation
```

---

## Tasks

### Task 1: Project Setup and Storage Engine (`storage.py`)

**Files:**
- Create: `pyproject.toml`
- Create: `douban2simkl/__init__.py`
- Create: `douban2simkl/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: None
- Produces: `Storage` class with methods:
  - `__init__(db_path: str = "douban_cache.db")`
  - `get_imdb_mapping(douban_id: str) -> Optional[dict]`
  - `save_imdb_mapping(douban_id: str, imdb_id: Optional[str], series_imdb_id: Optional[str], season: Optional[int], title: Optional[str]) -> None`
  - `get_sync_status(douban_id: str) -> Optional[str]`
  - `mark_synced(douban_id: str, status: str = "synced") -> None`

- [ ] **Step 1: Write `pyproject.toml` with dependencies**
  Specify `requests`, `rich`, `browser_cookie3`, `pytest`.

- [ ] **Step 2: Write failing unit test in `tests/test_storage.py`**
  Test creating tables, inserting IMDb mapping, retrieving mapping, and updating sync status.

- [ ] **Step 3: Run test to verify it fails**
  Run: `pytest tests/test_storage.py -v`
  Expected: FAIL with ModuleNotFoundError or missing class.

- [ ] **Step 4: Implement `Storage` in `douban2simkl/storage.py`**
  Implement SQLite tables `imdb_cache` (`douban_id PRIMARY KEY`, `imdb_id`, `series_imdb_id`, `season`, `title`, `updated_at`) and `sync_state` (`douban_id PRIMARY KEY`, `status`, `synced_at`).

- [ ] **Step 5: Run tests to verify they pass**
  Run: `pytest tests/test_storage.py -v`
  Expected: PASS

- [ ] **Step 6: Commit**
  ```bash
  git add pyproject.toml douban2simkl/__init__.py douban2simkl/storage.py tests/test_storage.py
  git commit -m "feat(storage): implement SQLite cache and sync state persistence"
  ```

---

### Task 2: Rating Calibration and Comment Normalizer (`normalizer.py`)

**Files:**
- Create: `douban2simkl/normalizer.py`
- Test: `tests/test_normalizer.py`

**Interfaces:**
- Consumes: None
- Produces:
  - `calibrate_rating(official_rating: Optional[int], comment: Optional[str]) -> tuple[Optional[int], str]` (returns `(score_1_to_10, reason)`)
  - `normalize_comment(comment: Optional[str]) -> tuple[Optional[str], bool]` (returns `(memo_text_max_140, is_truncated)`)

- [ ] **Step 1: Write unit tests in `tests/test_normalizer.py`**
  - Test official rating 4 with comment "3.5吧" -> score 7, reason "comment_half_star".
  - Test official rating 5 with comment "太好看了5星" -> score 10, reason "official".
  - Test official rating None with comment "2.5" -> score 5, reason "comment_half_star".
  - Test comment with unit false-positive "前2.5小时" with official 5 -> score 10 (not overridden).
  - Test Chinese character "四星半" -> score 9.
  - Test comment <= 140 chars -> full memo, `is_truncated=False`.
  - Test comment > 140 chars -> 137 chars + `...`, `is_truncated=True`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `pytest tests/test_normalizer.py -v`
  Expected: FAIL

- [ ] **Step 3: Implement `calibrate_rating` and `normalize_comment` in `douban2simkl/normalizer.py`**
  Use regex patterns for `[1-4]\.5`, `[一二三四五]星半`, and `[1-9]分`, with lookahead/lookbehind checks for units (`小时|h|寸|mm|月|号|岁`).

- [ ] **Step 4: Run test to verify it passes**
  Run: `pytest tests/test_normalizer.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git add douban2simkl/normalizer.py tests/test_normalizer.py
  git commit -m "feat(normalizer): add rating calibration engine and memo truncation"
  ```

---

### Task 3: Douban ID and TV Multi-Season Resolver (`resolver.py`)

**Files:**
- Create: `douban2simkl/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Storage` from `douban2simkl.storage`
- Produces: `ItemResolver` class with:
  - `extract_season_number(title: str) -> Optional[int]`
  - `fetch_douban_imdb_id(douban_id: str) -> Optional[str]`
  - `resolve_series_imdb_id(episode_imdb_id: str, tmdb_api_key: Optional[str] = None, omdb_api_key: Optional[str] = None) -> Optional[str]`
  - `resolve_item(douban_id: str, title: str, is_tv: bool) -> ResolvedItem`

- [ ] **Step 1: Write unit tests in `tests/test_resolver.py`**
  - Test `extract_season_number("大楼里只有谋杀 第二季")` -> 2.
  - Test `extract_season_number("Friends Season 5")` -> 5.
  - Test `extract_season_number("肖申克的救赎")` -> None.
  - Mock TMDb `/find` API returning TV episode with `show_id` and test resolving series IMDb ID.
  - Mock OMDb returning `seriesID`.
  - Test caching integration with `Storage`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `pytest tests/test_resolver.py -v`
  Expected: FAIL

- [ ] **Step 3: Implement `ItemResolver` in `douban2simkl/resolver.py`**
  - Scrape Douban mobile desc (`https://www.douban.com/doubanapp/h5/movie/{id}/desc`) with desktop fallback.
  - Resolve TV episode IMDb ID to series IMDb ID via TMDb (if key present), OMDb (if key present), or Cinemeta.
  - Integrate with `Storage` for caching.

- [ ] **Step 4: Run test to verify it passes**
  Run: `pytest tests/test_resolver.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git add douban2simkl/resolver.py tests/test_resolver.py
  git commit -m "feat(resolver): implement Douban-to-IMDb and TV multi-season resolver"
  ```

---

### Task 4: Douban Crawler with Automatic Cookie Detection (`douban.py`)

**Files:**
- Create: `douban2simkl/douban.py`
- Create: `douban2simkl/config.py`

**Interfaces:**
- Consumes: None
- Produces:
  - `get_douban_client(cookie_string: Optional[str] = None, browser: Optional[str] = None) -> DoubanClient`
  - `DoubanClient.fetch_all_movie_interests(on_progress: Optional[Callable] = None) -> list[dict]`
  - `load_from_archive_file(filepath: str) -> list[dict]`

- [ ] **Step 1: Implement `config.py`**
  Load environment variables (`SIMKL_CLIENT_ID`, `SIMKL_ACCESS_TOKEN`, `TMDB_API_KEY`, `OMDB_API_KEY`, `DOUBAN_COOKIE`).

- [ ] **Step 2: Implement `DoubanClient` in `douban2simkl/douban.py`**
  - Use `browser_cookie3` to scan for `.douban.com` cookies across Chrome, Edge, Firefox, Brave, Safari.
  - Support manual cookie string fallback.
  - Call Douban Rexxar API `user/{uid}/interests` with pagination (`PAGE_SIZE=50`), request throttling (1.0s interval), and HTTP 500 fallback.
  - Implement `load_from_archive_file` to support direct loading from `douban_archive.jsonl`.

- [ ] **Step 3: Verify with real archive file**
  Test running a quick verification reading 5 items from `douban_archive.jsonl`.

- [ ] **Step 4: Commit**
  ```bash
  git add douban2simkl/config.py douban2simkl/douban.py
  git commit -m "feat(douban): add browser cookie detector and interests crawler"
  ```

---

### Task 5: Simkl Client with PIN Auth, Deduplication, and Batch Sync (`simkl.py`)

**Files:**
- Create: `douban2simkl/simkl.py`
- Test: `tests/test_simkl.py`

**Interfaces:**
- Consumes: `config.py`, `storage.py`
- Produces: `SimklClient` with:
  - `request_pin() -> dict` (`{user_code, verification_url, expires_in, interval}`)
  - `poll_pin(user_code: str) -> Optional[str]` (returns `access_token`)
  - `get_existing_library_ids() -> set[str]` (calls `/sync/all-items/movies` & `/shows` with `extended=ids_only`)
  - `sync_history_batch(movies: list[dict], shows: list[dict]) -> dict` (calls `POST /sync/history`)
  - `add_to_list_batch(items: list[dict], status: str) -> dict` (calls `POST /sync/add-to-list`)

- [ ] **Step 1: Write unit tests in `tests/test_simkl.py`**
  - Mock Simkl PIN flow (request pin & polling).
  - Mock `/sync/all-items` returning movie & show IDs and verify `get_existing_library_ids()` populates a set containing IMDb/TMDB/TVDB IDs.
  - Mock `POST /sync/history` payload assembly and response parsing.

- [ ] **Step 2: Run test to verify it fails**
  Run: `pytest tests/test_simkl.py -v`
  Expected: FAIL

- [ ] **Step 3: Implement `SimklClient` in `douban2simkl/simkl.py`**
  - Implement PIN device authorization (RFC 8628).
  - Implement library diffing: fetch existing library IDs (`imdb`, `tmdb`, `tvdb`) into a set.
  - Implement chunked batch pusher (chunk size 50).
  - Convert statuses: "看过" -> `POST /sync/history`, "想看" -> `to: "plantowatch"`, "在看" -> `to: "watching"`.

- [ ] **Step 4: Run test to verify it passes**
  Run: `pytest tests/test_simkl.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  ```bash
  git add douban2simkl/simkl.py tests/test_simkl.py
  git commit -m "feat(simkl): implement PIN auth, library deduplication, and sync engine"
  ```

---

### Task 6: Exporter for Full Archive & Reviews (`exporter.py`)

**Files:**
- Create: `douban2simkl/exporter.py`

**Interfaces:**
- Consumes: None
- Produces:
  - `export_full_backup(records: list[dict], output_path: str = "douban_full_backup.jsonl") -> int`
  - `export_long_reviews(reviews: list[dict], output_path: str = "long_reviews_archive.md") -> int`
  - `generate_sync_report(stats: dict, output_path: str = "sync_report.md") -> str`

- [ ] **Step 1: Implement `exporter.py`**
  - Write `export_full_backup`: dump enriched records (with `douban_id`, `imdb_id`, `series_imdb_id`, `season`, `calibrated_rating`, `rating_source`, `comment`, `simkl_sync_status`).
  - Write `export_long_reviews`: write Markdown table or sectioned document for comments > 140 chars.
  - Write `generate_sync_report`: summary report with total, skipped, synced, and errors.

- [ ] **Step 2: Commit**
  ```bash
  git add douban2simkl/exporter.py
  git commit -m "feat(exporter): implement full enriched backup and markdown report exporters"
  ```

---

### Task 7: Interactive CLI & Top-Level Runner (`cli.py` & `run.py`)

**Files:**
- Create: `douban2simkl/cli.py`
- Create: `run.py`
- Create: `README.md`
- Create: `.env.example`

**Interfaces:**
- Consumes: `douban.py`, `resolver.py`, `normalizer.py`, `simkl.py`, `exporter.py`, `storage.py`
- Produces: CLI entrypoint executable `python run.py`

- [ ] **Step 1: Implement `cli.py` using Rich**
  - Display branded banner: "douban2simkl".
  - Step 1: Automatically detect browser cookie; if failed, prompt for cookie string or archive file path.
  - Step 2: Display Simkl PIN URL and code with live spinner until authorized.
  - Step 3: Fetch existing Simkl library, perform diffing, display summary card.
  - Step 4: Progress bar resolving IMDb IDs and pushing batches to Simkl.
  - Step 5: Export `douban_full_backup.jsonl`, `long_reviews_archive.md`, and print completion summary.
  - Support `--dry-run` flag to test everything without writing to Simkl.
  - Support `--input` flag to pass existing archive file directly.

- [ ] **Step 2: Create `run.py`**
  Wire up `python run.py` to invoke `cli.main()`.

- [ ] **Step 3: Create user-facing `README.md` and `.env.example`**
  Clear, accessible documentation explaining 1-click execution, browser requirements, and optional API keys.

- [ ] **Step 4: Execute `--dry-run` against local `douban_archive.jsonl`**
  Verify the full pipeline in dry-run mode.

- [ ] **Step 5: Commit**
  ```bash
  git add douban2simkl/cli.py run.py README.md .env.example
  git commit -m "feat(cli): complete interactive CLI wizard and top-level runner"
  ```
