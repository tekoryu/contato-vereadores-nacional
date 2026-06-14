"""Central configuration loader for contato-vereadores-nacional.

Reads ``settings.toml`` from the repository root and exposes typed
constants used across ``src/`` and ``scripts/``.  Environment variables
with the ``CVN_`` prefix override any value from the file, e.g.::

    CVN_MODEL=llama3:latest python src/pipeline.py

Usage::

    from config import cfg

    model = cfg.model
    results = cfg.paths.results_jsonl
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

# Repository root is two levels above this file (src/config.py → src/ → root)
ROOT: Path = Path(__file__).resolve().parent.parent
_SETTINGS_FILE: Path = ROOT / "settings.toml"


def _load_toml() -> dict:
    if not _SETTINGS_FILE.exists():
        return {}
    with open(_SETTINGS_FILE, "rb") as fh:
        return tomllib.load(fh)


def _env(key: str, default: str) -> str:
    """Return CVN_<KEY> env var if set, else *default*."""
    return os.environ.get(f"CVN_{key.upper()}", default)


@dataclass
class Paths:
    bronze_dir: Path
    silver_dir: Path
    prefeituras_csv: Path
    sigi_casas_csv: Path
    vereadores_json: Path
    vereadores_sample: Path
    vereadores_sapl_jsonl: Path
    results_jsonl: Path
    dead_urls_json: Path
    url_validation_jsonl: Path
    prefeituras_validated: Path
    sapl_failures_jsonl: Path
    sapl_failures_csv: Path
    sapl_coverage_sample: Path
    model_pilot_json: Path
    gold_json: Path
    gold_parquet: Path


@dataclass
class Config:
    paths: Paths
    model: str
    concurrency: int
    max_crawl_depth: int
    page_timeout_ms: int
    network_idle_timeout_ms: int
    sapl_harvest_workers: int
    sapl_request_timeout_s: int


def _build_config() -> Config:
    raw = _load_toml()
    p = raw.get("paths", {})
    pl = raw.get("pipeline", {})
    sa = raw.get("sapl", {})

    def path(key: str, default: str) -> Path:
        return ROOT / _env(key, p.get(key, default))

    paths = Paths(
        bronze_dir=path("bronze_dir", "data/bronze"),
        silver_dir=path("silver_dir", "data/silver"),
        prefeituras_csv=path("prefeituras_csv", "data/silver/prefeituras.csv"),
        sigi_casas_csv=path("sigi_casas_csv", "data/silver/sigi-casas.csv"),
        vereadores_json=path("vereadores_json", "data/silver/vereadores-completo.json"),
        vereadores_sample=path("vereadores_sample", "data/silver/vereadores-sample.json"),
        vereadores_sapl_jsonl=path("vereadores_sapl_jsonl", "data/silver/vereadores-sapl.jsonl"),
        results_jsonl=path("results_jsonl", "data/silver/results.jsonl"),
        dead_urls_json=path("dead_urls_json", "data/silver/dead_urls.json"),
        url_validation_jsonl=path("url_validation_jsonl", "data/silver/url-validation.jsonl"),
        prefeituras_validated=path("prefeituras_validated", "data/silver/prefeituras-validated.csv"),
        sapl_failures_jsonl=path("sapl_failures_jsonl", "data/silver/sapl-failures.jsonl"),
        sapl_failures_csv=path("sapl_failures_csv", "data/silver/sapl-failures.csv"),
        sapl_coverage_sample=path("sapl_coverage_sample", "data/silver/sapl-coverage-sample.jsonl"),
        model_pilot_json=path("model_pilot_json", "data/silver/model-pilot.json"),
        gold_json=path("gold_json", "data/vereadores-completo.json"),
        gold_parquet=path("gold_parquet", "data/vereadores-completo.parquet"),
    )

    return Config(
        paths=paths,
        model=_env("model", pl.get("default_model", "qwen2.5:14b")),
        concurrency=int(_env("concurrency", str(pl.get("default_concurrency", 3)))),
        max_crawl_depth=int(_env("max_crawl_depth", str(pl.get("max_crawl_depth", 3)))),
        page_timeout_ms=int(_env("page_timeout_ms", str(pl.get("page_timeout_ms", 5000)))),
        network_idle_timeout_ms=int(
            _env("network_idle_timeout_ms", str(pl.get("network_idle_timeout_ms", 3000)))
        ),
        sapl_harvest_workers=int(
            _env("sapl_harvest_workers", str(sa.get("harvest_workers", 10)))
        ),
        sapl_request_timeout_s=int(
            _env("sapl_request_timeout_s", str(sa.get("request_timeout_s", 10)))
        ),
    )


cfg: Config = _build_config()
