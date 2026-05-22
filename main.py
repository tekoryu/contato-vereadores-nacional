"""Consolidate elected city counselors with their social network URLs."""
import json
import re
from collections import defaultdict, Counter
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

RAW = Path(__file__).parent / "data" / "raw"
OUT = Path(__file__).parent / "data" / "vereadores-completo.json"

ELEITOS = RAW / "vereadores_eleitos_2024.parquet"
REDES = RAW / "rede_social_candidato_2024.parquet"
MUNICIPIOS = RAW / "municipio_tse_ibge.parquet"


def extract_handle(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path if parsed.netloc else url.strip().split("/", 1)[-1] if "/" in url else url.strip()
    handle = path.strip("/").split("/")[0].split("?")[0].split("#")[0]
    return handle.lstrip("@")


def classify_network(url: str) -> str:
    host = urlparse(url.strip().lower()).netloc
    host = re.sub(r"^www\.", "", host)
    if not host:
        host = url.strip().lower().split("/")[0]
        host = re.sub(r"^www\.", "", host)
    mapping = {
        "facebook.com": "facebook", "fb.com": "facebook", "m.facebook.com": "facebook",
        "instagram.com": "instagram",
        "twitter.com": "twitter", "x.com": "twitter", "mobile.twitter.com": "twitter",
        "youtube.com": "youtube", "youtu.be": "youtube", "m.youtube.com": "youtube",
        "tiktok.com": "tiktok", "vm.tiktok.com": "tiktok",
        "linkedin.com": "linkedin", "br.linkedin.com": "linkedin",
        "kwai.com": "kwai", "k.kwai.com": "kwai",
        "threads.net": "threads",
        "t.me": "telegram", "telegram.me": "telegram", "telegram.org": "telegram",
        "wa.me": "whatsapp", "whatsapp.com": "whatsapp",
        "api.whatsapp.com": "whatsapp", "chat.whatsapp.com": "whatsapp",
        "flickr.com": "flickr",
        "snapchat.com": "snapchat",
    }
    if host in mapping:
        return mapping[host]
    for key, val in mapping.items():
        if host.endswith("." + key):
            return val
    return host or "other"


def main() -> None:
    municipios_df = pd.read_parquet(MUNICIPIOS, columns=["CD_MUNICIPIO_TSE", "CD_MUNICIPIO_IBGE", "NM_MUNICIPIO_IBGE"])
    municipios = {
        str(row["CD_MUNICIPIO_TSE"]): {
            "municipio_ibge_id": str(row["CD_MUNICIPIO_IBGE"]),
            "municipio_nome": row["NM_MUNICIPIO_IBGE"],
        }
        for _, row in municipios_df.iterrows()
    }

    eleitos_df = pd.read_parquet(ELEITOS)
    eleitos = {
        str(row["candidato_seq"]): row.to_dict()
        for _, row in eleitos_df.iterrows()
    }

    redes_por_cand: dict[str, list[dict]] = defaultdict(list)
    redes_df = pd.read_parquet(REDES, columns=["candidato_seq", "ordem_rede_social_nr", "url_descricao"])
    for _, row in redes_df.iterrows():
        sq = str(row["candidato_seq"])
        ordem_raw = row["ordem_rede_social_nr"]
        url = str(row["url_descricao"])
        redes_por_cand[sq].append({
            "ordem": int(ordem_raw) if pd.notna(ordem_raw) else None,
            "url": url,
            "rede": classify_network(url),
        })

    consolidated = []
    network_counter: Counter = Counter()
    counselors_with_any = 0
    counselors_per_network: dict[str, set] = defaultdict(set)

    for seq, base in eleitos.items():
        redes = sorted(
            redes_por_cand.get(seq, []),
            key=lambda r: (r["ordem"] is None, r["ordem"] or 0),
        )
        entry = dict(base)
        muni = municipios.get(str(base["municipio_tse_id"]), {})
        entry["municipio_ibge_id"] = muni.get("municipio_ibge_id")
        entry["municipio_nome"] = muni.get("municipio_nome")
        by_network: dict[str, list[str]] = defaultdict(list)
        for r in redes:
            by_network[r["rede"]].append(extract_handle(r["url"]))
            network_counter[r["rede"]] += 1
            counselors_per_network[r["rede"]].add(seq)
        for net, urls in by_network.items():
            entry[f"rede_{net}"] = urls
        consolidated.append(entry)
        if redes:
            counselors_with_any += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump(consolidated, f, ensure_ascii=False, indent=2)

    total = len(consolidated)
    print(f"Total elected counselors: {total}")
    print(f"With >=1 social network: {counselors_with_any} ({counselors_with_any/total:.1%})")
    print(f"Total social network links: {sum(network_counter.values())}")
    print("\nDistribution by network (links / unique counselors):")
    for net, count in network_counter.most_common(20):
        uniq = len(counselors_per_network[net])
        print(f"  {net:<18} {count:>7} links   {uniq:>6} counselors ({uniq/total:.1%})")


if __name__ == "__main__":
    main()
