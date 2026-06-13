from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urljoin

import ollama
from playwright.async_api import Browser, TimeoutError as PlaywrightTimeoutError

from config import cfg
from fetcher import (
    _is_allowed_domain,
    _is_valid_navigation_target,
    extract_emails,
)

logger = logging.getLogger(__name__)

# AsyncClient yields control while the HTTP call to Ollama is in flight.
# The sync ollama.chat would block the entire event loop for ~0.7s per call,
# freezing all other workers. AsyncClient lets them keep running.
_ollama = ollama.AsyncClient()


async def fetch_page(browser: Browser, url: str) -> tuple[str, list[dict[str, str]]]:
    """Returns (page_text, links) where each link is {text, href}.

    Uses a shared browser and a fresh context per call for isolation.
    """
    # Every `await` below is a potential pause point. While this coroutine
    # is parked waiting for the network/Chromium, the event loop is free to
    # run other workers. That's where the 3x speedup comes from — the wait
    # time becomes productive for everyone else.
    t0 = time.perf_counter()
    context = await browser.new_context(ignore_https_errors=True)
    try:
        page = await context.new_page()
        await page.goto(url, timeout=cfg.page_timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=cfg.network_idle_timeout_ms)
        except PlaywrightTimeoutError:
            pass
        text = await page.inner_text("body")
        anchors = await page.query_selector_all("a")
        links = []
        for a in anchors:
            href = await a.get_attribute("href")
            link_text = (await a.inner_text()).strip()
            if href and link_text:
                links.append({"text": link_text, "href": href})
    finally:
        await context.close()
    elapsed = time.perf_counter() - t0
    logger.debug(f"fetch_page {elapsed:.2f}s — {len(links)} links ({url})")
    return text, links


async def pick_best_link(
    links: list[dict[str, str]],
    politician_name: str,
    model: str,
    *,
    decision_log: Any = None,
    context: dict[str, Any] | None = None,
) -> str | None:
    if not links:
        return None

    n = len(links)
    numbered = "\n".join(f"{i + 1}. {l['text']}" for i, l in enumerate(links))
    prompt = (
        f'Você está navegando em um site de câmara municipal brasileira buscando o e-mail de contato de "{politician_name}".\n'
        f'Abaixo está uma lista numerada de links da página atual. São {n} links, numerados de 1 a {n}.\n\n'
        f'{numbered}\n\n'
        f'Escolha o único link mais promissor a seguir. Prefira nesta ordem:\n'
        f'1. Um link que vá diretamente ao perfil ou página de contato deste(a) vereador(a) com o nome exato "{politician_name}".\n'
        f'2. Um link para uma página que liste todos os vereadores, onde você possa encontrar este(a) político(a).\n'
        f'NÃO escolha: páginas de login ou autenticação, serviços de assinatura digital ou validação de documentos, '
        f'perfis em redes sociais, ou prestadores de serviços externos sem relação com a câmara.\n'
        f'Se nenhum parecer útil, use 0.'
    )

    logger.debug(f"pick_best_link: {n} links → model {model}")
    t0 = time.perf_counter()
    # `await` here parks this worker until Ollama replies. The GPU still
    # serializes requests, but other workers can run their fetch_page calls
    # during the wait — that's why the LLM queue isn't a bottleneck at N=3.
    response = await _ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format={
            "type": "object",
            "properties": {"link_number": {"type": "integer"}},
            "required": ["link_number"],
        },
    )
    elapsed = time.perf_counter() - t0

    data = json.loads(response.message.content)  # pyright: ignore[reportArgumentType, reportOptionalMemberAccess]
    idx = data["link_number"] - 1
    if 0 <= idx < n:
        result_href = links[idx]["href"]
    else:
        if data["link_number"] != 0:
            logger.warning(f"pick_best_link: link_number={data['link_number']} out of range (n={n}), treating as no pick")
        result_href = None

    logger.debug(f"pick_best_link: chose link_number={data['link_number']} ({elapsed:.2f}s) → {result_href}")

    if decision_log:
        decision_log.log(
            type="pick_link",
            **(context or {}),
            num_links=len(links),
            prompt=prompt,
            response=data,
            result_href=result_href,
            duration_s=round(elapsed, 3),
        )

    return result_href


