import argparse
import datetime
import json
import os
import time

import pandas as pd

from fetcher import crawl_for_email
from logging_setup import DecisionLogger, setup_logging

DEFAULT_INPUT = "data/silver/vereadores-completo.json"
DEFAULT_RESULTS = "data/silver/results.jsonl"
DEAD_URLS_PATH = "data/silver/dead_urls.json"


def load_data(input_path: str) -> tuple[list[dict], dict]:
    with open(input_path) as f:
        politicians = json.load(f)

    prefeituras = pd.read_csv("data/silver/prefeituras.csv")
    url_map = (
        prefeituras
        .set_index("ibge_code")[["camara_url", "prefeitura_url"]]
        .to_dict("index")
    )

    return politicians, url_map


def load_processed_ids(results_path: str) -> set:
    if not os.path.exists(results_path):
        return set()

    processed = set()
    with open(results_path) as f:
        for line in f:
            record = json.loads(line)
            processed.add(record["candidato_seq"])
    return processed


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
    with open(results_path, "a") as f:
        record = {
            "candidato_seq": candidato_seq,
            "email": email,
            "telefone": telefone,
            "source_url": source_url,
            "source": source,
            "status": status,
        }
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_dead_urls(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def record_dead_url(path: str, url: str, error: Exception, dead_urls: dict) -> None:
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

    with open(path, "w", encoding="utf-8") as f:
        json.dump(dead_urls, f, indent=2, ensure_ascii=False)


def run(input_path: str, results_path: str, model: str) -> None:
    logger = setup_logging()
    decision_log = DecisionLogger()
    dead_urls = load_dead_urls(DEAD_URLS_PATH)

    politicians, url_map = load_data(input_path)
    processed = load_processed_ids(results_path)

    total = len(politicians)
    skipped = len(processed)
    logger.info(f"Input: {input_path} | Results: {results_path} | Model: {model}")
    logger.info(f"Total: {total} | Already processed: {skipped} | Remaining: {total - skipped}")
    logger.info(f"Dead URL cache: {len(dead_urls)} entries")

    for i, politician in enumerate(politicians):
        seq = politician["candidato_seq"]
        name = politician["candidato_nome_urna"]

        if seq in processed:
            continue

        ibge_id = int(politician["municipio_ibge_id"])
        urls = url_map.get(ibge_id)

        if not urls:
            logger.warning(f"[{i+1}/{total}] No URL entry for ibge_id={ibge_id} ({name}, {politician['municipio_nome']}), skipping.")
            write_result(results_path, seq, None, "no_url")
            continue

        candidates = [urls.get("camara_url"), urls.get("prefeitura_url")]
        candidates = [u for u in candidates if u and str(u) != "nan"]

        if not candidates:
            logger.warning(f"[{i+1}/{total}] No URLs for {politician['municipio_nome']}, skipping.")
            write_result(results_path, seq, None, "no_url")
            continue

        logger.info(f"[{i+1}/{total}] {name} — {politician['municipio_nome']}")

        email: str | None = None
        source_url: str | None = None
        had_error = False
        t_start = time.perf_counter()

        for url in candidates:
            key = url.rstrip("/")
            if key in dead_urls:
                entry = dead_urls[key]
                logger.warning(f"  Skipping dead URL ({entry['error_type']}, {entry['count']}x since {entry['first_seen']}): {url}")
                had_error = True
                continue

            logger.info(f"  Trying: {url}")
            try:
                result = crawl_for_email(url, name, model, decision_log=decision_log)
            except Exception as e:
                logger.error(f"  Error crawling {url}: {e}")
                record_dead_url(DEAD_URLS_PATH, url, e, dead_urls)
                had_error = True
                continue

            if result:
                email, source_url = result
                break

        elapsed = time.perf_counter() - t_start

        if email:
            write_result(results_path, seq, email, "found", source_url, source="crawler")
            logger.info(f"  → found: email={email} via crawler ({elapsed:.1f}s)")
        elif had_error:
            write_result(results_path, seq, None, "error")
            logger.info(f"  → error ({elapsed:.1f}s)")
        else:
            write_result(results_path, seq, None, "not_found")
            logger.info(f"  → not_found ({elapsed:.1f}s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl for politician emails.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to politicians JSON file.")
    parser.add_argument("--results", default=DEFAULT_RESULTS, help="Path to JSONL results file.")
    parser.add_argument("--model", default="qwen2.5:14b", help="Ollama model to use.")
    args = parser.parse_args()
    run(args.input, args.results, args.model)
