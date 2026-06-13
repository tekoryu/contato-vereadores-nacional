"""Asynchronous pipeline — concurrent crawl of municipal websites for councillor emails.

Uses an asyncio producer/workers/writer architecture to run multiple
Playwright browser contexts in parallel while sharing a single Ollama
inference server.

Entry point::

    python src/pipeline_async.py [--input PATH] [--results PATH]
                                  [--model NAME] [--concurrency N]

See ``docs/ASYNC_FLOW.md`` for a detailed diagram of the concurrency model.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import time

from playwright.async_api import async_playwright

from config import cfg
from fetcher_async import crawl_for_email
from logging_setup import DecisionLogger, setup_logging
from pipeline import (
    DEAD_URLS_PATH,
    DEFAULT_INPUT,
    DEFAULT_RESULTS,
    load_data,
    load_dead_urls,
    load_processed_ids,
)


# ── writer ────────────────────────────────────────────────────────────────────

async def results_writer(results_path: str, queue: asyncio.Queue[dict | None]) -> None:
    """Single consumer for the results JSONL file — prevents write-interleaving.

    Workers never write to the file directly; they put dicts onto *queue*.
    This task drains the queue one record at a time.  ``None`` is the
    shutdown sentinel.
    """
    with open(results_path, "a", encoding="utf-8") as fh:
        while True:
            record = await queue.get()
            if record is None:
                queue.task_done()
                return
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            queue.task_done()


# ── dead-URL cache (sync, asyncio-safe) ───────────────────────────────────────

def record_dead_url(url: str, error: Exception, dead_urls: dict[str, dict]) -> None:
    """Classify *error*, update *dead_urls* in place, and persist to disk.

    This function is intentionally synchronous: it contains no ``await``,
    so the entire read-modify-write is atomic from the event loop's
    perspective — no two workers can interleave inside it.
    """
    key: str = url.rstrip("/")
    today: str = str(datetime.date.today())

    msg: str = str(error)
    if "ERR_NAME_NOT_RESOLVED" in msg:
        error_type = "dns_error"
    elif "Timeout" in msg:
        error_type = "timeout"
    elif "Download is starting" in msg:
        error_type = "download"
    else:
        error_type = "other"

    if key in dead_urls:
        dead_urls[key]["count"] += 1
        dead_urls[key]["last_seen"] = today
        dead_urls[key]["error_type"] = error_type
    else:
        dead_urls[key] = {
            "error_type": error_type,
            "first_seen": today,
            "last_seen": today,
            "count": 1,
        }

    with open(DEAD_URLS_PATH, "w", encoding="utf-8") as fh:
        json.dump(dead_urls, fh, indent=2, ensure_ascii=False)


# ── worker ────────────────────────────────────────────────────────────────────

async def worker(
    name: str,
    browser,
    in_q: asyncio.Queue[tuple[int, int, dict] | None],
    results_q: asyncio.Queue[dict | None],
    url_map: dict[int, dict[str, str]],
    dead_urls: dict[str, dict],
    model: str,
    decision_log: DecisionLogger,
    logger: logging.Logger,
) -> None:
    """Consume politicians from *in_q*, crawl for emails, push results to *results_q*."""
    while True:
        item = await in_q.get()
        if item is None:
            in_q.task_done()
            return

        i, total, politician = item
        seq: int = politician["candidato_seq"]
        pname: str = politician["candidato_nome_urna"]
        ibge_id: int = int(politician["municipio_ibge_id"])
        urls: dict[str, str] | None = url_map.get(ibge_id)

        if not urls:
            logger.warning(
                f"[{name}][{i+1}/{total}] No URL entry for ibge_id={ibge_id} ({pname}), skipping."
            )
            await results_q.put({
                "candidato_seq": seq, "email": None, "telefone": None,
                "source_url": None, "source": None, "status": "no_url",
            })
            in_q.task_done()
            continue

        candidates: list[str] = [urls.get("camara_url", ""), urls.get("prefeitura_url", "")]
        candidates = [u for u in candidates if u and u != "nan"]

        if not candidates:
            logger.warning(f"[{name}][{i+1}/{total}] No URLs for {politician['municipio_nome']}, skipping.")
            await results_q.put({
                "candidato_seq": seq, "email": None, "telefone": None,
                "source_url": None, "source": None, "status": "no_url",
            })
            in_q.task_done()
            continue

        logger.info(f"[{name}][{i+1}/{total}] {pname} — {politician['municipio_nome']}")

        email: str | None = None
        source_url: str | None = None
        had_error: bool = False
        t_start: float = time.perf_counter()

        for url in candidates:
            key: str = url.rstrip("/")
            if key in dead_urls:
                entry = dead_urls[key]
                logger.warning(
                    f"  [{name}] Skipping dead URL ({entry['error_type']}, "
                    f"{entry['count']}x since {entry['first_seen']}): {url}"
                )
                had_error = True
                continue

            logger.info(f"  [{name}] Trying: {url}")
            try:
                result: tuple[str, str] | None = await crawl_for_email(
                    browser, url, pname, model, decision_log=decision_log
                )
            except Exception as exc:
                logger.error(f"  [{name}] Error crawling {url}: {exc}")
                record_dead_url(url, exc, dead_urls)
                had_error = True
                continue

            if result:
                email, source_url = result
                break

        elapsed: float = time.perf_counter() - t_start

        if email:
            await results_q.put({
                "candidato_seq": seq, "email": email, "telefone": None,
                "source_url": source_url, "source": "crawler", "status": "found",
            })
            logger.info(f"  [{name}] → found: email={email} via crawler ({elapsed:.1f}s)")
        elif had_error:
            await results_q.put({
                "candidato_seq": seq, "email": None, "telefone": None,
                "source_url": None, "source": None, "status": "error",
            })
            logger.info(f"  [{name}] → error ({elapsed:.1f}s)")
        else:
            await results_q.put({
                "candidato_seq": seq, "email": None, "telefone": None,
                "source_url": None, "source": None, "status": "not_found",
            })
            logger.info(f"  [{name}] → not_found ({elapsed:.1f}s)")

        in_q.task_done()


# ── main coroutine ────────────────────────────────────────────────────────────

async def run(input_path: str, results_path: str, model: str, concurrency: int) -> None:
    """Launch the async pipeline with *concurrency* parallel Playwright workers."""
    logger = setup_logging()
    decision_log = DecisionLogger()
    dead_urls: dict[str, dict] = load_dead_urls(DEAD_URLS_PATH)

    politicians, url_map = load_data(input_path)
    processed: set[int] = load_processed_ids(results_path)
    todo: list[dict] = [p for p in politicians if p["candidato_seq"] not in processed]

    total: int = len(politicians)
    logger.info(f"Input: {input_path} | Results: {results_path} | Model: {model}")
    logger.info(
        f"Total: {total} | Already processed: {len(processed)} "
        f"| Remaining: {len(todo)} | Workers: {concurrency}"
    )
    logger.info(f"Dead URL cache: {len(dead_urls)} entries")

    in_q: asyncio.Queue[tuple[int, int, dict] | None] = asyncio.Queue(maxsize=concurrency * 2)
    results_q: asyncio.Queue[dict | None] = asyncio.Queue()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            writer_task = asyncio.create_task(results_writer(results_path, results_q))

            worker_tasks = [
                asyncio.create_task(
                    worker(
                        f"w{i}", browser, in_q, results_q,
                        url_map, dead_urls, model, decision_log, logger,
                    )
                )
                for i in range(concurrency)
            ]

            for i, pol in enumerate(todo):
                await in_q.put((i, total, pol))
            for _ in worker_tasks:
                await in_q.put(None)

            await asyncio.gather(*worker_tasks)
            await results_q.put(None)
            await writer_task
        finally:
            await browser.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Async crawl of Brazilian municipal websites for councillor emails."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--results", default=DEFAULT_RESULTS)
    parser.add_argument("--model", default=cfg.model)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=cfg.concurrency,
        help=f"Number of parallel Playwright workers (default: {cfg.concurrency}).",
    )
    args = parser.parse_args()
    asyncio.run(run(args.input, args.results, args.model, args.concurrency))
