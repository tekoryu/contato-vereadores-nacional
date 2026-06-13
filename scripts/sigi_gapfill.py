"""Pass 1: SIGI gap-fill for missing/broken câmara URLs.

For every (ibge_code, kind="camara") in url-validation.jsonl with status
NOT in {ok, ok_unverified}, look up SIGI by (uf, normalized municipio) to
find an Interlegis-registered URL. If SIGI proposes a URL we don't already
have, validate it. If validation passes, promote to prefeituras.csv and
replace the bad row in url-validation.jsonl.

Dry-run mode (default) reports candidates without changing files.
Pass --apply to actually write changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from config import cfg  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))

import validate_urls as vu  # noqa: E402

CSV_PATH: Path = cfg.paths.prefeituras_csv
JSONL_PATH: Path = cfg.paths.url_validation_jsonl
SIGI_PATH: Path = cfg.paths.sigi_casas_csv

GOOD = {"ok", "ok_unverified"}
WORKERS = 24


def norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u.rstrip("/")


def load_sigi_camaras() -> dict[tuple[str, str], str]:
    idx: dict[tuple[str, str], str] = {}
    with SIGI_PATH.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["casa_legislativa__tipo__nome"] != "Câmara Municipal":
                continue
            url = (r.get("url") or "").strip()
            if not url:
                continue
            key = (
                r["casa_legislativa__municipio__uf__sigla"].strip(),
                norm(r["casa_legislativa__municipio__nome"]),
            )
            idx.setdefault(key, url)
    return idx


def load_validation() -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            out[(r["ibge_code"], r["kind"])] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes to disk")
    args = ap.parse_args()

    sigi = load_sigi_camaras()
    print(f"SIGI câmara entries (with URL): {len(sigi)}")

    validation = load_validation()
    prefeituras = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    by_ibge = {r["ibge_code"].strip(): r for r in prefeituras}

    candidates: list[tuple[str, str, str, str, str]] = []
    no_sigi_hit = 0
    same_as_current = 0
    for r in prefeituras:
        ibge = r["ibge_code"].strip()
        uf = r["uf"].strip()
        muni = r["ibge_name"].strip()
        v = validation.get((ibge, "camara"))
        if not v:
            continue
        if v["status"] in GOOD:
            continue
        sigi_url = sigi.get((uf, norm(muni)))
        if not sigi_url:
            no_sigi_hit += 1
            continue
        cand = normalize_url(sigi_url)
        cur = normalize_url(r["camara_url"])
        if cand == cur:
            same_as_current += 1
            continue
        candidates.append((ibge, uf, muni, cand, v["status"]))

    print(f"bad câmara rows: {sum(1 for r in prefeituras if validation.get((r['ibge_code'].strip(), 'camara'), {}).get('status') not in GOOD)}")
    print(f"  → SIGI proposed new URL:   {len(candidates)}")
    print(f"  → SIGI URL == current:     {same_as_current}")
    print(f"  → no SIGI entry:           {no_sigi_hit}")

    if not candidates:
        return 0

    print(f"\nValidating {len(candidates)} candidates...")
    dead = vu.load_dead_urls()
    results: dict[str, dict] = {}
    from collections import Counter
    statuses: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(vu.classify, cand, muni, uf, dead): (ibge, uf, muni, cand, prev)
            for ibge, uf, muni, cand, prev in candidates
        }
        for i, fut in enumerate(as_completed(futures), 1):
            ibge, uf, muni, cand, prev = futures[fut]
            try:
                out = fut.result()
            except Exception as e:  # noqa: BLE001
                out = {"status": "exception", "http_status": None, "final_url": cand, "elapsed_s": 0, "error": str(e)[:120]}
            results[ibge] = {"uf": uf, "muni": muni, "cand": cand, "prev_status": prev, **out}
            statuses[out["status"]] += 1
            if i % 50 == 0 or i == len(candidates):
                print(f"  [{i:4d}/{len(candidates)}] {dict(statuses.most_common(5))}")

    recovered = [r for r in results.values() if r["status"] in GOOD]
    print(f"\nValidation outcome for {len(candidates)} SIGI candidates:")
    for k, v in statuses.most_common():
        flag = "✓" if k in GOOD else " "
        print(f"  {flag} {k:22s} {v:5d}")
    print(f"\nRECOVERABLE câmara URLs: {len(recovered)}")

    if not args.apply:
        print("\n(dry-run — re-run with --apply to write changes)")
        return 0

    # Apply: update prefeituras.csv (camara_url) and url-validation.jsonl
    shutil.copyfile(CSV_PATH, CSV_PATH.with_suffix(".csv.bak2"))
    n_changed = 0
    for r in prefeituras:
        ibge = r["ibge_code"].strip()
        res = results.get(ibge)
        if not res or res["status"] not in GOOD:
            continue
        # promote final_url (validator's canonical) over the raw SIGI url
        r["camara_url"] = res["final_url"] or res["cand"]
        n_changed += 1
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(prefeituras[0].keys()))
        w.writeheader()
        w.writerows(prefeituras)
    print(f"prefeituras.csv: {n_changed} câmara_url rows updated")

    # update JSONL: evict old bad rows, append new validated rows
    promoted_ibges = {ibge for ibge, r in results.items() if r["status"] in GOOD}
    lines = JSONL_PATH.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    for line in lines:
        rec = json.loads(line)
        if rec["kind"] == "camara" and rec["ibge_code"] in promoted_ibges:
            continue
        kept.append(line)
    for ibge, res in results.items():
        if res["status"] not in GOOD:
            continue
        rec = {
            "ibge_code": ibge, "kind": "camara",
            "municipio": res["muni"], "uf": res["uf"],
            "url": by_ibge[ibge]["camara_url"],
            "status": res["status"],
            "http_status": res["http_status"],
            "final_url": res["final_url"],
            "elapsed_s": res["elapsed_s"],
            "error": res["error"],
        }
        kept.append(json.dumps(rec, ensure_ascii=False))
    JSONL_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"url-validation.jsonl: {len(promoted_ibges)} rows replaced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
