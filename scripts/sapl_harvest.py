"""Harvest vereadores from every SAPL-compliant câmara municipal.

Source of truth for "SAPL-compliant" is the SIGI export
(`data/silver/sigi-casas.csv`), filtered to rows where
`casa_legislativa__tipo__nome == "Câmara Municipal"` and
`tipo_servico__nome == "SAPL"`.

For each câmara, hits the parlamentar list endpoint
(`/api/parlamentares/parlamentar/?format=json`), follows pagination, and
writes one JSONL record per parlamentar to
`data/silver/vereadores-sapl.jsonl`.

Resumable: re-running skips câmaras already present in the output file.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sapl_client import fetch_parlamentares  # noqa: E402

OUT_PATH = ROOT / "data/silver/vereadores-sapl.jsonl"
SIGI_PATH = ROOT / "data/silver/sigi-casas.csv"
PREFEITURAS_PATH = ROOT / "data/silver/prefeituras.csv"
WORKERS = 12


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def load_ibge_index() -> dict[tuple[str, str], str]:
    """Map (uf, normalized_municipio) -> ibge_code from prefeituras.csv."""
    idx: dict[tuple[str, str], str] = {}
    with PREFEITURAS_PATH.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            uf = (r.get("uf") or "").strip()
            nome = (r.get("ibge_name") or "").strip()
            ibge = (r.get("ibge_code") or "").strip()
            if uf and nome and ibge:
                idx[(uf, norm(nome))] = ibge
    return idx


def load_sapl_casas() -> list[dict]:
    """SIGI rows for SAPL-flagged Câmaras Municipais."""
    casas: list[dict] = []
    with SIGI_PATH.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["casa_legislativa__tipo__nome"] != "Câmara Municipal":
                continue
            if r["tipo_servico__nome"] != "SAPL":
                continue
            url = (r.get("url") or "").strip()
            if not url:
                continue
            casas.append({
                "uf": r["casa_legislativa__municipio__uf__sigla"].strip(),
                "municipio": r["casa_legislativa__municipio__nome"].strip(),
                "casa_nome": r["casa_legislativa__nome"].strip(),
                "casa_email": (r.get("casa_legislativa__email") or "").strip(),
                "casa_telefone": (r.get("casa_legislativa__telefone") or "").strip(),
                "sapl_url": url,
            })
    return casas


def already_done() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    done: set[str] = set()
    with OUT_PATH.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            host = row.get("sapl_url")
            if host:
                done.add(host)
    return done


def harvest_one(casa: dict) -> tuple[dict, list[dict]]:
    t0 = time.time()
    result = fetch_parlamentares(casa["sapl_url"])
    elapsed = round(time.time() - t0, 2)
    rows: list[dict] = []
    if result.reachable:
        for p in result.parlamentares:
            rows.append({
                "uf": casa["uf"],
                "municipio": casa["municipio"],
                "ibge_code": casa.get("ibge_code", ""),
                "casa_nome": casa["casa_nome"],
                "casa_email": casa["casa_email"],
                "casa_telefone": casa["casa_telefone"],
                "sapl_url": casa["sapl_url"],
                "sapl_base": result.base_url,
                "nome_parlamentar": p.nome_parlamentar,
                "nome_completo": p.nome_completo,
                "email": p.email,
                "telefone": p.telefone,
                "telefone_celular": p.telefone_celular,
                "ativo": p.ativo,
            })
    status = {
        "uf": casa["uf"],
        "municipio": casa["municipio"],
        "sapl_url": casa["sapl_url"],
        "reachable": result.reachable,
        "n_parlamentares": len(result.parlamentares),
        "elapsed_s": elapsed,
        "error": result.error,
    }
    return status, rows


def main() -> None:
    casas = load_sapl_casas()
    ibge_idx = load_ibge_index()
    for c in casas:
        c["ibge_code"] = ibge_idx.get((c["uf"], norm(c["municipio"])), "")

    done = already_done()
    pending = [c for c in casas if c["sapl_url"] not in done]
    print(f"SAPL câmaras: {len(casas)}  already harvested: {len(done)}  pending: {len(pending)}")

    if not pending:
        print("Nothing to do.")
        return

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary = {"total": len(pending), "reachable": 0, "parlamentares": 0, "emails": 0}

    with OUT_PATH.open("a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(harvest_one, c): c for c in pending}
        for i, fut in enumerate(as_completed(futures), 1):
            casa = futures[fut]
            try:
                status, rows = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"✗ [{i}/{len(pending)}] {casa['uf']} {casa['municipio']:30s} ERROR {e}")
                continue

            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()

            if status["reachable"]:
                summary["reachable"] += 1
                summary["parlamentares"] += status["n_parlamentares"]
                summary["emails"] += sum(1 for r in rows if r["email"])
                flag = "✓"
            else:
                flag = "✗"
            print(f"{flag} [{i:4d}/{len(pending)}] {status['uf']} {status['municipio'][:28]:28s} "
                  f"parl={status['n_parlamentares']:3d} ({status['elapsed_s']}s) {status['sapl_url']}")

    print()
    print("=" * 70)
    print(f"Câmaras processed:  {summary['total']}")
    print(f"  reachable:        {summary['reachable']}")
    print(f"  parlamentares:    {summary['parlamentares']}")
    print(f"  with email:       {summary['emails']}")
    print(f"Output: {OUT_PATH}")


if __name__ == "__main__":
    main()
