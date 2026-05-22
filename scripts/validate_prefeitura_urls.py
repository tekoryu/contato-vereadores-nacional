import asyncio
import csv
import re
import unicodedata
import aiohttp
from tqdm.asyncio import tqdm

INPUT = "prefeituras.csv"
OUTPUT = "prefeituras.csv"
CONCURRENCY = 50
TIMEOUT = 10

# Map UF → state name variations to strip from city name slugs
# e.g. "Morro do Chapéu do Piauí" → "morrodochapeu"
UF_STATE_SUFFIXES = {
    "AC": ["acre", "doacre"],
    "AL": ["alagoas", "dealagoas"],
    "AM": ["amazonas", "doamazonas"],
    "AP": ["amapa", "doamapa"],
    "BA": ["bahia", "dabahia"],
    "CE": ["ceara", "doceara"],
    "DF": ["distritofederal"],
    "ES": ["espiritosanto", "doespiritosanto"],
    "GO": ["goias", "degoias"],
    "MA": ["maranhao", "domaranhao"],
    "MG": ["minasgerais", "deminasgerais"],
    "MS": ["matogrossodosul", "dematogrossodosul"],
    "MT": ["matogrosso", "dematogrosso"],
    "PA": ["para", "dopara"],
    "PB": ["paraiba", "daparaiba"],
    "PE": ["pernambuco", "depernambuco"],
    "PI": ["piaui", "dopiaui"],
    "PR": ["parana", "doparana"],
    "RJ": ["riodejaneiro", "doriodejaneiro"],
    "RN": ["riograndedonorte", "doriograndedonorte"],
    "RO": ["rondonia", "derondonia"],
    "RR": ["roraima", "deroraima"],
    "RS": ["riograndedosul", "doriograndedosul"],
    "SC": ["santacatarina", "desantacatarina"],
    "SE": ["sergipe", "desergipe"],
    "SP": ["saopaulo", "desaopaulo"],
    "TO": ["tocantins", "detocantins"],
}


def slugify(name: str) -> str:
    name = name.lower()
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def candidate_urls(name: str, uf: str) -> list[str]:
    base = slugify(name)
    uf_lower = uf.lower()

    candidates = [
        f"https://{base}.{uf_lower}.gov.br/",           # original
        f"https://www.{base}.{uf_lower}.gov.br/",        # www prefix
    ]

    # strip state name suffix from slug
    for suffix in UF_STATE_SUFFIXES.get(uf, []):
        if base.endswith(suffix):
            stripped = base[: -len(suffix)]
            if stripped:
                candidates.append(f"https://{stripped}.{uf_lower}.gov.br/")
                candidates.append(f"https://www.{stripped}.{uf_lower}.gov.br/")

    return candidates


async def check(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
            return resp.status < 400
    except Exception:
        return False


async def main():
    rows = []
    with open(INPUT, newline="") as f:
        rows = list(csv.DictReader(f))

    candidates = [
        (i, row)
        for i, row in enumerate(rows)
        if not row["prefeitura_url"].strip()
    ]

    print(f"Checking {len(candidates)} municipalities across fallback URL patterns...")

    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(ssl=False)

    async def validate(i, row):
        urls = candidate_urls(row["ibge_name"], row["uf"])
        async with sem:
            for url in urls:
                if await check(session, url):
                    rows[i]["prefeitura_url"] = url
                    return True
        return False

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [validate(i, row) for i, row in candidates]
        results = []
        for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Checking"):
            results.append(await coro)

    confirmed = sum(results)
    print(f"\nConfirmed: {confirmed}/{len(candidates)} URLs validated")

    with open(OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
