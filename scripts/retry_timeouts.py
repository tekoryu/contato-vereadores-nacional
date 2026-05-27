"""Re-check `timeout` URLs from url-validation.jsonl with a longer timeout.

Strategy: 30-second timeout + one retry. Updates the JSONL in place,
replacing the timed-out row with the new classification.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_urls as vu  # noqa: E402

vu.TIMEOUT_S = 30
WORKERS = 16
JSONL = ROOT / "data/silver/url-validation.jsonl"


def main() -> int:
    lines = JSONL.read_text(encoding="utf-8").splitlines()
    timeouts: list[dict] = []
    other: list[str] = []
    for line in lines:
        r = json.loads(line)
        if r.get("status") == "timeout":
            timeouts.append(r)
        else:
            other.append(line)

    print(f"timeouts to retry: {len(timeouts)} | keeping: {len(other)}")
    if not timeouts:
        return 0

    dead = vu.load_dead_urls()
    updated: list[str] = []
    from collections import Counter
    statuses: Counter[str] = Counter()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(vu.classify, r["url"], r["municipio"], r["uf"], dead): r
            for r in timeouts
        }
        for i, fut in enumerate(as_completed(futures), 1):
            r = futures[fut]
            try:
                out = fut.result()
            except Exception as e:  # noqa: BLE001
                out = {"status": "exception", "http_status": None, "final_url": r["url"], "elapsed_s": 0, "error": str(e)[:120]}
            rec = {
                "ibge_code": r["ibge_code"], "kind": r["kind"],
                "municipio": r["municipio"], "uf": r["uf"], "url": r["url"],
                **out,
            }
            updated.append(json.dumps(rec, ensure_ascii=False))
            statuses[out["status"]] += 1
            if i % 20 == 0 or i == len(timeouts):
                print(f"  [{i:3d}/{len(timeouts)}] {dict(statuses.most_common())}")

    JSONL.write_text("\n".join(other + updated) + "\n", encoding="utf-8")
    print("\nFinal status distribution for retried rows:")
    for k, v in statuses.most_common():
        print(f"  {k:22s} {v:5d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
