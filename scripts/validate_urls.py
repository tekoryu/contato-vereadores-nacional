"""Validate prefeitura/câmara URLs in `data/silver/prefeituras.csv`.

For every non-empty URL, classify into one of:

    ok                   HTTP 2xx + município name found in body
    ok_unverified        HTTP 2xx, município name not found
    redirect_off_domain  followed redirects to a different host
    dns_error            host did not resolve
    tls_error            certificate problems even after retry
    timeout              no response within TIMEOUT_S
    http_4xx / http_5xx  server returned an error code
    parking              body matches known parking / domain-for-sale signatures
    cached_dead          host was already in data/silver/dead_urls.json

Outputs:
    data/silver/url-validation.jsonl  — one record per (ibge_code, kind, url)
    data/silver/prefeituras-validated.csv — original CSV + per-URL flags
"""

from __future__ import annotations

import csv
import json
import re
import socket
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from config import cfg  # noqa: E402
IN_CSV: Path = cfg.paths.prefeituras_csv
OUT_JSONL: Path = cfg.paths.url_validation_jsonl
OUT_CSV: Path = cfg.paths.prefeituras_validated
DEAD_URLS: Path = cfg.paths.dead_urls_json

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT_S = 10
MAX_BYTES = 64 * 1024
WORKERS = 32

PARKING_PATTERNS = (
    "registro.br",
    "domain for sale",
    "este domínio está à venda",
    "buy this domain",
    "godaddy.com",
    "hugedomains.com",
    "sedoparking",
    "porkbun",
    "namecheap.com/parking",
    "default web site page",
    "apache2 ubuntu default page",
    "nginx welcome",
    "it works!",
)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s.lower()).strip()


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url, re.I)
    h = (m.group(1) if m else url).lower()
    h = h.split(":", 1)[0]
    return h.removeprefix("www.")


def load_dead_urls() -> set[str]:
    if not DEAD_URLS.exists():
        return set()
    data = json.loads(DEAD_URLS.read_text(encoding="utf-8"))
    return {k.rstrip("/") for k in data.keys()}


