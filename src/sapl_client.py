"""SAPL parlamentar client.

Many Brazilian câmaras municipais run SAPL (Sistema de Apoio ao Processo
Legislativo) and expose a public JSON API at:

    https://sapl.<casa-host>/api/parlamentares/parlamentar/?format=json

The response is paginated and each result carries structured fields including
`email`, `telefone`, `nome_parlamentar`, `nome_completo`, `ativo`, etc.
"""

from __future__ import annotations

import csv
import json
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable

USER_AGENT = "contato-vereadores/0.1 (+research)"
TIMEOUT_S = 8


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def _http_get_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "json" not in ctype:
                return None
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ConnectionResetError):
        return None


@dataclass
class Parlamentar:
    nome_parlamentar: str
    nome_completo: str
    email: str
    telefone: str
    telefone_celular: str
    ativo: bool
    source_url: str


@dataclass
class SAPLResult:
    base_url: str
    reachable: bool
    parlamentares: list[Parlamentar] = field(default_factory=list)
    error: str | None = None


def candidate_sapl_hosts(casa_host: str) -> list[str]:
    """Build plausible SAPL hostnames from a câmara base host.

    For `riobranco.ac.leg.br` returns:
      ['https://sapl.riobranco.ac.leg.br', 'https://riobranco.ac.leg.br']
    """
    host = casa_host.strip().rstrip("/")
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    host = host.split("/")[0]
    if host.startswith("www."):
        host = host[4:]

    bases = []
    if not host.startswith("sapl."):
        bases.append(f"https://sapl.{host}")
    bases.append(f"https://{host}")
    return bases


def fetch_parlamentares(casa_host: str, max_pages: int = 50) -> SAPLResult:
    for base in candidate_sapl_hosts(casa_host):
        url = f"{base}/api/parlamentares/parlamentar/?format=json"
        data = _http_get_json(url)
        if data is None or "results" not in data:
            continue

        parlamentares: list[Parlamentar] = []
        pages = 0
        next_url: str | None = url
        while next_url and pages < max_pages:
            page = _http_get_json(next_url) if pages else data
            if not page or "results" not in page:
                break
            for r in page["results"]:
                parlamentares.append(
                    Parlamentar(
                        nome_parlamentar=r.get("nome_parlamentar") or "",
                        nome_completo=r.get("nome_completo") or "",
                        email=(r.get("email") or "").strip(),
                        telefone=r.get("telefone") or "",
                        telefone_celular=r.get("telefone_celular") or "",
                        ativo=bool(r.get("ativo")),
                        source_url=base,
                    )
                )
            next_url = page.get("pagination", {}).get("links", {}).get("next")
            if next_url and next_url.startswith("http://"):
                next_url = "https://" + next_url[len("http://"):]
            pages += 1

        return SAPLResult(base_url=base, reachable=True, parlamentares=parlamentares)

    return SAPLResult(base_url="", reachable=False, error="no_sapl_endpoint")


def match_parlamentar(name: str, parlamentares: Iterable[Parlamentar]) -> Parlamentar | None:
    """Best-effort name match by token overlap on normalized names."""
    target = set(_norm(name).split())
    if not target:
        return None
    best: tuple[int, Parlamentar | None] = (0, None)
    for p in parlamentares:
        cand_tokens = set(_norm(p.nome_parlamentar).split()) | set(_norm(p.nome_completo).split())
        overlap = len(target & cand_tokens)
        if overlap > best[0]:
            best = (overlap, p)
    return best[1] if best[0] >= 1 else None


def load_sigi_index(path: str = "data/silver/sigi-casas.csv") -> dict[tuple[str, str], dict]:
    """Return {(uf, normalized_municipio): {url, email, servicos}}."""
    idx: dict[tuple[str, str], dict] = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["casa_legislativa__tipo__nome"] != "Câmara Municipal":
                continue
            key = (r["casa_legislativa__municipio__uf__sigla"], _norm(r["casa_legislativa__municipio__nome"]))
            entry = idx.setdefault(key, {"url": "", "email": "", "servicos": set()})
            entry["servicos"].add(r["tipo_servico__nome"])
            if r["url"] and not entry["url"]:
                entry["url"] = r["url"]
            if r["casa_legislativa__email"] and not entry["email"]:
                entry["email"] = r["casa_legislativa__email"]
    return idx


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python sapl_client.py <casa-host> [politician-name]")
        sys.exit(1)
    host = sys.argv[1]
    result = fetch_parlamentares(host)
    print(f"base_url={result.base_url} reachable={result.reachable} count={len(result.parlamentares)}")
    if len(sys.argv) >= 3 and result.reachable:
        match = match_parlamentar(sys.argv[2], result.parlamentares)
        if match:
            print(f"match: {match.nome_parlamentar!r} email={match.email!r} active={match.ativo}")
        else:
            print("no match")
