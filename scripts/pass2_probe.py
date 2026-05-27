"""Pass 2: heuristic pattern probe + validate for missing câmara URLs.

For each câmara with status ∉ {ok, ok_unverified} in url-validation.jsonl,
generate canonical URL candidates and run them through the validator. Keep
only candidates that pass content verification ('ok' or 'ok_unverified').

Candidate patterns (per município, lowercase NFKD-stripped, spaces removed):
    https://<muni>.<uf>.leg.br
    https://www.<muni>.<uf>.leg.br
    https://cm<muni>.<uf>.gov.br
    https://camara<muni>.<uf>.gov.br

Dry-run by default. Pass --apply to write changes to prefeituras.csv and
url-validation.jsonl. Filter to a single UF with --uf <SIGLA>.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_urls as vu  # noqa: E402

CSV_PATH = ROOT / "data/silver/prefeituras.csv"
JSONL_PATH = ROOT / "data/silver/url-validation.jsonl"

GOOD = {"ok", "ok_unverified"}
WORKERS = 32


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    return "".join(ch for ch in s if ch.isalnum())


def candidates_for(muni: str, uf: str) -> list[str]:
    m = slug(muni)
    u = uf.lower()
    return [
        f"https://{m}.{u}.leg.br",
        f"https://www.{m}.{u}.leg.br",
        f"https://cm{m}.{u}.gov.br",
        f"https://camara{m}.{u}.gov.br",
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uf", help="restrict to a single UF (e.g. MA)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    validation: dict[tuple[str, str], dict] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            validation[(r["ibge_code"], r["kind"])] = r

    prefeituras = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    by_ibge = {r["ibge_code"].strip(): r for r in prefeituras}

    targets: list[tuple[str, str, str]] = []
    for r in prefeituras:
        ibge = r["ibge_code"].strip()
        uf = r["uf"].strip()
        muni = r["ibge_name"].strip()
        if args.uf and uf.upper() != args.uf.upper():
            continue
        v = validation.get((ibge, "camara"))
        if v and v["status"] in GOOD:
            continue
        targets.append((ibge, uf, muni))

    print(f"targets: {len(targets)}{f' (uf={args.uf})' if args.uf else ''}")

    probes: list[tuple[str, str, str, str]] = []
    for ibge, uf, muni in targets:
        cur = (by_ibge[ibge].get("camara_url") or "").strip().rstrip("/")
        for cand in candidates_for(muni, uf):
            if cand.rstrip("/") == cur:
                continue
            probes.append((ibge, uf, muni, cand))
    print(f"candidate probes: {len(probes)}")

    dead = vu.load_dead_urls()
    per_muni: dict[str, list[dict]] = {}
    from collections import Counter
    statuses: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(vu.classify, cand, muni, uf, dead): (ibge, uf, muni, cand)
            for ibge, uf, muni, cand in probes
        }
        for i, fut in enumerate(as_completed(futures), 1):
            ibge, uf, muni, cand = futures[fut]
            try:
                out = fut.result()
            except Exception as e:  # noqa: BLE001
                out = {"status": "exception", "http_status": None, "final_url": cand, "elapsed_s": 0, "error": str(e)[:120]}
            per_muni.setdefault(ibge, []).append({"uf": uf, "muni": muni, "cand": cand, **out})
            statuses[out["status"]] += 1
            if i % 100 == 0 or i == len(probes):
                print(f"  [{i:5d}/{len(probes)}] {dict(statuses.most_common(5))}")

    # pick best per município: ok > ok_unverified, then fastest
    priority = {"ok": 0, "ok_unverified": 1}
    winners: dict[str, dict] = {}
    for ibge, results in per_muni.items():
        recoverable = [r for r in results if r["status"] in GOOD]
        if not recoverable:
            continue
        recoverable.sort(key=lambda r: (priority[r["status"]], r["elapsed_s"]))
        winners[ibge] = recoverable[0]

    print(f"\nProbe outcomes:")
    for k, v in statuses.most_common():
        print(f"  {k:22s} {v:5d}")
    print(f"\nRECOVERED câmaras: {len(winners)} / {len(targets)} targets")

    if not args.apply or not winners:
        if not args.apply:
            print("\n(dry-run — re-run with --apply to write changes)")
        return 0

    shutil.copyfile(CSV_PATH, CSV_PATH.with_suffix(".csv.bak3"))
    n_changed = 0
    for r in prefeituras:
        w = winners.get(r["ibge_code"].strip())
        if not w:
            continue
        r["camara_url"] = w["final_url"] or w["cand"]
        n_changed += 1
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(prefeituras[0].keys()))
        wr.writeheader()
        wr.writerows(prefeituras)
    print(f"prefeituras.csv: {n_changed} câmara_url rows updated")

    lines = JSONL_PATH.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    promoted = set(winners.keys())
    for line in lines:
        rec = json.loads(line)
        if rec["kind"] == "camara" and rec["ibge_code"] in promoted:
            continue
        kept.append(line)
    for ibge, w in winners.items():
        new_url = by_ibge[ibge]["camara_url"]
        rec = {
            "ibge_code": ibge, "kind": "camara",
            "municipio": w["muni"], "uf": w["uf"],
            "url": new_url,
            "status": w["status"],
            "http_status": w["http_status"],
            "final_url": w["final_url"],
            "elapsed_s": w["elapsed_s"],
            "error": w["error"],
        }
        kept.append(json.dumps(rec, ensure_ascii=False))
    JSONL_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"url-validation.jsonl: {len(promoted)} rows replaced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
