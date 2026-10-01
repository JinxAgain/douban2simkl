"""Interactive CLI Wizard and orchestrator for douban2simkl."""

import argparse
import concurrent.futures
import json
import logging
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

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
        if client.verify_token():
            return config.SIMKL_ACCESS_TOKEN
        console.print("[yellow]SIMKL_ACCESS_TOKEN in .env is invalid or unauthenticated. Starting authorization...[/yellow]")
        client.set_access_token("")

    # 2. Check local database
    token = storage.get_setting("simkl_access_token")
    if token:
        client.set_access_token(token)
        if client.verify_token():
            return token
        console.print("[yellow]Stored Simkl token in cache is invalid or expired. Starting authorization...[/yellow]")
        client.set_access_token("")

    if dry_run:
        console.print("[yellow][DRY RUN] Skipping Simkl authentication flow.[/yellow]")
        return "dry_run_token"

    console.print("[bold yellow]Simkl authorization required.[/bold yellow]")

    # Check stored client_id first
    stored_cid = storage.get_setting("simkl_client_id")
    if stored_cid:
        client.client_id = stored_cid
    elif config.SIMKL_CLIENT_ID:
        client.client_id = config.SIMKL_CLIENT_ID

    pin_data = None
    while not pin_data:
        if client.client_id:
            try:
                with console.status("[cyan]Requesting PIN code from Simkl...[/cyan]"):
                    pin_data = client.request_pin()
                break
            except Exception as e:
                console.print(f"[yellow]Simkl Client ID verification failed ({e}).[/yellow]")

        guide = """
[bold]To connect to your Simkl account, you need a free Simkl Client ID (takes ~15 seconds):[/bold]
1. Open this link in your browser: [bold underline blue]https://simkl.com/settings/developer/new/[/bold underline blue]
2. Fill in:
   - [bold]Name[/bold]: [green]douban2simkl[/green]
   - [bold]Redirect URI[/bold]: [green]urn:ietf:wg:oauth:2.0:oob[/green] (or https://simkl.com)
3. Click [bold]Create App[/bold] and copy the generated [bold]Client ID[/bold].
"""
        console.print(Panel(guide, title="[bold yellow]Simkl Client ID Required[/bold yellow]", border_style="yellow"))
        input_cid = console.input("[bold cyan]Enter your Simkl Client ID (or Access Token): [/bold cyan]").strip()
        if not input_cid:
            return None

        # Check if the user directly entered a bearer access token
        if input_cid.startswith("ey") or len(input_cid) > 80:
            config.save_simkl_token(input_cid)
            storage.set_setting("simkl_access_token", input_cid)
            client.set_access_token(input_cid)
            if client.verify_token():
                console.print("[bold green]Saved and verified Simkl Access Token successfully![/bold green]\n")
                return input_cid
            else:
                console.print("[bold red]Provided token is invalid. Please try PIN code authorization.[/bold red]")

        client.client_id = input_cid
        config.save_simkl_client_id(input_cid)
        storage.set_setting("simkl_client_id", input_cid)

    user_code = pin_data.get("user_code", "")
    verification_url = pin_data.get("verification_url", "https://simkl.com/pin")
    complete_url = pin_data.get("verification_uri_complete", verification_url)
    expires_in = pin_data.get("expires_in", 900)
    interval = pin_data.get("interval", 5)

    pin_panel = f"""
1. Open this URL in your browser:
   [bold underline blue]{complete_url}[/bold underline blue]

2. (If prompted) Enter PIN: [bold green font_size=20]{user_code}[/bold green font_size=20]
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
    force_crawl: bool = False,
    threads: int = 3,
) -> None:
    """Execute the end-to-end sync and export pipeline."""
    print_banner()

    # Pre-load Douban session if available to share cookies with resolver
    douban_session = None
    try:
        temp_client = get_douban_client(cookie_string=config.DOUBAN_COOKIE)
        douban_session = temp_client.session
    except Exception:
        pass

    storage = Storage(db_path)
    simkl_client = SimklClient(client_id=config.SIMKL_CLIENT_ID)
    resolver = DoubanResolver(storage=storage, session=douban_session)

    # Step 1: Obtain Douban records
    douban_records: List[Dict[str, Any]] = []
    archive_path = None
    if input_file:
        archive_path = input_file
    elif not force_crawl:
        if os.path.exists("douban_archive.jsonl"):
            archive_path = "douban_archive.jsonl"

    if archive_path:
        if not os.path.exists(archive_path):
            console.print(f"[bold red]Input file '{archive_path}' not found![/bold red]")
            sys.exit(1)
        console.print(f"[cyan]Loading records from archive file: [bold]{archive_path}[/bold]...[/cyan]")
        douban_records = load_from_archive_file(archive_path)
    else:
        console.print("[cyan]Detecting Douban cookies from local browsers...[/cyan]")
        try:
            douban_client = get_douban_client(cookie_string=config.DOUBAN_COOKIE)
            user_info = douban_client.checkin()
            console.print(
                f"[green]Logged in to Douban as: [bold]{user_info.get('username')}[/bold] (UID: {user_info.get('uid')})[/green]"
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as fetch_progress:
                fetch_task = fetch_progress.add_task("[cyan]Scanning Douban collections...", total=None)

                def on_init(status_totals: Dict[str, int], grand_total: int) -> None:
                    fetch_progress.update(
                        fetch_task,
                        total=grand_total if grand_total > 0 else 100,
                        description=f"[cyan]Found {grand_total} records. Fetching...",
                    )

                def on_progress(status_name: str, cur_status: int, total_status: int, total_all: int, grand_total: int) -> None:
                    status_cn = {"done": "看过", "doing": "在看", "mark": "想看"}.get(status_name, status_name)
                    fetch_progress.update(
                        fetch_task,
                        completed=total_all,
                        total=grand_total if grand_total > 0 else total_status,
                        description=f"[cyan]Fetching [{status_cn}] ({cur_status}/{total_status})",
                    )

                douban_records = douban_client.fetch_all_movie_interests(
                    limit=limit, on_init=on_init, on_progress=on_progress
                )
                try:
                    with open("douban_archive.jsonl", "w", encoding="utf-8") as f:
                        for rec in douban_records:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    console.print(f"[bold green]Saved {len(douban_records)} raw records to douban_archive.jsonl[/bold green]\n")
                except Exception as e:
                    logger.warning("Failed to save douban_archive.jsonl: %s", e)
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

    # Bulk Wikidata Pre-fetch: query Wikidata SPARQL in batches of 100 for all uncached items
    uncached_ids = [
        str(r.get("douban_id"))
        for r in douban_records
        if not storage.get_imdb_mapping(str(r.get("douban_id")))
    ]
    if uncached_ids:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as wiki_progress:
            wiki_task = wiki_progress.add_task("[cyan]Pre-fetching from Wikidata Knowledge Graph...", total=len(uncached_ids))

            def on_wiki_progress(completed: int, total: int, matches: int) -> None:
                wiki_progress.update(
                    wiki_task,
                    completed=completed,
                    total=total,
                    description=f"[cyan]Wikidata Knowledge Graph (matched {matches} items)...",
                )

            wiki_results = resolver.batch_resolve_wikidata(uncached_ids, on_progress=on_wiki_progress)
            console.print(
                f"[bold green]✓ Pre-resolved {len(wiki_results)} items via Wikidata (zero Douban requests)![/bold green]\n"
            )

    workers = min(max(1, threads), 5)
    console.print(
        f"[cyan]Resolving & syncing with [bold]{workers}[/bold] concurrent workers (NeoDB -> Douban Fallback)...[/cyan]\n"
    )

    def resolve_single_record(record: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        douban_id = str(record.get("douban_id", ""))
        title = record.get("title", "")
        raw_type = record.get("type", "movie")
        is_tv = raw_type == "tv"
        cached = storage.get_imdb_mapping(douban_id)
        if not cached:
            # Small random delay between 0.1s and 0.25s for NeoDB / external APIs
            time.sleep(random.uniform(0.1, 0.25))
        resolved = resolver.resolve_item(
            douban_id=douban_id,
            title=title,
            is_tv=is_tv,
            tmdb_api_key=config.TMDB_API_KEY,
            omdb_api_key=config.OMDB_API_KEY,
        )
        return record, resolved

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

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for item, resolved in executor.map(resolve_single_record, douban_records):
                douban_id = str(item.get("douban_id", ""))
                title = item.get("title", "")
                raw_type = item.get("type", "movie")
                is_tv = raw_type == "tv"
                status = item.get("status", "done")  # done, mark, doing
                create_time = item.get("create_time", "")
                official_rating = item.get("official_rating") or item.get("rating")
                comment = item.get("comment", "") or ""

                # Check if this item is already marked as synced in local DB
                cached_sync_status = storage.get_sync_status(douban_id)

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
    parser.add_argument("--crawl", action="store_true", help="Force online crawling from Douban even if local archive file exists")
    parser.add_argument("--threads", type=int, default=3, help="Concurrent workers for resolving IMDb IDs (default: 3, max: 5)")

    args = parser.parse_args()
    run_pipeline(
        input_file=args.input,
        dry_run=args.dry_run,
        db_path=args.db,
        limit=args.limit,
        batch_size=args.batch_size,
        skip_auth=args.skip_auth,
        force_crawl=args.crawl,
        threads=args.threads,
    )


if __name__ == "__main__":
    main()
