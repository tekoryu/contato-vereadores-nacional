import json
import os
import sys

import pandas as pd

from fetcher import crawl_for_email

RESULTS_PATH = "data/silver/results.jsonl"


def load_data() -> tuple[list[dict], dict]:
    with open("data/silver/vereadores-completo.json") as f:
        politicians = json.load(f)

    prefeituras = pd.read_csv("data/silver/prefeituras.csv")
    url_map = (
        prefeituras
        .set_index("ibge_code")[["camara_url", "prefeitura_url"]]
        .to_dict("index")
    )

    return politicians, url_map


def load_processed_ids() -> set:
    if not os.path.exists(RESULTS_PATH):
        return set()

    processed = set()
    with open(RESULTS_PATH) as f:
        for line in f:
            record = json.loads(line)
            processed.add(record["candidato_seq"])
    return processed


def write_result(candidato_seq: int, email: str | None, status: str) -> None:
    with open(RESULTS_PATH, "a") as f:
        record = {"candidato_seq": candidato_seq, "email": email, "status": status}
        f.write(json.dumps(record) + "\n")


def run(model: str = "qwen2.5:14b") -> None:
    politicians, url_map = load_data()
    processed = load_processed_ids()

    total = len(politicians)
    skipped = len(processed)
    print(f"Total: {total} | Already processed: {skipped} | Remaining: {total - skipped}")

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
            print(f"  [{i+1}/{total}] No URLs for {politician['municipio_nome']}, skipping.")
            write_result(seq, None, "no_url")
            continue

        print(f"\n[{i+1}/{total}] {name} — {politician['municipio_nome']}")

        email = None
        had_error = False
        for url in candidates:
            print(f"  Trying: {url}")
            try:
                email = crawl_for_email(url, name, model)
            except Exception as e:
                print(f"  Error: {e}")
                had_error = True
                break
            if email:
                break

        if email:
            write_result(seq, email, "found")
        elif had_error:
            write_result(seq, None, "error")
        else:
            write_result(seq, None, "not_found")
        print(f"  → {'found: ' + email if email else 'not_found'}")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:14b"
    run(model)
