import json
import logging
import re
import sys
import time
import ollama
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_BAD_PATH_PATTERNS = ("/login", "/signin", "/certidao", "/assinar", "/assinatura", "/emissao-certidao")
_BAD_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")


def _base_host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_allowed_domain(href: str, start_url: str) -> bool:
    """True if href is on the same domain as start_url or an official .gov.br/.leg.br domain."""
    host = _base_host(href)
    start_host = _base_host(start_url)
    if host == start_host or host.endswith("." + start_host):
        return True
    return host.endswith(".gov.br") or host.endswith(".leg.br")


def _is_valid_navigation_target(href: str) -> bool:
    """True if the URL is a reasonable crawl target (not a login/doc/signature page)."""
    path = urlparse(href).path.lower()
    if any(pattern in path for pattern in _BAD_PATH_PATTERNS):
        return False
    return not any(path.endswith(ext) for ext in _BAD_EXTENSIONS)


def fetch_page(url: str) -> tuple[str, list[dict]]:
    """Returns (page_text, links) where each link is {text, href}."""
    t0 = time.perf_counter()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
        text = page.inner_text("body")
        anchors = page.query_selector_all("a")
        links = []
        for a in anchors:
            href = a.get_attribute("href")
            link_text = a.inner_text().strip()
            if href and link_text:
                links.append({"text": link_text, "href": href})
        context.close()
        browser.close()
    elapsed = time.perf_counter() - t0
    logger.debug(f"fetch_page {elapsed:.2f}s — {len(links)} links ({url})")
    return text, links


def extract_emails(text: str) -> list[str]:
    pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    return list(set(re.findall(pattern, text)))


def pick_best_link(
    links: list[dict],
    politician_name: str,
    model: str,
    *,
    decision_log=None,
    context: dict | None = None,
) -> str | None:
    """Ask the AI to pick the most promising link by number. Returns the href or None."""
    if not links:
        return None

    n = len(links)
    numbered = "\n".join(f"{i + 1}. {l['text']}" for i, l in enumerate(links))
    prompt = (
        f'You are navigating a Brazilian municipal chamber website looking for the contact email of "{politician_name}".\n'
        f'Below is a numbered list of links on the current page. There are {n} links, numbered 1 to {n}.\n\n'
        f'{numbered}\n\n'
        f'Choose the single best link to follow next. Prefer in this order:\n'
        f'1. A link that goes directly to this specific politician\'s profile or contact page.\n'
        f'2. A link to a page that lists all councillors (vereadores, parlamentares) where you can then find this politician.\n'
        f'Do NOT choose: login or authentication pages, digital signature or document validation services, '
        f'social media profiles, or external service providers unrelated to the chamber.\n'
        f'If none seems useful, use 0.'
    )

    logger.debug(f"pick_best_link: {n} links → model {model}")
    t0 = time.perf_counter()
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format={
            "type": "object",
            "properties": {"link_number": {"type": "integer"}},
            "required": ["link_number"],
        },
    )
    elapsed = time.perf_counter() - t0

    data = json.loads(response.message.content)  # pyright: ignore[reportOptionalMemberAccess]
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


def identify_email(
    emails: list[str],
    politician_name: str,
    model: str,
    *,
    decision_log=None,
    context: dict | None = None,
) -> str | None:
    """Ask the AI to pick which email belongs to the politician. Returns the email or None."""
    if not emails:
        return None

    email_list = "\n".join(f"- {e}" for e in emails)
    prompt = (
        f'You are given a list of email addresses found on a Brazilian municipal chamber website.\n'
        f'Your task is to identify the personal contact email for the politician named "{politician_name}".\n\n'
        f'Emails found:\n{email_list}\n\n'
        f'Rules:\n'
        f'- Only return an email if it clearly belongs to this specific politician (e.g. their name appears in it).\n'
        f'- Ignore generic institutional emails such as press, media, ouvidoria, contato, secretaria, etc.\n'
        f'- If no email clearly belongs to this politician, set email to null.'
    )

    logger.debug(f"identify_email: {len(emails)} candidates for {politician_name}")
    t0 = time.perf_counter()
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format={
            "type": "object",
            "properties": {"email": {"type": ["string", "null"]}},
            "required": ["email"],
        },
    )
    elapsed = time.perf_counter() - t0

    data = json.loads(response.message.content)  # pyright: ignore[reportOptionalMemberAccess]
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


def crawl_for_email(
    start_url: str,
    politician_name: str,
    model: str = "qwen2.5:14b",
    max_depth: int = 3,
    *,
    decision_log=None,
) -> tuple[str, str] | None:
    """Returns (email, source_url) if found, else None."""
    current_url = start_url
    visited: set[str] = {start_url.rstrip("/")}

    for depth in range(max_depth + 1):
        ctx = {"politician": politician_name, "url": current_url, "depth": depth, "model": model}

        logger.info(f"  [depth {depth}/{max_depth}] Fetching: {current_url}")
        text, links = fetch_page(current_url)

        emails = extract_emails(text)
        logger.info(f"  [depth {depth}/{max_depth}] Found {len(emails)} email(s) on page")

        if emails:
            logger.info(f"  [depth {depth}/{max_depth}] Asking AI to identify email for: {politician_name}")
            result = identify_email(emails, politician_name, model, decision_log=decision_log, context=ctx)
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
        next_href = pick_best_link(valid_links, politician_name, model, decision_log=decision_log, context=ctx)

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


if __name__ == "__main__":
    from logging_setup import DecisionLogger, setup_logging

    if len(sys.argv) < 3:
        print("Usage: python src/fetcher.py <url> <politician_name> [model]")
        sys.exit(1)

    url = sys.argv[1]
    name = sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else "qwen2.5:14b"

    log = setup_logging()
    dl = DecisionLogger()

    log.info("=" * 60)
    log.info(f"Politician : {name}")
    log.info(f"Start URL  : {url}")
    log.info(f"Model      : {model}")
    log.info("=" * 60)

    result = crawl_for_email(url, name, model, decision_log=dl)

    log.info("=" * 60)
    if result:
        email, source_url = result
        log.info(f"Email     : {email}")
        log.info(f"Source URL: {source_url}")
    else:
        log.info("Result: No email found")
    log.info("=" * 60)