def load_done() -> set[tuple[str, str]]:
    if not OUT_JSONL.exists():
        return set()
    done: set[tuple[str, str]] = set()
    with OUT_JSONL.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                done.add((r["ibge_code"], r["kind"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def fetch(url: str) -> dict:
    """Return {status, http_status, final_url, body, error}."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
        },
    )
    ctx = ssl.create_default_context()
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=ctx) as resp:
                final = resp.geturl()
                code = resp.status
                raw = resp.read(MAX_BYTES)
                charset = resp.headers.get_content_charset() or "utf-8"
                try:
                    body = raw.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    body = raw.decode("utf-8", errors="replace")
                return {"http_status": code, "final_url": final, "body": body, "error": None}
        except urllib.error.HTTPError as e:
            return {"http_status": e.code, "final_url": url, "body": "", "error": f"http_{e.code}"}
        except (socket.timeout, TimeoutError):
            return {"http_status": None, "final_url": url, "body": "", "error": "timeout"}
        except urllib.error.URLError as e:
            reason = str(e.reason).lower()
            if "name or service not known" in reason or "nodename nor servname" in reason:
                return {"http_status": None, "final_url": url, "body": "", "error": "dns_error"}
            if "certificate" in reason or "ssl" in reason or "tlsv1" in reason:
                if attempt == 1:
                    ctx = ssl._create_unverified_context()
                    continue
                return {"http_status": None, "final_url": url, "body": "", "error": "tls_error"}
            if "timed out" in reason:
                return {"http_status": None, "final_url": url, "body": "", "error": "timeout"}
            return {"http_status": None, "final_url": url, "body": "", "error": f"url_error:{reason[:40]}"}
        except (ConnectionResetError, ConnectionRefusedError, OSError) as e:
            return {"http_status": None, "final_url": url, "body": "", "error": f"conn:{type(e).__name__}"}
    return {"http_status": None, "final_url": url, "body": "", "error": "unknown"}


def classify(url: str, municipio: str, uf: str, dead: set[str]) -> dict:
    started = time.time()
    norm_url = url.strip()
    if not norm_url:
        return {"status": "empty", "http_status": None, "final_url": "", "elapsed_s": 0, "error": None}

    if not norm_url.startswith(("http://", "https://")):
        norm_url = "https://" + norm_url

    if host_of(norm_url) in {host_of(d) for d in dead}:
        return {"status": "cached_dead", "http_status": None, "final_url": norm_url, "elapsed_s": 0, "error": "in dead_urls.json"}

    res = fetch(norm_url)
    elapsed = round(time.time() - started, 2)

    if res["error"]:
        err = res["error"]
        if err == "dns_error":
            status = "dns_error"
        elif err == "timeout":
            status = "timeout"
        elif err == "tls_error":
            status = "tls_error"
        elif err.startswith("http_"):
            code = int(err.split("_")[1])
            status = "http_4xx" if 400 <= code < 500 else "http_5xx"
        else:
            status = "conn_error"
        return {"status": status, "http_status": res["http_status"], "final_url": res["final_url"], "elapsed_s": elapsed, "error": err}

    code = res["http_status"]
    body_norm = norm(res["body"])
    final = res["final_url"]

    if not (200 <= (code or 0) < 300):
        status = "http_4xx" if 400 <= (code or 0) < 500 else ("http_5xx" if 500 <= (code or 0) < 600 else "http_other")
        return {"status": status, "http_status": code, "final_url": final, "elapsed_s": elapsed, "error": None}

    for pat in PARKING_PATTERNS:
        if pat in body_norm:
            return {"status": "parking", "http_status": code, "final_url": final, "elapsed_s": elapsed, "error": f"matched:{pat}"}

    if host_of(final) != host_of(norm_url):
        muni_n = norm(municipio)
        if muni_n and muni_n in body_norm:
            return {"status": "redirect_off_domain", "http_status": code, "final_url": final, "elapsed_s": elapsed, "error": "redirected but municipio name found"}
        return {"status": "redirect_off_domain", "http_status": code, "final_url": final, "elapsed_s": elapsed, "error": "host changed"}

    muni_n = norm(municipio)
    if muni_n and muni_n in body_norm:
        return {"status": "ok", "http_status": code, "final_url": final, "elapsed_s": elapsed, "error": None}
    return {"status": "ok_unverified", "http_status": code, "final_url": final, "elapsed_s": elapsed, "error": "municipio name not found in body"}


def main() -> None:
    rows = list(csv.DictReader(IN_CSV.open(encoding="utf-8")))
    dead = load_dead_urls()
    done = load_done()
    print(f"prefeituras: {len(rows)} | dead_urls cache: {len(dead)} | already done: {len(done)}")

    tasks = []
    for r in rows:
        ibge = r["ibge_code"].strip()
        for kind in ("prefeitura", "camara"):
            if (ibge, kind) in done:
                continue
            url = r[f"{kind}_url"].strip()
            tasks.append((ibge, kind, url, r["ibge_name"], r["uf"]))

    print(f"pending checks: {len(tasks)}")
    if not tasks:
        print("Nothing to do — JSONL is already complete.")
    else:
        from collections import Counter
        statuses: Counter[str] = Counter()
        OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with OUT_JSONL.open("a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(classify, url, muni, uf, dead): (ibge, kind, url, muni, uf)
                       for ibge, kind, url, muni, uf in tasks}
            for i, fut in enumerate(as_completed(futures), 1):
                ibge, kind, url, muni, uf = futures[fut]
                try:
                    out = fut.result()
                except Exception as e:  # noqa: BLE001
                    out = {"status": "exception", "http_status": None, "final_url": url, "elapsed_s": 0, "error": str(e)[:120]}
                rec = {"ibge_code": ibge, "kind": kind, "municipio": muni, "uf": uf, "url": url, **out}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                statuses[out["status"]] += 1
                if i % 200 == 0 or i == len(tasks):
                    f.flush()
                    top = ", ".join(f"{k}={v}" for k, v in statuses.most_common(5))
                    print(f"  [{i:5d}/{len(tasks)}] {top}")

        print("\nFinal status distribution:")
        for k, v in statuses.most_common():
            print(f"  {k:22s} {v:5d}")

    flags: dict[tuple[str, str], dict] = {}
    with OUT_JSONL.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            flags[(r["ibge_code"], r["kind"])] = r

    fieldnames = list(rows[0].keys()) + [
        "prefeitura_status", "prefeitura_http", "prefeitura_final_url",
        "camara_status", "camara_http", "camara_final_url",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            ibge = r["ibge_code"].strip()
            p = flags.get((ibge, "prefeitura"), {})
            c = flags.get((ibge, "camara"), {})
            w.writerow({
                **r,
                "prefeitura_status": p.get("status", ""),
                "prefeitura_http": p.get("http_status", "") or "",
                "prefeitura_final_url": p.get("final_url", "") or "",
                "camara_status": c.get("status", ""),
                "camara_http": c.get("http_status", "") or "",
                "camara_final_url": c.get("final_url", "") or "",
            })
    print(f"\nWrote {OUT_CSV}")
    print(f"Wrote {OUT_JSONL}")


if __name__ == "__main__":
    sys.exit(main())
