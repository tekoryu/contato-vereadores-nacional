"""Promote validator-resolved final_url to canonical in prefeituras.csv.

For every (ibge_code, kind) row in `data/silver/url-validation.jsonl` where:
    status == "redirect_off_domain"
    error  == "redirected but municipio name found"

we replace the original `prefeitura_url` / `camara_url` value in
`data/silver/prefeituras.csv` with the validator's `final_url`. A backup is
written to `prefeituras.csv.bak` before any change.

Run idempotently — re-running after URLs are already promoted is a no-op
(those rows will now resolve to status=ok on a fresh validation run).
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data/silver/prefeituras.csv"
JSONL_PATH = ROOT / "data/silver/url-validation.jsonl"
BACKUP_PATH = ROOT / "data/silver/prefeituras.csv.bak"


def load_promotions() -> dict[tuple[str, str], str]:
    promo: dict[tuple[str, str], str] = {}
    with JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("status") != "redirect_off_domain":
                continue
            if r.get("error") != "redirected but municipio name found":
                continue
            final = (r.get("final_url") or "").strip()
            if not final:
                continue
            promo[(r["ibge_code"], r["kind"])] = final
    return promo


def main() -> int:
    promotions = load_promotions()
    print(f"candidates to promote: {len(promotions)}")

    shutil.copyfile(CSV_PATH, BACKUP_PATH)
    print(f"backup written to: {BACKUP_PATH}")

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    fieldnames = list(rows[0].keys())

    changed = 0
    unchanged = 0
    for r in rows:
        ibge = r["ibge_code"].strip()
        for kind in ("prefeitura", "camara"):
            col = f"{kind}_url"
            new = promotions.get((ibge, kind))
            if not new:
                continue
            if r[col].strip() == new:
                unchanged += 1
                continue
            r[col] = new
            changed += 1

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"URLs promoted:      {changed}")
    print(f"already canonical:  {unchanged}")
    print(f"wrote: {CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
