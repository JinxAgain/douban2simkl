"""Interactive CLI Wizard and orchestrator for douban2simkl."""

import argparse
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from douban2simkl import config
from douban2simkl.douban import DoubanClient, get_douban_client, load_from_archive_file
from douban2simkl.exporter import export_full_backup, export_long_reviews, generate_sync_report
from douban2simkl.normalizer import calibrate_rating, normalize_comment
from douban2simkl.resolver import DoubanResolver
from douban2simkl.simkl import SimklClient
from douban2simkl.storage import Storage

logger = logging.getLogger("douban2simkl")
console = Console()


def print_banner() -> None:
    banner = """[bold cyan]
     _             _                  ____       _           _   _ 
  __| | ___  _   _| |__   __ _ _ __  |___ \\  ___(_)_ __ ___ | | _| |
 / _` |/ _ \\| | | | '_ \\ / _` | '_ \\   __) |/ __| | '_ ` _ \\| |/ / |
| (_| | (_) | |_| | |_) | (_| | | | | / __/ \\__ \\ | | | | | |   <| |
 \\__,_|\\___/ \\__,_|_.__/ \\__,_|_| |_||_____||___/_|_| |_| |_|_|\\_\\_|
[/bold cyan]
[dim]Sync your Douban movies and TV shows watch history seamlessly to Simkl[/dim]
"""
    console.print(Panel(banner, border_style="cyan"))


def get_or_prompt_simkl_token(
    client: SimklClient, storage: Storage, dry_run: bool = False
) -> Optional[str]:
    """Retrieve or authenticate Simkl access token via PIN flow."""
    # 1. Check environment variable
    if config.SIMKL_ACCESS_TOKEN:
        client.set_access_token(config.SIMKL_ACCESS_TOKEN)
        return config.SIMKL_ACCESS_TOKEN

    # 2. Check local database
    token = storage.get_setting("simkl_access_token")
    if token:
        client.set_access_token(token)
        return token

    if dry_run:
        console.print("[yellow][DRY RUN] Skipping Simkl authentication flow.[/yellow]")
        return "dry_run_token"

    # 3. Request PIN
    console.print("[bold yellow]Simkl authorization required.[/bold yellow]")
    with console.status("[cyan]Requesting PIN code from Simkl...[/cyan]"):
        try:
            pin_data = client.request_pin()
        except Exception as e:
            console.print(f"[bold red]Failed to request PIN from Simkl: {e}[/bold red]")
            return None

    user_code = pin_data.get("user_code", "")
    verification_url = pin_data.get("verification_url", "https://simkl.com/pin")
    expires_in = pin_data.get("expires_in", 900)
    interval = pin_data.get("interval", 5)

    pin_panel = f"""
1. Open this URL in your browser: [bold underline blue]{verification_url}[/bold underline blue]
2. Enter the PIN code: [bold green font_size=20]{user_code}[/bold green font_size=20]
3. Click 'Authorize' to grant access.

Waiting for your approval (expires in {expires_in // 60} minutes)...
"""
    console.print(Panel(pin_panel, title="[bold]Authorize douban2simkl on Simkl[/bold]", border_style="green"))

    start_time = time.time()
    with console.status("[bold cyan]Waiting for authorization...[/bold cyan]") as spinner:
        while time.time() - start_time < expires_in:
            time.sleep(interval)
            try:
                auth_token = client.poll_pin(user_code)
                if auth_token:
                    config.save_simkl_token(auth_token)
                    storage.set_setting("simkl_access_token", auth_token)
                    console.print("[bold green]Successfully authorized and saved Simkl token![/bold green]\n")
                    return auth_token
            except Exception:
                pass

    console.print("[bold red]PIN authorization timed out.[/bold red]")
    return None


