import argparse
import json
import os
import time

import pandas as pd

from fetcher import crawl_for_email
from logging_setup import DecisionLogger, setup_logging

DEFAULT_INPUT = "data/silver/vereadores-completo.json"
DEFAULT_RESULTS = "data/silver/results.jsonl"


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


def write_result(results_path: str, candidato_seq: int, email: str | None, status: str, source_url: str | None = None) -> None:
    with open(results_path, "a") as f:
        record = {"candidato_seq": candidato_seq, "email": email, "source_url": source_url, "status": status}
        f.write(json.dumps(record) + "\n")


def run(input_path: str, results_path: str, model: str) -> None:
    logger = setup_logging()
    decision_log = DecisionLogger()

    politicians, url_map = load_data(input_path)
    processed = load_processed_ids(results_path)

    total = len(politicians)
    skipped = len(processed)
    logger.info(f"Input: {input_path} | Results: {results_path} | Model: {model}")
    logger.info(f"Total: {total} | Already processed: {skipped} | Remaining: {total - skipped}")

    for i, politician in enumerate(politicians):
        seq = politician["candidato_seq"]
        name = politician["candidato_nome_urna"]

        if seq in processed:
            continue

        ibge_id = int(politician["municipio_ibge_id"])
        urls = url_map.get(ibge_id)

        if not urls:
            raise ValueError(f"No URL entry for ibge_id={ibge_id} ({name}, {politician['municipio_nome']})")

        candidates = [urls.get("camara_url"), urls.get("prefeitura_url")]
        candidates = [u for u in candidates if u and str(u) != "nan"]

        if not candidates:
            logger.warning(f"[{i+1}/{total}] No URLs for {politician['municipio_nome']}, skipping.")
            write_result(results_path, seq, None, "no_url")
            continue

        logger.info(f"[{i+1}/{total}] {name} — {politician['municipio_nome']}")

        email = None
        source_url = None
        had_error = False
        t_start = time.perf_counter()

        for url in candidates:
            logger.info(f"  Trying: {url}")
            try:
                result = crawl_for_email(url, name, model, decision_log=decision_log)
            except Exception as e:
                logger.error(f"  Error crawling {url}: {e}")
                had_error = True
                continue
            if result:
                email, source_url = result
                break

        elapsed = time.perf_counter() - t_start

        if email:
            write_result(results_path, seq, email, "found", source_url)
            logger.info(f"  → found: {email} ({elapsed:.1f}s)")
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
