"""Synchronous pipeline — crawl Brazilian municipal websites for councillor emails.

Entry point::

    python src/pipeline.py [--input PATH] [--results PATH] [--model NAME]

The pipeline is **resumable**: politicians already present in the results
file are skipped on subsequent runs.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time
from pathlib import Path

import pandas as pd

from config import cfg
from fetcher import crawl_for_email
from logging_setup import DecisionLogger, setup_logging

# ── defaults (resolved from settings.toml via cfg) ────────────────────────────
DEFAULT_INPUT: str = str(cfg.paths.vereadores_json)
DEFAULT_RESULTS: str = str(cfg.paths.results_jsonl)
DEAD_URLS_PATH: str = str(cfg.paths.dead_urls_json)


# ── data loading ──────────────────────────────────────────────────────────────

def load_data(input_path: str) -> tuple[list[dict], dict[int, dict[str, str]]]:
    """Load the target politician roster and build an IBGE-code → URL map.

    Returns:
        politicians: list of politician dicts from the input JSON.
        url_map: mapping of ``ibge_code`` → ``{"camara_url": ..., "prefeitura_url": ...}``.
    """
    with open(input_path, encoding="utf-8") as fh:
        politicians: list[dict] = json.load(fh)

    prefeituras = pd.read_csv(str(cfg.paths.prefeituras_csv))
    url_map: dict[int, dict[str, str]] = (
        prefeituras
        .set_index("ibge_code")[["camara_url", "prefeitura_url"]]
        .to_dict("index")
    )

    return politicians, url_map


def load_processed_ids(results_path: str) -> set[int]:
    """Return the set of ``candidato_seq`` values already written to *results_path*."""
    if not os.path.exists(results_path):
        return set()

    processed: set[int] = set()
    with open(results_path, encoding="utf-8") as fh:
        for line in fh:
            record: dict = json.loads(line)
            processed.add(record["candidato_seq"])
    return processed


# ── result persistence ────────────────────────────────────────────────────────

def write_result(
    results_path: str,
    candidato_seq: int,
    email: str | None,
    status: str,
    source_url: str | None = None,
    *,
    telefone: str | None = None,
    source: str | None = None,
) -> None:
    """Append a single result record to the JSONL output file."""
    record: dict = {
        "candidato_seq": candidato_seq,
        "email": email,
        "telefone": telefone,
        "source_url": source_url,
        "source": source,
        "status": status,
    }
    with open(results_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── dead-URL cache ────────────────────────────────────────────────────────────

def load_dead_urls(path: str) -> dict[str, dict]:
    """Load the dead-URL cache from disk, returning an empty dict if absent."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def record_dead_url(
    path: str,
    url: str,
    error: Exception,
    dead_urls: dict[str, dict],
) -> None:
    """Classify *error*, update *dead_urls* in place, and persist to *path*."""
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

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dead_urls, fh, indent=2, ensure_ascii=False)


# ── main loop ─────────────────────────────────────────────────────────────────

def run(input_path: str, results_path: str, model: str) -> None:
    """Run the synchronous crawl pipeline."""
    logger = setup_logging()
    decision_log = DecisionLogger()
    dead_urls: dict[str, dict] = load_dead_urls(DEAD_URLS_PATH)

    politicians, url_map = load_data(input_path)
    processed: set[int] = load_processed_ids(results_path)

    total: int = len(politicians)
    skipped: int = len(processed)
    logger.info(f"Input: {input_path} | Results: {results_path} | Model: {model}")
    logger.info(f"Total: {total} | Already processed: {skipped} | Remaining: {total - skipped}")
    logger.info(f"Dead URL cache: {len(dead_urls)} entries")

    for i, politician in enumerate(politicians):
        seq: int = politician["candidato_seq"]
        name: str = politician["candidato_nome_urna"]

        if seq in processed:
            continue

        ibge_id: int = int(politician["municipio_ibge_id"])
        urls: dict[str, str] | None = url_map.get(ibge_id)

        if not urls:
            logger.warning(
                f"[{i+1}/{total}] No URL entry for ibge_id={ibge_id} "
                f"({name}, {politician['municipio_nome']}), skipping."
            )
            write_result(results_path, seq, None, "no_url")
            continue

        candidates: list[str] = [urls.get("camara_url", ""), urls.get("prefeitura_url", "")]
        candidates = [u for u in candidates if u and u != "nan"]

        if not candidates:
            logger.warning(f"[{i+1}/{total}] No URLs for {politician['municipio_nome']}, skipping.")
            write_result(results_path, seq, None, "no_url")
            continue

        logger.info(f"[{i+1}/{total}] {name} — {politician['municipio_nome']}")

        email: str | None = None
        source_url: str | None = None
        had_error: bool = False
        t_start: float = time.perf_counter()

        for url in candidates:
            key: str = url.rstrip("/")
            if key in dead_urls:
                entry = dead_urls[key]
                logger.warning(
                    f"  Skipping dead URL ({entry['error_type']}, "
                    f"{entry['count']}x since {entry['first_seen']}): {url}"
                )
                had_error = True
                continue

            logger.info(f"  Trying: {url}")
            try:
                result: tuple[str, str] | None = crawl_for_email(
                    url, name, model, decision_log=decision_log
                )
            except Exception as exc:
                logger.error(f"  Error crawling {url}: {exc}")
                record_dead_url(DEAD_URLS_PATH, url, exc, dead_urls)
                had_error = True
                continue

            if result:
                email, source_url = result
                break

        elapsed: float = time.perf_counter() - t_start

        if email:
            write_result(results_path, seq, email, "found", source_url, source="crawler")
            logger.info(f"  → found: email={email} via crawler ({elapsed:.1f}s)")
        elif had_error:
            write_result(results_path, seq, None, "error")
            logger.info(f"  → error ({elapsed:.1f}s)")
        else:
            write_result(results_path, seq, None, "not_found")
            logger.info(f"  → not_found ({elapsed:.1f}s)")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Crawl Brazilian municipal websites for councillor contact emails."
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"Path to politicians JSON file (default: {DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--results",
        default=DEFAULT_RESULTS,
        help=f"Path to JSONL results file (default: {DEFAULT_RESULTS}).",
    )
    parser.add_argument(
        "--model",
        default=cfg.model,
        help=f"Ollama model to use (default: {cfg.model}).",
    )
    args = parser.parse_args()
    run(args.input, args.results, args.model)
