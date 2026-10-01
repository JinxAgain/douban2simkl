# douban2simkl

A modern, robust, and zero-friction tool to synchronize your Douban (豆瓣) movie and TV show watch history, ratings, watchlists, and reviews to [Simkl](https://simkl.com).

English | [中文说明](#中文说明)

---

## Key Features

- **Full Status Synchronization**:
  - **Watched (看过)**: Synced to Simkl watched history with watch date, calibrated rating, and review memo (`POST /sync/history`).
  - **Watching (在看)**: Synced to Simkl "Watching" watchlist (`POST /sync/add-to-list`).
  - **Plan to Watch (想看)**: Synced to Simkl "Plan to Watch" watchlist (`POST /sync/add-to-list`).
- **Multi-ID Support (IMDb, TMDb, TVDB)**:
  - Supports syncing via `imdb_id`, `tmdb_id`, and `tvdb_id`. If a Chinese drama or indie title has no IMDb ID, Simkl can still identify and match it using TMDb or TVDB IDs!
- **Anti-Scraping Multi-Tier Resolution Pipeline**:
  - **Wikidata SPARQL Knowledge Graph**: Bulk-resolves thousands of Douban items (`P4529` -> `P345`) in seconds with **zero requests to Douban**, completely bypassing anti-scraping limits.
  - **Wikidata TV Series Pre-Resolver**: Bulk-links multi-season shows (Season 2+) to their root series IDs.
  - **NeoDB Open Catalog API**: Fast, rate-limit-friendly catalog lookup for Chinese and international media.
  - **Strict TMDb Title & Year Search Fallback**: Automatically searches TMDb with Chinese/original titles and release years, backed by strict title similarity checks to prevent false matches.
  - **OMDb API Fallback**: Supplementary metadata lookup.
  - **Douban Scraper Fallback**: Only accessed as a last resort with gentle throttling.
- **Accurate Rating Calibration**:
  - Automatically identifies half-star ratings mentioned in user comments (e.g. `3.5`, `4.5`, `三星半`, `四星半`, `7/10`) and maps them accurately to Simkl's 1-10 scale ($3.5 \times 2 = 7$, $4.5 \times 2 = 9$).
- **Smart Memo & Long Review Handling**:
  - Automatically truncates comments to Simkl's 140-character memo limit.
  - Preserves all full-length long reviews (>140 chars) into a beautifully formatted Markdown archive (`long_reviews_archive.md`).
- **Zero-Friction Authentication**:
  - Automatically detects `.douban.com` cookies from local browsers (Chrome, Edge, Firefox, Brave, Safari) without manual cookie export.
  - One-click OAuth2 Device PIN flow: open `https://simkl.com/pin`, enter the displayed code, and start syncing immediately.
- **Smart Deduplication & Local Cache**:
  - Pre-fetches your existing Simkl library to prevent duplicate scrobbles.
  - Persists resolved IDs and sync status in a local SQLite database (`douban2simkl.db`) for instant restarts and incremental updates.
- **Standalone Diagnostic Reports**:
  - `unresolved_items.md`: Lists titles missing metadata IDs on all platforms with direct Douban links.
  - `simkl_failed_sync.md`: Lists items rejected by Simkl API or missing in Simkl's catalog with error details and direct Douban links.
  - `douban_full_backup.jsonl`: Complete enriched backup with all resolved IDs, ratings, and comments.

---

## Installation

```bash
git clone https://github.com/JinxAgain/douban2simkl.git
cd douban2simkl

# Install dependencies (requires Python 3.10+)
pip install -r requirements.txt
# Or using uv:
# uv sync
```

---

## Usage

### 1. One-Click Run (Default)

```bash
python run.py
```
1. Automatically reads from existing `douban_archive.jsonl` or fetches directly from your Douban account using browser cookies.
2. If first run, opens Simkl PIN verification (`https://simkl.com/pin`). Enter the code displayed in the terminal.
3. Pre-resolves IDs via Wikidata SPARQL, TMDb, and NeoDB.
4. Pushes watch history and watchlists in batches to Simkl.
5. Generates enriched local backups and Markdown reports.

### 2. Preview Mode (Dry Run)

Simulate the entire resolution and batching process without pushing anything to Simkl:
```bash
python run.py --dry-run
```

### 3. Specify Existing Douban Archive

```bash
python run.py --input path/to/douban_archive.jsonl
```

### 4. Force Online Douban Crawl

Force crawling Douban collections even if a local archive file exists:
```bash
python run.py --crawl
```

### 5. Adjust Concurrency and Limits

```bash
# Limit to 20 items for quick testing
python run.py --limit 20 --dry-run

# Run with 5 worker threads (default: 3)
python run.py --threads 5
```

---

## Configuration (Optional)

Copy `.env.example` to `.env` to configure optional API keys:

```bash
cp .env.example .env
```

```env
# Simkl API Settings (Pre-configured with default client ID)
SIMKL_CLIENT_ID=
SIMKL_CLIENT_SECRET=

# Optional: External Metadata APIs for TV multi-season parent series resolution & title fallback
TMDB_API_KEY=
OMDB_API_KEY=

# Optional: Douban Cookie (Leave blank to auto-detect from local browsers)
DOUBAN_COOKIE=
```

> [!NOTE]
> You do **not** need to manually fill in `SIMKL_ACCESS_TOKEN`. The interactive CLI automatically acquires and securely persists your token via the official Simkl PIN device flow.

---

## Output Files

| File | Description |
| :--- | :--- |
| `douban_full_backup.jsonl` | Complete enriched backup containing all Douban metadata, resolved IDs (IMDb/TMDb/TVDB), calibrated ratings, and sync status. |
| `long_reviews_archive.md` | Full-text Markdown archive for reviews exceeding Simkl's 140-character limit. |
| `unresolved_items.md` | Standalone table of items that could not be mapped to IMDb/TMDb/TVDB, with direct Douban links. |
| `simkl_failed_sync.md` | Standalone table of items rejected by Simkl API or not found in Simkl catalog, with error details. |
| `sync_report.md` | Summary report with execution counts and metrics. |
| `douban2simkl.db` | Local SQLite database caching resolved metadata and sync state. |

---

<a name="中文说明"></a>
## 中文说明

`douban2simkl` 是一款专注于将豆瓣电影、电视剧的标记记录完整同步至 [Simkl](https://simkl.com) 的无痛同步工具。

### 核心亮点
1. **多状态全量同步**：同时支持「看过」（同步历史观影记录、观影时间与评分）、「在看」（同步至 Watching）和「想看」（同步至 Plan to Watch）。
2. **多源反反爬映射体系**：
   - 优先通过 **Wikidata SPARQL 知识图谱** 批量直查豆瓣 ID $\to$ IMDb 编号，几千条记录数秒内完成，对豆瓣零请求。
   - 自动解析季播剧（如第二季、第三季）关联的父剧集系列编号与季号。
   - 集成 **NeoDB 开放数据接口** 与 **TMDb 严格标题/年份搜索回退**，支持多标识符（IMDb、TMDb、TVDB）互补，彻底解决冷门华语剧集和动画的匹配问题。
3. **半星评分智能校准**：自动识别短评中的半星打分（例如 `3.5星`、`四星半`、`7/10`），自动换算为 Simkl 的 1-10 分制。
4. **长评自动归档**：超过 Simkl 140 字限制的长篇短评自动归档至独立优美的 Markdown 文件（`long_reviews_archive.md`）。
5. **极简认证体验**：
   - 豆瓣：自动检测本机浏览器 Cookie，无需抓包复制。
   - Simkl：内置官方 OAuth2 Device PIN 认证，在浏览器打开 `https://simkl.com/pin` 输入 4 位码即可完成授权。
6. **失败与未匹配条目清晰导出**：
   - `unresolved_items.md`：导出所有未匹配到 ID 的条目（附豆瓣链接）。
   - `simkl_failed_sync.md`：导出同步 Simkl 失败或未收录的条目（附失败原因与豆瓣链接）。

---

## Testing

Run the test suite using pytest:

```bash
pytest tests/ -v
```

---

## License

[MIT License](LICENSE)
