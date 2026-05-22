# The Odyssey to Info That Should Be Public

A chronicle of the hunt for contact data across Brazil's 5,571 municipalities — information that is theoretically public, practically scattered, and stubbornly incomplete.

## Goal

Build a comprehensive contact dataset for Brazilian municipal legislators (vereadores), including prefeitura and câmara URLs for all 5,571 municipalities.

---

## 2026-05-22 — Prefeitura URL enrichment

### Starting point

`prefeituras.csv` was created with four columns: `ibge_code`, `ibge_name`, `uf`, `prefeitura_url`. Of the 5,571 municipalities, only **3,138 had a prefeitura URL** — 56.3% coverage, leaving 2,433 municipalities without any prefeitura site.

### New data source: Panorama Senado dos Municípios

A file `data/raw/contato.csv` was added, sourced from **Panorama Senado dos Municípios**. It contains one row per municipality with the following fields: `IDIBGE`, `MUNICIPIO`, `UF`, `NOME`, `ENDERECO`, `TELEFONE`, `SITE`, `Email`.

The `SITE` column primarily holds **câmara municipal** (city council) websites, but a significant portion of entries contain **prefeitura websites** entered in that field. Of the 5,571 rows, 4,724 had a non-empty `SITE` value.

### Enrichment logic

Municipalities were matched by IBGE code (`ibge_code` ↔ `IDIBGE`). For each match:

- If the `SITE` URL contained patterns associated with câmaras (`camara`, `leg.br`, `cm.`, etc.), it was stored in a new `camara_url` column.
- If the `SITE` URL did **not** match those patterns and the municipality was missing a `prefeitura_url`, it was used to fill the `prefeitura_url` column.

### Results

| Metric | Before | After |
|---|---|---|
| `prefeitura_url` coverage | 56.3% (3,138) | **66.8% (3,721)** |
| `prefeitura_url` still missing | 2,433 | **1,850** |
| `camara_url` filled (new column) | — | **3,377** |
| Missing **both** URLs | — | **390** |

The 390 municipalities with no URL of any kind are concentrated in BA (52), PI (38), MA (31), RS (31), TO (25), AM (23), PB (22), and RN (17) — mostly smaller interior municipalities in the Northeast and North with limited digital presence.

### Remaining gaps

- **1,850** municipalities still lack a prefeitura URL.
- **390** municipalities have no web presence on record at all.

Possible next steps: URL pattern heuristics (e.g. `municipio.uf.gov.br`), additional data sources, or manual research.

---

## 2026-05-22 — The Heuristic Gambit

### The observation

While reviewing the still-missing 1,850 municipalities, a manually found URL revealed a clean pattern: **Chapada da Natividade (TO)** lives at `https://chapadadanatividade.to.gov.br/`. Many Brazilian prefeituras follow the convention `municipio.uf.gov.br` — strip accents, remove spaces and punctuation, lowercase, append the state code.

It was worth a shot.

### The approach

A script (`scripts/validate_prefeitura_urls.py`) was written to:

1. Generate a candidate URL for every municipality still missing a `prefeitura_url`, using the slug pattern above.
2. Fire async HTTP requests against all 1,850 candidates (50 concurrent), following redirects and accepting any HTTP status below 400 as a live site.
3. Write confirmed URLs back into `prefeituras.csv`.

### Results

Out of 1,850 candidates, **1,517 came back alive** — an 82% hit rate on a pure heuristic with no external data source.

| Pass | Method | `prefeitura_url` coverage |
|---|---|---|
| 1 — Baseline | Manual / unknown origin | 56.3% (3,138) |
| 2 — Panorama Senado | Cross-reference `contato.csv` | 66.8% (3,721) |
| 3 — URL heuristic | `municipio.uf.gov.br` pattern + HTTP validation | **94.0% (5,238)** |

- **333** municipalities still lack a prefeitura URL.
- **72** municipalities have no web presence of any kind — no prefeitura, no câmara.

### What remains

The surviving 72 dark municipalities are the hardest cases: small, interior, low digital presence. The 333 missing prefeitura URLs may follow different URL patterns (e.g. `www.municipio.gov.br`, portal systems, or hosted on state platforms). Further passes would need smarter heuristics or additional data sources.

---

## 2026-05-22 — Fallback Patterns and Diminishing Returns

### New patterns from manual research

Three municipalities from the "dark" list were looked up manually, revealing two new URL patterns:

- **Pacaembu/SP** → `https://www.pacaembu.sp.gov.br/` — same slug, `www.` prefix
- **Morro do Chapéu do Piauí/PI** → `https://morrodochapeu.pi.gov.br/` — state name stripped from slug
- **São Sebastião do Alto/RJ** → `https://ssalto.rj.gov.br/` — hand-crafted abbreviation (not automatable)

### Approach

The validation script (`scripts/validate_prefeitura_urls.py`) was updated to try up to four candidate URLs per municipality, in order:

1. `municipio.uf.gov.br` (original)
2. `www.municipio.uf.gov.br` (www prefix)
3. `municipiosemestado.uf.gov.br` (state name stripped from slug)
4. `www.municipiosemestado.uf.gov.br` (www + stripped)

State name suffixes were mapped for all 26 UFs + DF.

### Results

42 additional URLs confirmed from 333 candidates.

| Pass | Method | `prefeitura_url` coverage |
|---|---|---|
| 1 — Baseline | Manual / unknown origin | 56.3% (3,138) |
| 2 — Panorama Senado | Cross-reference `contato.csv` | 66.8% (3,721) |
| 3 — URL heuristic | `municipio.uf.gov.br` + HTTP validation | 94.0% (5,238) |
| 4 — Fallback patterns | `www.` prefix + state suffix stripping | **94.8% (5,280)** |

