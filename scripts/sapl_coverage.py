"""Coverage probe for SAPL API across the 26-row sample.

Runs per politician:
  1. Look up câmara URL via SIGI index (UF + município name).
  2. Fall back to prefeituras.csv camara_url if no SIGI hit.
  3. Probe SAPL API at https://sapl.<host> and https://<host>.
  4. Try to match politician by name; report email if found.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from config import cfg  # noqa: E402
sys.path.insert(0, str(ROOT / "src"))

from sapl_client import fetch_parlamentares, load_sigi_index, match_parlamentar  # noqa: E402


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def load_prefeituras_index(path: Path) -> dict[str, str]:
    idx: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ibge = (r.get("ibge_code") or "").strip()
            url = (r.get("camara_url") or "").strip()
            if ibge and url:
                idx[ibge] = url
    return idx


def main() -> None:
    sample = json.loads((cfg.paths.vereadores_sample).read_text())
    sigi = load_sigi_index(str(cfg.paths.sigi_casas_csv))
    prefeituras = load_prefeituras_index(cfg.paths.prefeituras_csv)

    rows = []
    summary = {"total": len(sample), "sigi_hit": 0, "sapl_reachable": 0, "name_matched": 0, "email_found": 0}

    for i, p in enumerate(sample, 1):
        uf = p["uf_sigla"]
        muni = p["municipio_nome"]
        name = p["candidato_nome_urna"]
        ibge = str(p.get("municipio_ibge_id") or "")

        sigi_hit = sigi.get((uf, norm(muni)))
        host = ""
        host_source = ""
        if sigi_hit:
            host = sigi_hit["url"]
            host_source = "sigi"
            summary["sigi_hit"] += 1
        elif ibge in prefeituras:
            host = prefeituras[ibge]
            host_source = "prefeituras"

        t0 = time.time()
        result = fetch_parlamentares(host) if host else None
        elapsed = time.time() - t0

        sapl_ok = bool(result and result.reachable)
        match = match_parlamentar(name, result.parlamentares) if sapl_ok else None
        email = match.email if match else ""
        if sapl_ok:
            summary["sapl_reachable"] += 1
        if match:
            summary["name_matched"] += 1
        if email:
            summary["email_found"] += 1

        n_parl = len(result.parlamentares) if result else 0
        rows.append({
            "name": name, "uf": uf, "municipio": muni,
            "host_source": host_source, "host": host,
            "sapl_ok": sapl_ok, "n_parlamentares": n_parl,
            "matched": match.nome_parlamentar if match else "",
            "email": email, "elapsed_s": round(elapsed, 2),
        })
        flag = "✓" if email else ("·" if match else ("?" if sapl_ok else "✗"))
        print(f"{flag} [{i:2d}/{len(sample)}] {name[:30]:30s} {uf} {muni[:22]:22s} "
              f"src={host_source or '-':12s} sapl={'Y' if sapl_ok else 'N'} "
              f"parl={n_parl:3d} match={match.nome_parlamentar[:25] if match else '-':25s} "
              f"email={email or '-'} ({elapsed:.1f}s)")

    print()
    print("=" * 80)
    print(f"Total politicians:   {summary['total']}")
    print(f"  SIGI URL hit:      {summary['sigi_hit']} ({summary['sigi_hit']*100//summary['total']}%)")
    print(f"  SAPL reachable:    {summary['sapl_reachable']} ({summary['sapl_reachable']*100//summary['total']}%)")
    print(f"  Name matched:      {summary['name_matched']} ({summary['name_matched']*100//summary['total']}%)")
    print(f"  Email found:       {summary['email_found']} ({summary['email_found']*100//summary['total']}%)")
    print("=" * 80)

    out = cfg.paths.sapl_coverage_sample
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nWrote per-row results to {out}")


if __name__ == "__main__":
    main()
