"""Pilot benchmark: 4 Ollama models on vereador roster extraction.

For each of 5 SAPL câmaras with known ground truth (vereadores + emails
from the SAPL API), we:

  1. Use Playwright to render the câmara's /parlamentar/ HTML page
  2. Strip to visible text
  3. Ask each model to return JSON [{nome, email}]
  4. Compare extraction against SAPL ground truth

Metrics: name recall, email precision/recall, JSON valid rate, elapsed s.
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import ollama
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from config import cfg  # noqa: E402
SAPL_JSONL: Path = cfg.paths.vereadores_sapl_jsonl
OUT_PATH: Path = cfg.paths.model_pilot_json

MODELS = ["gemma3:4b", "qwen3:8b", "qwen2.5:7b", "llama3:latest"]
if len(sys.argv) > 1:
    MODELS = sys.argv[1].split(",")

PILOT_CAMARAS = [
    ("MG", "Cataguases", "https://sapl.cataguases.mg.leg.br/"),
    ("PB", "Guarabira", "https://sapl.guarabira.pb.leg.br/"),
    ("SP", "Monte Mor", "https://sapl.montemor.sp.leg.br/"),
    ("RJ", "Quatis", "https://sapl.quatis.rj.leg.br/"),
    ("PE", "Cabrobó", "https://sapl.cabrobo.pe.leg.br/"),
]

PROMPT_TEMPLATE = """Extraia a lista de vereadores desta página de uma Câmara Municipal brasileira.

Retorne APENAS um array JSON válido, sem nenhum texto adicional, sem markdown, sem explicação.
Formato exato:
[__SCHEMA__]

Se um vereador não tiver email visível, use "" para o campo email.
Inclua TODOS os vereadores listados.

Texto da página:
---
__TEXT__
---

JSON:""".replace("__SCHEMA__", '{"nome": "...", "email": "..."}, ...')


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def load_ground_truth() -> dict[tuple[str, str], list[dict]]:
    gt: dict[tuple[str, str], list[dict]] = {}
    with SAPL_JSONL.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not r.get("ativo"):
                continue
            key = (r["uf"], r["municipio"])
            gt.setdefault(key, []).append({
                "nome": r.get("nome_parlamentar") or r.get("nome_completo"),
                "email": (r.get("email") or "").strip().lower(),
            })
    return gt


def render_page(url: str) -> str:
    """Render with Playwright, return visible text."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent="Mozilla/5.0")
        page = ctx.new_page()
        target = url + "parlamentar/"
        page.goto(target, timeout=30000, wait_until="domcontentloaded")
        # SAPL renders parlamentar list via Vue; wait for actual data
        try:
            page.wait_for_function(
                "() => !document.body.innerText.includes('[[') && document.body.innerText.length > 2000",
                timeout=15000,
            )
        except Exception:
            page.wait_for_timeout(5000)
        text = page.evaluate("() => document.body.innerText")
        browser.close()
        return text