- **291** municipalities still lack a prefeitura URL.
- **60** are completely dark (no prefeitura, no câmara).

The remaining gaps require non-standard slug guessing (abbreviations, portal redirects) or a new data source — heuristics alone have reached their ceiling here.

---

## 2026-05-22 — Turning the Heuristic on Câmaras

### Observation

The same slug-based URL patterns used for prefeituras could be applied to câmara URLs. Analysis of the 3,377 câmara URLs already collected from Panorama Senado revealed two dominant domain patterns:

- `municipio.uf.leg.br` (~1,056 cases)
- `camara*.municipio.uf.gov.br` (~2,170 cases)

### Approach

A new script (`scripts/validate_camara_urls.py`) was written to try up to five candidate URLs per municipality missing a `camara_url`, in order:

1. `municipio.uf.leg.br`
2. `www.municipio.uf.leg.br`
3. `camara.municipio.uf.gov.br`
4. `www.camara.municipio.uf.gov.br`
5. `camaramunicipio.uf.gov.br`

The same state-suffix stripping logic from the prefeitura script was applied as a secondary slug variant.

### Results

1,114 câmara URLs confirmed from 2,194 candidates — a 51% hit rate.

| Metric | Before | After |
|---|---|---|
| `camara_url` coverage | 60.6% (3,377) | **80.6% (4,491)** |
| `camara_url` still missing | 2,194 | **1,080** |
| Missing **both** URLs | 60 | **27** |

Only **27 municipalities** now have no web presence of any kind — no prefeitura, no câmara. The odyssey has covered 99.5% of the country.

---

## 2026-05-22 — The Last 27: Manual Hunt

### The final frontier

With heuristics exhausted, the remaining 27 completely dark municipalities were researched manually via web search. These were cities where every automated pattern had failed — non-standard slugs, portal-hosted sites, or just genuine digital absence.

### Results

All 27 were investigated. URLs were found for the vast majority; only 4 gaps remained unresolvable:

- **Japurá (AM)** — no standalone prefeitura gov.br site; only referenced via third-party transparency portals
- **Tabatinga (AM)** — câmara site not found (prefeitura confirmed at tabatinga.am.gov.br)
- **Urucurituba (AM)** — câmara site not found (prefeitura confirmed at urucurituba.am.gov.br)
- **Morro do Chapéu do Piauí (PI)** — câmara site not found (prefeitura confirmed at morrodochapeu.pi.gov.br)

Notable non-obvious URLs that no heuristic could have guessed:
- **Óleo/SP** → `pmoleo.sp.gov.br` (prefixed with `pm`)
- **São Sebastião de Lagoa de Roça/PB** → `lagoaderoca.pb.gov.br` (shortened popular name)
- **Caracol/MS** → `pmcaracol.ms.gov.br` (prefixed with `pm`)

### Final state of the dataset

| Metric | Before manual hunt | After manual hunt |
|---|---|---|
| `prefeitura_url` coverage | 94.8% (5,280) | **95.2% (5,306)** |
| `camara_url` coverage | 80.6% (4,491) | **81.0% (4,514)** |
| Missing **both** URLs | 27 | **0** |

Every municipality in Brazil now has at least one official URL on record. The odyssey is complete.

---

## 2026-05-22 — A Word on Data Hygiene

### The agent side-effect

The research agent that hunted down the last 27 URLs also, uninstructed, appended 54 rows to `data/raw/contato.csv`. Upon inspection, the IBGE codes it used were fabricated — valid codes that pointed to entirely different municipalities (e.g. `4219507` → Xanxerê/SC instead of Vargem/SC).

The URLs themselves were correct and had already been applied to `prefeituras.csv` using our own verified IBGE codes. The rogue rows were stripped from `contato.csv` and saved separately as `data/raw/contato_manual_research.csv`, with the unreliable IBGE column removed entirely.

### Lesson

IBGE codes must always be sourced from authoritative references (IBGE itself, or our own `prefeituras.csv`). Never trust a code generated or inferred by a language model — they will look plausible and be wrong.

---

## 2026-05-22 — The Cost of the Odyssey

### Token accounting

Not all passes cost the same. Most of this work was done by Python scripts — zero LLM tokens, just HTTP requests and CSV manipulation. Only two steps involved language model calls beyond the main conversation.

| Pass | Method | URLs recovered | LLM tokens | Notes |
|---|---|---|---|---|
| 1 — Panorama Senado | Python script, CSV cross-reference | 583 prefeitura + 3,377 câmara | ~0 | Pure data join |
| 2 — `municipio.uf.gov.br` heuristic | Python + async HTTP (1,850 requests) | 1,517 | ~0 | No LLM involved |
| 3 — Fallback patterns (`www.`, state suffix) | Python + async HTTP (333 requests) | 42 | ~0 | No LLM involved |
| 4 — Câmara heuristic (`leg.br`, `camara.*`) | Python + async HTTP (2,194 requests) | 1,114 | ~0 | No LLM involved |
| 5 — Manual hunt (last 27) | Web search agent | 26 prefeitura + 23 câmara | **~79,000** | 90 tool uses, 27 web searches |
| Conversation overhead | Planning, scripting, history | — | ~ongoing | This session |

### The takeaway

**~79,000 tokens** (plus conversation context) to close the final 0.5% — the hardest 27 municipalities that defeated every automated approach. The other 99.5% cost essentially nothing in LLM terms, just compute time for HTTP validation.

The efficient strategy: exhaust deterministic methods first, reach for LLM-assisted research only when pattern-matching fails. In this project, that ratio held — scripts did the heavy lifting, the model filled the last gap.
