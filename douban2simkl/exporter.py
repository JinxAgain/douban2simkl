import datetime
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def export_full_backup(records: List[Dict[str, Any]], output_path: str = "douban_full_backup.jsonl") -> int:
    """Export all enriched Douban records to a local JSONL file."""
    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    logger.info("Exported %d full backup records to %s", written, output_path)
    return written


def export_long_reviews(reviews: List[Dict[str, Any]], output_path: str = "long_reviews_archive.md") -> int:
    """Export long reviews (>140 chars) to a formatted Markdown file."""
    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Douban Long Reviews Archive\n\n")
        f.write(
            f"> Generated on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
            f"> Total long reviews archived: {len(reviews)}\n\n---\n\n"
        )

        for item in reviews:
            title = item.get("title", "Untitled")
            year = item.get("year", "")
            year_str = f" ({year})" if year else ""
            douban_id = item.get("douban_id", "")
            imdb_id = item.get("imdb_id", "")
            rating = item.get("calibrated_rating", "N/A")
            official = item.get("official_rating", "N/A")
            created = item.get("create_time", "")
            comment = item.get("comment", "").strip()

            f.write(f"## {title}{year_str}\n\n")
            meta_parts = []
            if douban_id:
                meta_parts.append(f"- **Douban**: [{douban_id}](https://movie.douban.com/subject/{douban_id}/)")
            if imdb_id:
                meta_parts.append(f"- **IMDb**: [{imdb_id}](https://www.imdb.com/title/{imdb_id}/)")
            meta_parts.append(f"- **Rating**: {rating}/10 (Official: {official} stars)")
            if created:
                meta_parts.append(f"- **Watched At**: {created}")

            f.write("\n".join(meta_parts) + "\n\n")
            f.write(f"```quote\n{comment}\n```\n\n---\n\n")
            written += 1

    logger.info("Exported %d long reviews to %s", written, output_path)
    return written


def export_unresolved_items(records: List[Dict[str, Any]], output_path: str = "unresolved_items.md") -> int:
    """Export failed/unresolved items to a separate Markdown document with direct Douban links."""
    written = 0
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Unresolved Douban Items (Missing IMDb ID)\n\n")
        f.write(
            f"> Generated on {now_str}  \n"
            f"> Total unresolved items: {len(records)}  \n"
            f"> These items could not be automatically mapped to IMDb via Wikidata, NeoDB, or Douban.\n\n---\n\n"
        )
        f.write("| Douban ID | Title | Year | Type | Status | Link |\n")
        f.write("| :--- | :--- | :---: | :---: | :---: | :--- |\n")
        for item in records:
            did = item.get("douban_id", "")
            title = item.get("title", "")
            year = item.get("year", "") or "-"
            item_type = item.get("type", "movie")
            status = item.get("status", "done")
            link = f"https://movie.douban.com/subject/{did}/"
            f.write(f"| `{did}` | **{title}** | {year} | `{item_type}` | `{status}` | [View on Douban]({link}) |\n")
            written += 1
    logger.info("Exported %d unresolved items to %s", written, output_path)
    return written


def generate_sync_report(stats: Dict[str, Any], output_path: str = "sync_report.md") -> str:
    """Generate a Markdown sync summary report."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = f"""# Douban to Simkl Sync Report

**Generated At**: {now_str}

## Execution Summary

- **Total Scanned**: {stats.get('total_scanned', 0)}
- **Already in Simkl (Skipped)**: {stats.get('already_in_simkl', 0)}
- **New Items to Sync**: {stats.get('new_to_sync', 0)}
- **Successfully Synced**: {stats.get('synced', 0)}
- **Simkl API Errors**: {stats.get('simkl_errors', 0)}
- **Unresolved IMDb**: {stats.get('unresolved', 0)}
- **Long Reviews Archived (>140 chars)**: {stats.get('long_reviews_count', 0)}

## Files Generated

- Full enriched backup: `douban_full_backup.jsonl`
- Long reviews archive: `long_reviews_archive.md`
- Unresolved items list: `unresolved_items.md` (if any missing IMDb)
- Local cache database: `douban2simkl.db`
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("Saved sync report to %s", output_path)
    return report