async def identify_email(
    emails: list[str],
    politician_name: str,
    model: str,
    *,
    decision_log: Any = None,
    context: dict[str, Any] | None = None,
) -> str | None:
    if not emails:
        return None

    email_list = "\n".join(f"- {e}" for e in emails)
    prompt = (
        f'Você recebeu uma lista de endereços de e-mail encontrados em um site de câmara municipal brasileira.\n'
        f'Sua tarefa é identificar o e-mail de contato pessoal do(a) vereador(a) "{politician_name}".\n\n'
        f'E-mails encontrados:\n{email_list}\n\n'
        f'Regras:\n'
        f'- Retorne um e-mail apenas se ele claramente pertencer a este(a) vereador(a) específico(a) (ex.: o nome dele(a) aparece no endereço).\n'
        f'- Ignore e-mails institucionais genéricos como imprensa, mídia, ouvidoria, contato, secretaria, etc.\n'
        f'- Se nenhum e-mail claramente pertencer a este(a) vereador(a), defina email como null.'
    )

    logger.debug(f"identify_email: {len(emails)} candidates for {politician_name}")
    t0 = time.perf_counter()
    response = await _ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format={
            "type": "object",
            "properties": {"email": {"type": ["string", "null"]}},
            "required": ["email"],
        },
    )
    elapsed = time.perf_counter() - t0

    data = json.loads(response.message.content)  # pyright: ignore[reportArgumentType, reportOptionalMemberAccess]
    email = data.get("email")
    if not email or str(email).lower() in ("null", "none", ""):
        email = None

    logger.debug(f"identify_email: result={email!r} ({elapsed:.2f}s)")

    if decision_log:
        decision_log.log(
            type="identify_email",
            **(context or {}),
            candidates=emails,
            prompt=prompt,
            response=data,
            result_email=email,
            duration_s=round(elapsed, 3),
        )

    return email


async def crawl_for_email(
    browser: Browser,
    start_url: str,
    politician_name: str,
    model: str = cfg.model,
    max_depth: int = cfg.max_crawl_depth,
    *,
    decision_log: Any = None,
) -> tuple[str, str] | None:
    # Each iteration of the depth loop has two `await` points (fetch_page
    # then an LLM call). At each one this coroutine can be paused and the
    # event loop will switch to another worker that's ready to make progress.
    current_url = start_url
    visited: set[str] = {start_url.rstrip("/")}

    for depth in range(max_depth + 1):
        ctx = {"politician": politician_name, "url": current_url, "depth": depth, "model": model}

        logger.info(f"  [depth {depth}/{max_depth}] Fetching: {current_url}")
        text, links = await fetch_page(browser, current_url)

        emails = extract_emails(text)
        logger.info(f"  [depth {depth}/{max_depth}] Found {len(emails)} email(s) on page")

        if emails:
            logger.info(f"  [depth {depth}/{max_depth}] Asking AI to identify email for: {politician_name}")
            result = await identify_email(emails, politician_name, model, decision_log=decision_log, context=ctx)
            if result:
                return result, current_url
            logger.info(f"  [depth {depth}/{max_depth}] AI could not match any email to this politician")

        if depth == max_depth:
            logger.info(f"  [depth {depth}/{max_depth}] Max depth reached. Giving up.")
            break

        valid_links = []
        filtered_count = 0
        for l in links:
            href = l["href"]
            if href.startswith(("mailto:", "javascript:", "#")):
                continue
            if not href.startswith("http"):
                href = urljoin(current_url, href)
            if href.rstrip("/") in visited:
                continue
            if not _is_allowed_domain(href, start_url):
                filtered_count += 1
                logger.debug(f"  domain-filtered: {href}")
                continue
            valid_links.append({"text": l["text"], "href": href})

        if filtered_count:
            logger.debug(f"  [depth {depth}/{max_depth}] {filtered_count} link(s) removed by domain filter")

        logger.info(f"  [depth {depth}/{max_depth}] {len(valid_links)} navigable links. Asking AI to pick next...")
        next_href = await pick_best_link(valid_links, politician_name, model, decision_log=decision_log, context=ctx)

        if not next_href:
            logger.info(f"  [depth {depth}/{max_depth}] AI found no promising link. Giving up.")
            return None

        if not next_href.startswith("http"):
            next_href = urljoin(current_url, next_href)

        if not _is_valid_navigation_target(next_href):
            logger.warning(f"  [depth {depth}/{max_depth}] AI picked invalid target ({next_href}), giving up.")
            return None

        visited.add(next_href.rstrip("/"))
        logger.info(f"  [depth {depth}/{max_depth}] AI picked: {next_href}")
        current_url = next_href

    return None
