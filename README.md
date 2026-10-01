# douban2simkl

A seamless, zero-friction tool to synchronize your Douban (豆瓣) movie and TV show watch history, ratings, and reviews to [Simkl](https://simkl.com).

## Key Features

- **Zero-Friction Authentication**:
  - Automatically detects `.douban.com` cookies from local browsers (Chrome, Edge, Firefox, Brave, Safari) without needing manual login.
  - One-click Simkl PIN authorization flow via `https://simkl.com/pin`.
- **Accurate Rating Calibration**:
  - Automatically identifies half-star ratings mentioned in user comments (e.g. `3.5`, `4.5`, `三星半`, `四星半`) and maps them accurately to Simkl's 1-10 scale ($3.5 \times 2 = 7$, $4.5 \times 2 = 9$).
- **Smart Memo & Long Review Handling**:
  - Adapts to Simkl's 140-character memo limitation.
  - Automatically archives all long reviews (>140 characters) into a beautifully formatted local markdown document (`long_reviews_archive.md`).
- **TV Multi-Season Mapping**:
  - Automatically detects Season 2+ series and maps Douban episode IDs to parent Series IMDb IDs with season metadata.
- **Smart Deduplication & Caching**:
  - Fetches your existing Simkl library to prevent duplicate imports.
  - Persists resolved IMDb mappings and sync states in a local SQLite database (`douban2simkl.db`) for instant restarts and incremental updates.
- **Complete Local Backup**:
  - Always exports a complete enriched JSONL backup (`douban_full_backup.jsonl`) containing Douban IDs, IMDb IDs, calibrated ratings, comments, and sync statuses.

---

## Installation

```bash
git clone https://github.com/JinxAgain/db-2-simkl.git
cd db-2-simkl

# Install dependencies (requires Python 3.10+)
pip install -r requirements.txt
# Or using uv:
# uv sync
```

---

## Usage

### 1. One-Click Run (Default)

Simply run:
```bash
python run.py
```
1. The tool will auto-detect your Douban browser cookies or load `douban_archive.jsonl` if present.
2. If first run, it will display a Simkl PIN URL and 4-digit code. Open `https://simkl.com/pin`, enter the code, and approve.
3. The tool will fetch your existing Simkl library, diff missing entries, resolve IMDb IDs, and sync in batches.
4. Exported files (`douban_full_backup.jsonl`, `long_reviews_archive.md`, `sync_report.md`) will be generated.

### 2. Dry Run Mode (Preview without modifying Simkl)

```bash
python run.py --dry-run --input douban_archive.jsonl
```

### 3. Using Existing Archive File

```bash
python run.py --input path/to/douban_archive.jsonl
```

### 4. Limit Item Count (Testing)

```bash
python run.py --limit 20 --dry-run
```

---

## Configuration (Optional)

Copy `.env.example` to `.env` if you want to customize API keys:

```env
# Simkl API Settings (pre-configured with default client ID)
SIMKL_CLIENT_ID=c5b2c9d69e4a3b118029d20c58e72cbe9b76c8c50eef5f7ce3a22839b2512f46
SIMKL_ACCESS_TOKEN=

# Optional: External Metadata APIs for TV multi-season parent series resolution
TMDB_API_KEY=
OMDB_API_KEY=

# Optional: Douban Cookie (Leave blank to auto-detect from local browsers)
DOUBAN_COOKIE=
```

---

## Output Files

| File | Description |
| --- | --- |
| `douban_full_backup.jsonl` | Complete enriched Douban archive with IMDb IDs, series mappings, and ratings. |
| `long_reviews_archive.md` | Formatted Markdown archive of all reviews exceeding Simkl's 140-char limit. |
| `sync_report.md` | Detailed execution summary including counts of scanned, skipped, and synced items. |
| `douban2simkl.db` | SQLite cache storing IMDb lookups and sync state to avoid repeated network requests. |

---

## License

MIT License
