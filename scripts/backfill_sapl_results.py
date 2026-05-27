"""Backfill results.jsonl with vereadores already harvested via SAPL.

Reads `data/silver/vereadores-sapl.jsonl` (produced by `sapl_harvest.py`)
and matches each entry against the target list in
`data/silver/vereadores-completo.json` by `ibge_code` + name token overlap.

Writes one record per match with contact info to `data/silver/results.jsonl`,
in the same schema the pipeline uses. Skips:

  - non-matches (left for the crawler to try)
  - matches with neither email nor telefone (also left for the crawler)
  - vereadores already present in `results.jsonl`

Resumable: re-running is a no-op once everything is backfilled.
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INPUT_TARGETS = ROOT / "data/silver/vereadores-completo.json"
INPUT_SAPL = ROOT / "data/silver/vereadores-sapl.jsonl"
OUT_RESULTS = ROOT / "data/silver/results.jsonl"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def best_match(name: str, candidates: list[dict]) -> dict | None:
    """Pick the SAPL record whose normalized tokens best overlap with `name`."""
    target = set(norm(name).split())
    if not target:
        return None
    best_overlap = 0
    best_rec: dict | None = None
    for rec in candidates:
        cand_tokens = set(norm(rec.get("nome_parlamentar", "")).split())
        cand_tokens |= set(norm(rec.get("nome_completo", "")).split())
        overlap = len(target & cand_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_rec = rec
    return best_rec if best_overlap >= 1 else None


def pick_telefone(rec: dict) -> str | None:
    """Prefer mobile, fall back to landline."""
    tel = (rec.get("telefone_celular") or rec.get("telefone") or "").strip()
    return tel or None


def load_sapl_index() -> dict[str, list[dict]]:
    """Group active SAPL records by ibge_code."""
    idx: dict[str, list[dict]] = defaultdict(list)
    with INPUT_SAPL.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if not rec.get("ativo"):
                continue
            ibge = (rec.get("ibge_code") or "").strip()
            if ibge:
                idx[ibge].append(rec)
    return idx


def load_processed() -> set[int]:
    if not OUT_RESULTS.exists():
        return set()
    seen: set[int] = set()
    with OUT_RESULTS.open(encoding="utf-8") as f:
        for line in f:
            seen.add(json.loads(line)["candidato_seq"])
    return seen


def main() -> int:
    if not INPUT_SAPL.exists():
        print(f"missing {INPUT_SAPL}", file=sys.stderr)
        return 1
    if not INPUT_TARGETS.exists():
        print(f"missing {INPUT_TARGETS}", file=sys.stderr)
        return 1

    sapl_by_ibge = load_sapl_index()
    print(f"SAPL records: active parlamentares grouped into {len(sapl_by_ibge)} câmaras")

    with INPUT_TARGETS.open(encoding="utf-8") as f:
        targets = json.load(f)
    print(f"Targets: {len(targets)} vereadores")

    processed = load_processed()
    print(f"Already in results.jsonl: {len(processed)}")

    OUT_RESULTS.parent.mkdir(parents=True, exist_ok=True)

    stats = {"matched_with_contact": 0, "matched_no_contact": 0, "no_camara_sapl": 0, "no_name_match": 0}
    written = 0

    with OUT_RESULTS.open("a", encoding="utf-8") as out:
        for t in targets:
            seq = t["candidato_seq"]
            if seq in processed:
                continue

            ibge = str(t.get("municipio_ibge_id") or "").strip()
            pool = sapl_by_ibge.get(ibge)
            if not pool:
                stats["no_camara_sapl"] += 1
                continue

            match = best_match(t["candidato_nome_urna"], pool)
            if not match:
                stats["no_name_match"] += 1
                continue

            email = (match.get("email") or "").strip() or None
            tel = pick_telefone(match)
            if not email and not tel:
                stats["matched_no_contact"] += 1
                continue

            record = {
                "candidato_seq": seq,
                "email": email,
                "telefone": tel,
                "source_url": match.get("sapl_base") or match.get("sapl_url"),
                "source": "sapl",
                "status": "found",
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            stats["matched_with_contact"] += 1

    print()
    print("=" * 60)
    print(f"Written:                {written}")
    print(f"Matched with contact:   {stats['matched_with_contact']}")
    print(f"Matched, no contact:    {stats['matched_no_contact']}  (left for crawler)")
    print(f"No câmara in SAPL:      {stats['no_camara_sapl']}      (left for crawler)")
    print(f"No name match in SAPL:  {stats['no_name_match']}    (left for crawler)")
    print(f"Output: {OUT_RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
