import argparse
import asyncio
import datetime
import json
import logging
import time

from playwright.async_api import async_playwright

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


async def results_writer(results_path: str, queue: asyncio.Queue) -> None:
    """Single consumer for results.jsonl — no append races possible.

    Workers don't write to the file directly. They put dicts onto `queue`
    and this single task drains the queue, writing one line at a time.
    Because only one coroutine ever opens the file, two workers can never
    interleave their bytes. `None` is the shutdown sentinel.
    """
    with open(results_path, "a") as f:
        while True:
            # `await queue.get()` parks this task whenever the queue is empty,
            # so it costs nothing when no work is coming in.
            record = await queue.get()
            if record is None:
                queue.task_done()
                return
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            queue.task_done()


def record_dead_url(url: str, error: Exception, dead_urls: dict) -> None:
    """Synchronous: mutates dict + dumps file. Safe under asyncio (no awaits).

    Because there's no `await` inside, the entire read-modify-write runs
    atomically from the event loop's perspective — no other worker can
    sneak in between the dict update and the file dump. This is why a
    plain sync function is correct here without any locks.
    """
    key = url.rstrip("/")
    today = str(datetime.date.today())

    msg = str(error)
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

    with open(DEAD_URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(dead_urls, f, indent=2, ensure_ascii=False)


async def worker(
    name: str,
    browser,
    in_q: asyncio.Queue,
    results_q: asyncio.Queue,
    url_map: dict,
    dead_urls: dict,
    model: str,
    decision_log: DecisionLogger,
    logger: logging.Logger,
) -> None:
    # One worker = one independent instance of this coroutine. We spawn N of
    # them upfront (see `run` below). They all share the same in_q/results_q
    # and compete to grab politicians from the queue. First-free-wins.
    while True:
        # `await in_q.get()` parks this worker until a politician is
        # available. While parked, the event loop runs other workers.
        item = await in_q.get()
        if item is None:
            # Shutdown sentinel: producer puts one `None` per worker after
            # the last politician, so each worker exits cleanly.
            in_q.task_done()
            return
        i, total, politician = item
        seq = politician["candidato_seq"]
        pname = politician["candidato_nome_urna"]
        ibge_id = int(politician["municipio_ibge_id"])
        urls = url_map.get(ibge_id)

        if not urls:
            logger.warning(f"[{name}][{i+1}/{total}] No URL entry for ibge_id={ibge_id} ({pname}), skipping.")
            await results_q.put({
                "candidato_seq": seq, "email": None, "telefone": None,
                "source_url": None, "source": None, "status": "no_url",
            })
            in_q.task_done()
            continue

        candidates = [urls.get("camara_url"), urls.get("prefeitura_url")]
        candidates = [u for u in candidates if u and str(u) != "nan"]

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
        had_error = False
        t_start = time.perf_counter()

        for url in candidates:
            key = url.rstrip("/")
            if key in dead_urls:
                entry = dead_urls[key]
                logger.warning(f"  [{name}] Skipping dead URL ({entry['error_type']}, {entry['count']}x since {entry['first_seen']}): {url}")
                had_error = True
                continue

            logger.info(f"  [{name}] Trying: {url}")
            try:
                result = await crawl_for_email(browser, url, pname, model, decision_log=decision_log)
            except Exception as e:
                logger.error(f"  [{name}] Error crawling {url}: {e}")
                record_dead_url(url, e, dead_urls)
                had_error = True
                continue

            if result:
                email, source_url = result
                break

        elapsed = time.perf_counter() - t_start

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


async def run(input_path: str, results_path: str, model: str, concurrency: int) -> None:
    logger = setup_logging()
    decision_log = DecisionLogger()
    dead_urls = load_dead_urls(DEAD_URLS_PATH)

    politicians, url_map = load_data(input_path)
    processed = load_processed_ids(results_path)
    todo = [p for p in politicians if p["candidato_seq"] not in processed]

    total = len(politicians)
    logger.info(f"Input: {input_path} | Results: {results_path} | Model: {model}")
    logger.info(f"Total: {total} | Already processed: {len(processed)} | Remaining: {len(todo)} | Workers: {concurrency}")
    logger.info(f"Dead URL cache: {len(dead_urls)} entries")

    # `in_q` has bounded size so the producer doesn't load 30k items into
    # memory at once. When it's full, `await in_q.put(...)` parks the
    # producer until a worker drains a slot — natural backpressure.
    in_q: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 2)
    results_q: asyncio.Queue = asyncio.Queue()

    async with async_playwright() as p:
        # One shared Chromium process. Each fetch opens a fresh "context"
        # (like a private tab) — cheap to create, isolates cookies/state.
        browser = await p.chromium.launch(headless=True)
        try:
            # The writer task starts parked on `results_q.get()`. It will
            # wake up every time a worker puts a record onto the queue.
            writer = asyncio.create_task(results_writer(results_path, results_q))

            # Spawn N worker coroutines upfront. All N exist simultaneously
            # from this point on — they are NOT created on demand. Each one
            # is parked on `in_q.get()` waiting for its first politician.
            # The event loop is the dispatcher that switches between them
            # whenever one hits an `await`.
            workers = [
                asyncio.create_task(
                    worker(f"w{i}", browser, in_q, results_q, url_map, dead_urls,
                           model, decision_log, logger)
                )
                for i in range(concurrency)
            ]

            # Producer: feed politicians into the queue. Each `await put`
            # may park briefly if the queue is full (see maxsize above).
            for i, pol in enumerate(todo):
                await in_q.put((i, total, pol))
            # One `None` sentinel per worker so each one exits its loop.
            for _ in workers:
                await in_q.put(None)

            # Wait for all workers to drain the queue and return.
            await asyncio.gather(*workers)
            # Now that no more records are coming, tell the writer to stop.
            await results_q.put(None)
            await writer
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Async crawl for politician emails.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--results", default=DEFAULT_RESULTS)
    parser.add_argument("--model", default="qwen2.5:14b")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(run(args.input, args.results, args.model, args.concurrency))
