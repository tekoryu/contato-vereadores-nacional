# Data Pipeline Methodology: Mapping Brazilian Municipal Legislatures

This document outlines the iterative data engineering and discovery pipeline used to build a comprehensive dataset of contact information for Brazilian municipal legislators (*vereadores*). 

The primary challenge of this project was the fragmented and highly irregular nature of municipal web presence in Brazil. While the information is theoretically public, it is practically scattered across thousands of disparate platforms, subdomains, and hosting providers.

## Objective

Build a comprehensive contact dataset for Brazilian municipal legislators, mapping official websites for all 5,571 municipalities and extracting individual contact emails using an automated, AI-assisted pipeline.

---

## Phase 1: Baseline and Initial Enrichment

### Starting Point
The initial dataset (`prefeituras.csv`) contained basic municipal identifiers (IBGE code, name, state) and known *prefeitura* (city hall) URLs. Out of 5,571 municipalities, only **3,138 had a confirmed prefeitura URL** (56.3% coverage), leaving a gap of 2,433 municipalities.

### Data Source Integration
A supplementary dataset (`contato.csv`) sourced from the *Panorama Senado dos Municípios* was integrated. This dataset contained a `SITE` column that primarily held *câmara municipal* (city council) websites, but frequently contained prefeitura URLs instead.

### Enrichment Logic
Municipalities were matched by their unique IBGE codes. For each match:
- URLs containing patterns associated with legislative chambers (`camara`, `leg.br`, `cm.`, etc.) were assigned to a new `camara_url` column.
- URLs lacking these patterns were used to fill missing `prefeitura_url` entries.

### Results
| Metric | Baseline | After Enrichment |
|---|---|---|
| `prefeitura_url` coverage | 56.3% (3,138) | **66.8% (3,721)** |
| `prefeitura_url` missing | 2,433 | **1,850** |
| `camara_url` filled | — | **3,377** |
| Missing **both** URLs | — | **390** |

The 390 municipalities lacking any web presence were predominantly smaller interior cities in the Northeast and North regions.

---

## Phase 2: URL Pattern Heuristics

To close the gap of 1,850 missing prefeitura URLs, an automated heuristic approach was developed based on standard Brazilian government naming conventions.

### The Approach
A validation script (`scripts/validate_prefeitura_urls.py`) was implemented to:
1. Generate candidate URLs using the standard slug pattern: `https://[municipio].[uf].gov.br/` (accents removed, lowercase, spaces stripped).
2. Execute asynchronous HTTP requests against all 1,850 candidates (50 concurrent connections), following redirects and validating HTTP status codes.
3. Persist confirmed, live URLs back to the dataset.

### Results
Out of 1,850 candidates, **1,517 were confirmed active** — an 82% success rate using a pure heuristic model without external data sources.

| Pass | Method | `prefeitura_url` Coverage |
|---|---|---|
| 1 — Baseline | Manual / unknown origin | 56.3% (3,138) |
| 2 — Panorama Senado | Cross-reference `contato.csv` | 66.8% (3,721) |
| 3 — URL heuristic | `municipio.uf.gov.br` pattern validation | **94.0% (5,238)** |

---

## Phase 3: Fallback Patterns and Câmara Discovery

### Extended Heuristics for Prefeituras
For the remaining 333 missing prefeituras, fallback patterns were tested:
1. `www.[municipio].[uf].gov.br` (www prefix)
2. `[municipio_sem_estado].[uf].gov.br` (state name stripped from the city slug)
3. `www.[municipio_sem_estado].[uf].gov.br`

This recovered an additional 42 URLs, pushing `prefeitura_url` coverage to **94.8% (5,280)**.

### Applying Heuristics to Câmaras Municipais
The same methodology was adapted for legislative chambers (`scripts/validate_camara_urls.py`), testing five candidate patterns per municipality:
1. `[municipio].[uf].leg.br`
2. `www.[municipio].[uf].leg.br`
3. `camara.[municipio].[uf].gov.br`
4. `www.camara.[municipio].[uf].gov.br`
5. `camara[municipio].[uf].gov.br`

### Results
This pass confirmed 1,114 câmara URLs from 2,194 candidates (51% hit rate).

| Metric | Before Heuristics | After Heuristics |
|---|---|---|
| `camara_url` coverage | 60.6% (3,377) | **80.6% (4,491)** |
| `camara_url` missing | 2,194 | **1,080** |
| Missing **both** URLs | 60 | **27** |

---

## Phase 4: Manual Hunt and Final Validation

The final 27 municipalities representing complete digital absence ("dark" municipalities with no prefeitura or câmara URL) required manual web research. These cases involved non-standard slugs (e.g., abbreviations like `ssalto.rj.gov.br`), portal-hosted sites, or genuine lack of a dedicated domain.

### Final State of the Dataset

| Metric | Final Coverage | Total Count |
|---|---|---|
| `prefeitura_url` coverage | **95.2%** | 5,306 |
| `camara_url` coverage | **81.0%** | 4,514 |
| Missing **both** URLs | **0.0%** | 0 |

**Every municipality in Brazil (5,571) now has at least one verified official URL on record.**

---

## Conclusion and System Architecture

This project successfully mapped the digital footprint of the entire Brazilian municipal legislative system. By combining deterministic data engineering (joining external datasets, async HTTP probing, and heuristic URL generation) with probabilistic AI extraction (using local LLMs to navigate sites and extract specific contact emails), the pipeline achieved comprehensive coverage at scale.

### Key Technical Achievements:
- **Resilient Asynchronous Pipeline:** Built a highly concurrent, async-native web scraper using Playwright and `asyncio`, capable of processing thousands of domains with robust error handling and dead-URL caching.
- **Local AI Integration:** Deployed `qwen2.5:14b` locally via Ollama to make semantic navigation decisions (e.g., identifying the correct "Contact" or "Vereadores" page) and extract structured data from unstructured HTML, avoiding the costs and rate limits of proprietary cloud APIs.
- **Data Quality:** Established a reproducible, multi-tiered data architecture (Bronze, Silver, Gold layers) ensuring data provenance and easy resumability of long-running tasks.

This repository serves as a complete, production-ready example of how modern AI tooling can be orchestrated alongside traditional data engineering to solve complex, real-world data acquisition challenges.
