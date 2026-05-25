import re
import sys
import ollama
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright


def fetch_page(url: str) -> tuple[str, list[dict]]:
    """Returns (page_text, links) where each link is {text, href}."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url, timeout=15000)
        text = page.inner_text("body")
        anchors = page.query_selector_all("a")
        links = []
        for a in anchors:
            href = a.get_attribute("href")
            link_text = a.inner_text().strip()
            if href and link_text:
                links.append({"text": link_text, "href": href})
        browser.close()
    return text, links


def extract_emails(text: str) -> list[str]:
    pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    return list(set(re.findall(pattern, text)))


def pick_best_link(links: list[dict], politician_name: str, model: str) -> str | None:
    """Ask the AI to pick the most promising link by number. Returns the href or None."""
    if not links:
        return None

    numbered = "\n".join(f"{i + 1}. {l['text']}" for i, l in enumerate(links))
    prompt = (
        f'You are navigating a Brazilian municipal chamber website looking for contact info for "{politician_name}".\n'
        f'Below is a numbered list of links on the current page.\n\n'
        f'{numbered}\n\n'
        f'Which link number is most likely to lead to a page listing councillors or their contact information?\n'
        f'Reply with only the number, nothing else. If none seems promising, reply with "0".'
    )

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        idx = int(response.message.content.strip()) - 1 # pyright: ignore[reportOptionalMemberAccess]
        if 0 <= idx < len(links):
            return links[idx]["href"]
    except ValueError:
        pass

    return None


def identify_email(emails: list[str], politician_name: str, model: str) -> str | None:
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
        f'- If no email clearly belongs to this politician, reply with "none".\n\n'
        f'Reply with only the email address or "none", nothing else.'
    )

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )

    result = response.message.content.strip() # pyright: ignore[reportOptionalMemberAccess]
    return None if result.lower() == "none" else result


def crawl_for_email(start_url: str, politician_name: str, model: str = "qwen2.5:14b", max_depth: int = 2) -> str | None:
    current_url = start_url

    for depth in range(max_depth + 1):
        print(f"\n[depth {depth}/{max_depth}] Fetching: {current_url}")
        text, links = fetch_page(current_url)

        emails = extract_emails(text)
        print(f"[depth {depth}/{max_depth}] Found {len(emails)} email(s) on page")

        if emails:
            print(f"[depth {depth}/{max_depth}] Asking AI to identify email for: {politician_name}")
            result = identify_email(emails, politician_name, model)
            if result:
                return result
            print(f"[depth {depth}/{max_depth}] AI could not match any email to this politician")

        if depth == max_depth:
            print(f"[depth {depth}/{max_depth}] Max depth reached. Giving up.")
            break

        # Filter out non-navigable links and resolve relative URLs
        valid_links = []
        for l in links:
            href = l["href"]
            if href.startswith(("mailto:", "javascript:", "#")):
                continue
            if not href.startswith("http"):
                href = urljoin(current_url, href)
            valid_links.append({"text": l["text"], "href": href})

        print(f"[depth {depth}/{max_depth}] {len(valid_links)} navigable links found. Asking AI to pick next...")
        next_href = pick_best_link(valid_links, politician_name, model)

        if not next_href:
            print(f"[depth {depth}/{max_depth}] AI found no promising link. Giving up.")
            return None

        if not next_href.startswith("http"):
            next_href = urljoin(current_url, next_href)

        print(f"[depth {depth}/{max_depth}] AI picked: {next_href}")
        current_url = next_href

    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python src/fetcher.py <url> <politician_name> [model]")
        sys.exit(1)

    url = sys.argv[1]
    name = sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else "qwen2.5:14b"

    print("=" * 60)
    print(f"Politician : {name}")
    print(f"Start URL  : {url}")
    print(f"Model      : {model}")
    print("=" * 60)

    result = crawl_for_email(url, name, model)

    print("\n" + "=" * 60)
    print(f"Result: {result if result else 'No email found'}")
    print("=" * 60)
