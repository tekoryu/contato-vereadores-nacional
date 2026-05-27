# Contato Vereadores Nacional

**AI-Assisted Politician Email Finder for Brazilian municipal sites**

Este projeto rastreia sites de câmaras municipais brasileiras e extrai emails
públicos de vereadores usando um modelo local de LLM via Ollama, com um
pipeline auxiliar que colhe parlamentares diretamente da API SAPL onde
disponível.

## Stack Técnica

- Python 3.12+
- Ollama (LLM local)
- Playwright (renderização headless)
- Pandas + PyArrow
- Git LFS (para os dados em `data/bronze/` e `data/silver/`)

## Estrutura do Projeto

```
contato-vereadores-nacional/
├── data/
│   ├── bronze/                       # fontes externas brutas (TSE, IBGE, etc.)
│   │   ├── contato.csv
│   │   ├── municipio_tse_ibge.parquet
│   │   ├── rede_social_candidato_2024.parquet
│   │   └── vereadores_eleitos_2024.parquet
│   └── silver/                       # dados normalizados e resultados
│       ├── prefeituras.csv           # URLs de câmara/prefeitura por IBGE
│       ├── vereadores-completo.json  # vereadores-alvo do scan
│       ├── vereadores-sapl.jsonl     # colhidos via API SAPL
│       ├── sigi-casas.csv            # inventário SIGI
│       └── results.jsonl             # saída do pipeline (resumable)
├── docs/
│   ├── HISTORY.md
│   └── ONBOARDING.md
├── scripts/                          # utilitários de dados (one-shot)
│   ├── sapl_harvest.py               # colhe vereadores via API SAPL
│   ├── sapl_coverage.py
│   ├── sigi_gapfill.py
│   ├── validate_urls.py
│   ├── pass2_probe.py
│   ├── promote_final_urls.py
│   └── retry_timeouts.py
├── src/
│   ├── pipeline.py                   # entrypoint do scan
│   ├── fetcher.py                    # crawler + extração via LLM
│   ├── sapl_client.py                # cliente da API SAPL
│   └── logging_setup.py
├── pyproject.toml
└── README.md
```

## Instalação

1. Clone o repositório (com LFS habilitado):

```bash
git clone https://github.com/tekoryu/contato-vereadores-nacional.git
cd contato-vereadores-nacional
git lfs pull
```

2. Crie e ative um ambiente virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Instale as dependências do projeto:

```bash
pip install .
playwright install chromium
```

4. Garanta que o Ollama esteja rodando e baixe o modelo padrão:

```bash
ollama pull qwen2.5:14b
```

## Uso

### Pipeline principal (scan de câmaras)

```bash
python src/pipeline.py
```

Opções:

- `--input`: JSON com os vereadores-alvo (padrão: `data/silver/vereadores-completo.json`)
- `--results`: JSONL de saída (padrão: `data/silver/results.jsonl`). O pipeline é
  **resumable** — vereadores já presentes nesse arquivo são pulados.
- `--model`: modelo Ollama (padrão: `qwen2.5:14b`)

Exemplo:

```bash
python src/pipeline.py \
  --input data/silver/vereadores-completo.json \
  --results data/silver/results.jsonl \
  --model qwen2.5:14b
```

### Coleta via API SAPL (one-shot, opcional)

Para câmaras que expõem a API SAPL, é mais barato puxar a lista de
parlamentares direto do endpoint oficial em vez de rastrear o site:

```bash
python scripts/sapl_harvest.py
```

Anexa a `data/silver/vereadores-sapl.jsonl`; também é resumable.

## Notas

- O servidor Ollama precisa estar em execução antes de rodar o pipeline.
- Os arquivos em `data/bronze/` e `data/silver/` são versionados via Git LFS;
  rode `git lfs pull` após o clone.
- `data/silver/dead_urls.json` cacheia URLs comprovadamente mortas para evitar
  re-tentativas em runs futuros.