def run_pipeline(
    input_file: Optional[str] = None,
    dry_run: bool = False,
    db_path: str = "douban2simkl.db",
    limit: Optional[int] = None,
    batch_size: int = 50,
    skip_auth: bool = False,
) -> None:
    """Execute the end-to-end sync and export pipeline."""
    print_banner()

    storage = Storage(db_path)
    simkl_client = SimklClient(client_id=config.SIMKL_CLIENT_ID)
    resolver = DoubanResolver(storage=storage)

    # Step 1: Obtain Douban records
    douban_records: List[Dict[str, Any]] = []
    if input_file:
        if not os.path.exists(input_file):
            console.print(f"[bold red]Input file '{input_file}' not found![/bold red]")
            sys.exit(1)
        console.print(f"[cyan]Loading records from archive file: [bold]{input_file}[/bold]...[/cyan]")
        douban_records = load_from_archive_file(input_file)
    else:
        # Check if local douban_archive.jsonl exists first as convenience
        if os.path.exists("douban_archive.jsonl"):
            console.print("[cyan]Found local [bold]douban_archive.jsonl[/bold], loading directly...[/cyan]")
            douban_records = load_from_archive_file("douban_archive.jsonl")
        else:
            console.print("[cyan]Detecting Douban cookies from local browsers...[/cyan]")
            try:
                douban_client = get_douban_client(cookie_string=config.DOUBAN_COOKIE)
                with console.status("[cyan]Fetching watch history from Douban API...[/cyan]"):
                    douban_records = douban_client.fetch_all_movie_interests()
            except Exception as e:
                console.print(f"[yellow]Could not automatically fetch Douban records: {e}[/yellow]")
                prompt_path = console.input("[bold yellow]Please enter path to a Douban JSONL archive file: [/bold yellow]").strip()
                if prompt_path and os.path.exists(prompt_path):
                    douban_records = load_from_archive_file(prompt_path)
                else:
                    console.print("[bold red]No valid Douban data source available. Exiting.[/bold red]")
                    sys.exit(1)

    total_scanned = len(douban_records)
    console.print(f"[bold green]Loaded {total_scanned} records from Douban.[/bold green]\n")

    if limit and limit > 0:
        douban_records = douban_records[:limit]
        console.print(f"[yellow]Limiting execution to first {len(douban_records)} records.[/yellow]\n")

    # Step 2: Simkl Authorization
    if not skip_auth:
        token = get_or_prompt_simkl_token(simkl_client, storage, dry_run=dry_run)
        if not token:
            console.print("[bold red]Simkl authentication failed. Exiting.[/bold red]")
            sys.exit(1)

    # Step 3: Fetch Simkl Library for Deduplication
    simkl_existing_ids: Set[str] = set()
    if not skip_auth and not dry_run:
        with console.status("[cyan]Fetching existing library from Simkl for deduplication...[/cyan]"):
            simkl_existing_ids = simkl_client.get_existing_library_ids()
            console.print(
                f"[bold green]Found {len(simkl_existing_ids)} items in your Simkl library.[/bold green]\n"
            )

    # Step 4: Resolution, Calibration, and Batch Sync
    enriched_records: List[Dict[str, Any]] = []
    long_reviews: List[Dict[str, Any]] = []

    already_in_simkl_count = 0
    new_to_sync_count = 0
    synced_count = 0
    failed_count = 0

    history_movies_batch: List[Dict[str, Any]] = []
    history_shows_batch: List[Dict[str, Any]] = []
    watchlist_items_batch: List[Dict[str, Any]] = []
    pending_sync_ids: List[str] = []

    def flush_batches() -> None:
        nonlocal synced_count, failed_count
        if dry_run or skip_auth:
            synced_count += len(pending_sync_ids)
            pending_sync_ids.clear()
            history_movies_batch.clear()
            history_shows_batch.clear()
            watchlist_items_batch.clear()
            return

        if history_movies_batch or history_shows_batch:
            try:
                simkl_client.sync_history_batch(
                    movies=history_movies_batch if history_movies_batch else None,
                    shows=history_shows_batch if history_shows_batch else None,
                )
                for did in pending_sync_ids:
                    storage.mark_synced(did, "synced")
                synced_count += len(pending_sync_ids)
            except Exception as e:
                logger.error("Failed to push history batch: %s", e)
                for did in pending_sync_ids:
                    storage.mark_synced(did, f"error: {e}")
                failed_count += len(pending_sync_ids)

        if watchlist_items_batch:
            # Group by 'to' status
            by_status: Dict[str, List[Dict[str, Any]]] = {}
            for it in watchlist_items_batch:
                st = it.pop("to", "plantowatch")
                by_status.setdefault(st, []).append(it)
            for st, items in by_status.items():
                try:
                    simkl_client.add_to_list_batch(movies=items, to=st)
                except Exception as e:
                    logger.error("Failed to push watchlist batch: %s", e)

        pending_sync_ids.clear()
        history_movies_batch.clear()
        history_shows_batch.clear()
        watchlist_items_batch.clear()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        sync_task = progress.add_task("[cyan]Processing & Syncing...", total=len(douban_records))

        for item in douban_records:
            douban_id = str(item.get("douban_id", ""))
            title = item.get("title", "")
            raw_type = item.get("type", "movie")
            is_tv = raw_type == "tv"
            status = item.get("status", "done")  # done, mark, doing
            create_time = item.get("create_time", "")
            official_rating = item.get("rating")
            comment = item.get("comment", "") or ""

            # Check if this item is already marked as synced in local DB
            cached_sync_status = storage.get_sync_status(douban_id)

            # Resolve IMDb ID
            resolved = resolver.resolve_item(
                douban_id=douban_id,
                title=title,
                is_tv=is_tv,
                tmdb_api_key=config.TMDB_API_KEY,
                omdb_api_key=config.OMDB_API_KEY,
            )
            imdb_id = resolved.get("imdb_id")
            series_imdb_id = resolved.get("series_imdb_id")
            season = resolved.get("season")

            # Calibrate rating & comment
            calibrated_rating, rating_source = calibrate_rating(official_rating, comment)
            memo_text, is_long = normalize_comment(comment)

            record_sync_status = "pending"

            # Check deduplication against Simkl library
            target_id = series_imdb_id or imdb_id
            if (target_id and target_id in simkl_existing_ids) or cached_sync_status == "synced":
                already_in_simkl_count += 1
                record_sync_status = "already_synced"
            elif not imdb_id:
                failed_count += 1
                record_sync_status = "unresolved_no_imdb"
            else:
                new_to_sync_count += 1
                # Prepare payload
                if status == "done":
                    if is_tv or season:
                        show_obj: Dict[str, Any] = {
                            "ids": {"imdb": series_imdb_id or imdb_id},
                            "seasons": [{"number": season or 1}],
                        }
                        if calibrated_rating:
                            show_obj["rating"] = calibrated_rating
                        if create_time:
                            show_obj["watched_at"] = create_time
                        if memo_text:
                            show_obj["memo"] = {"text": memo_text, "is_private": False}
                        history_shows_batch.append(show_obj)
                    else:
                        movie_obj: Dict[str, Any] = {
                            "ids": {"imdb": imdb_id},
                        }
                        if calibrated_rating:
                            movie_obj["rating"] = calibrated_rating
                        if create_time:
                            movie_obj["watched_at"] = create_time
                        if memo_text:
                            movie_obj["memo"] = {"text": memo_text, "is_private": False}
                        history_movies_batch.append(movie_obj)
                    pending_sync_ids.append(douban_id)
                elif status in ("mark", "doing"):
                    simkl_to = "watching" if status == "doing" else "plantowatch"
                    watchlist_obj = {
                        "ids": {"imdb": series_imdb_id or imdb_id},
                        "to": simkl_to,
                    }
                    watchlist_items_batch.append(watchlist_obj)
                    pending_sync_ids.append(douban_id)

                record_sync_status = "synced"

                if len(pending_sync_ids) >= batch_size:
                    flush_batches()

            enriched_entry = {
                "douban_id": douban_id,
                "title": title,
                "original_title": item.get("original_title", ""),
                "year": item.get("year"),
                "type": "tv" if (is_tv or season) else "movie",
                "status": status,
                "create_time": create_time,
                "official_rating": official_rating,
                "calibrated_rating": calibrated_rating,
                "rating_source": rating_source,
                "imdb_id": imdb_id,
                "series_imdb_id": series_imdb_id,
                "season": season,
                "comment": comment,
                "memo_synced": memo_text,
                "simkl_sync_status": record_sync_status,
            }
            enriched_records.append(enriched_entry)

            if is_long:
                long_reviews.append(enriched_entry)

            progress.advance(sync_task)

        # Flush any remaining items
        flush_batches()

    # Step 5: Exporting
    console.print("\n[cyan]Exporting local archive and backup files...[/cyan]")
    export_full_backup(enriched_records, "douban_full_backup.jsonl")
    if long_reviews:
        export_long_reviews(long_reviews, "long_reviews_archive.md")

    stats = {
        "total_scanned": total_scanned,
        "already_in_simkl": already_in_simkl_count,
        "new_to_sync": new_to_sync_count,
        "synced": synced_count,
        "failed": failed_count,
        "long_reviews_count": len(long_reviews),
    }
    generate_sync_report(stats, "sync_report.md")

    # Step 6: Presentation Table
    table = Table(title="[bold green]Sync Execution Completed[/bold green]", border_style="green")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Count", style="bold magenta")
    table.add_row("Total Scanned", str(total_scanned))
    table.add_row("Already in Simkl (Skipped)", str(already_in_simkl_count))
    table.add_row("New Items to Sync", str(new_to_sync_count))
    table.add_row("Successfully Synced", str(synced_count))
    table.add_row("Failed / Unresolved IMDb", str(failed_count))
    table.add_row("Long Reviews Archived (>140 chars)", str(len(long_reviews)))

    console.print(table)
    console.print(
        Panel.fit(
            "[bold green]Files Generated:[/bold green]\n"
            "- [bold]douban_full_backup.jsonl[/bold] (Enriched full backup with IMDb IDs and calibrated ratings)\n"
            "- [bold]long_reviews_archive.md[/bold] (Full text archive for reviews exceeding 140 chars)\n"
            "- [bold]sync_report.md[/bold] (Detailed summary report)\n"
            "- [bold]douban2simkl.db[/bold] (Local SQLite cache)",
            border_style="blue",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Douban movie and TV show watch history to Simkl")
    parser.add_argument("-i", "--input", help="Path to input Douban archive file (JSONL format)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying Simkl")
    parser.add_argument("--db", default="douban2simkl.db", help="Path to SQLite cache database")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to process")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for Simkl sync calls")
    parser.add_argument("--skip-auth", action="store_true", help="Skip Simkl authorization and only export local backups")

    args = parser.parse_args()
    run_pipeline(
        input_file=args.input,
        dry_run=args.dry_run,
        db_path=args.db,
        limit=args.limit,
        batch_size=args.batch_size,
        skip_auth=args.skip_auth,
    )


if __name__ == "__main__":
    main()