def extract_json(reply: str) -> list[dict] | None:
    """Pull the first JSON array we can find from the model output."""
    reply = reply.strip()
    # try direct parse
    try:
        v = json.loads(reply)
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        pass
    # find first [...]
    m = re.search(r"\[\s*\{.*?\}\s*\]", reply, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        return None


def score(extracted: list[dict] | None, truth: list[dict]) -> dict:
    if not extracted:
        return {
            "json_ok": False, "n_extracted": 0,
            "name_recall": 0.0, "email_precision": 0.0, "email_recall": 0.0,
        }
    truth_names = {norm(t["nome"]): t for t in truth}
    truth_emails = {t["email"] for t in truth if t["email"]}

    name_hits = 0
    email_matched = 0
    email_in_truth = 0
    for e in extracted:
        ename = norm(str(e.get("nome", "")))
        if not ename:
            continue
        # name matches by token overlap on any truth name (≥2 tokens or full match)
        etoks = set(ename.split())
        for tn in truth_names:
            ttoks = set(tn.split())
            if etoks & ttoks and len(etoks & ttoks) >= min(2, len(ttoks)):
                name_hits += 1
                break
        em = (str(e.get("email", "")) or "").strip().lower()
        if em:
            email_in_truth += 1
            if em in truth_emails:
                email_matched += 1

    return {
        "json_ok": True,
        "n_extracted": len(extracted),
        "name_recall": name_hits / len(truth) if truth else 0.0,
        "email_precision": email_matched / email_in_truth if email_in_truth else 0.0,
        "email_recall": email_matched / len(truth_emails) if truth_emails else 0.0,
    }


def main() -> int:
    gt_all = load_ground_truth()

    # Pre-render all pages once (saves time vs re-rendering per model)
    pages: dict[tuple[str, str], str] = {}
    print("Rendering pages...")
    for uf, muni, url in PILOT_CAMARAS:
        truth = gt_all.get((uf, muni), [])
        if not truth:
            print(f"  ✗ {uf}/{muni}: no ground truth, skipping")
            continue
        try:
            t0 = time.time()
            text = render_page(url)
            print(f"  ✓ {uf}/{muni}: {len(text):>6d} chars in {time.time()-t0:.1f}s  ({len(truth)} truth)")
            pages[(uf, muni)] = text
        except Exception as e:
            print(f"  ✗ {uf}/{muni}: {e}")

    if not pages:
        print("No pages rendered, abort.")
        return 1

    results: dict[str, list[dict]] = {m: [] for m in MODELS}

    for model in MODELS:
        print(f"\n=== {model} ===")
        for (uf, muni), text in pages.items():
            truth = gt_all[(uf, muni)]
            prompt = PROMPT_TEMPLATE.replace("__TEXT__", text[:8000])
            t0 = time.time()
            try:
                resp = ollama.generate(model=model, prompt=prompt, think=False, options={"temperature": 0})
                reply = resp.get("response", "")
                err = None
            except Exception as e:  # noqa: BLE001
                reply = ""
                err = str(e)[:120]
            elapsed = time.time() - t0
            extracted = extract_json(reply)
            sc = score(extracted, truth)
            sc["elapsed_s"] = round(elapsed, 1)
            sc["uf"] = uf
            sc["muni"] = muni
            sc["n_truth"] = len(truth)
            sc["error"] = err
            results[model].append(sc)
            print(f"  {uf:3s} {muni:24s} {elapsed:5.1f}s  "
                  f"json={'Y' if sc['json_ok'] else 'N'}  "
                  f"n={sc['n_extracted']:2d}/{sc['n_truth']:2d}  "
                  f"name_recall={sc['name_recall']:.2f}  "
                  f"email_p/r={sc['email_precision']:.2f}/{sc['email_recall']:.2f}")

    print("\n" + "=" * 78)
    print(f"{'MODEL':18s} {'avg_s':>7s} {'json_ok':>8s} {'name_R':>8s} {'email_P':>8s} {'email_R':>8s}")
    summary = {}
    for model in MODELS:
        rs = results[model]
        if not rs:
            continue
        avg_s = sum(r["elapsed_s"] for r in rs) / len(rs)
        json_ok = sum(1 for r in rs if r["json_ok"]) / len(rs)
        name_R = sum(r["name_recall"] for r in rs) / len(rs)
        email_P = sum(r["email_precision"] for r in rs) / len(rs)
        email_R = sum(r["email_recall"] for r in rs) / len(rs)
        summary[model] = {"avg_s": round(avg_s, 1), "json_ok": round(json_ok, 2),
                          "name_recall": round(name_R, 2), "email_precision": round(email_P, 2),
                          "email_recall": round(email_R, 2)}
        print(f"{model:18s} {avg_s:7.1f} {json_ok:8.2f} {name_R:8.2f} {email_P:8.2f} {email_R:8.2f}")

    OUT_PATH.write_text(json.dumps({"summary": summary, "per_camara": results}, indent=2, ensure_ascii=False))
    print(f"\nWrote: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
