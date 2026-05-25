# Contato Vereadores Nacional

**AI-Assisted Politician Email Finder for Brazilian municipal sites**

Este projeto oferece uma CLI Python para rastrear sites de câmaras municipais e tentar extrair emails públicos de vereadores usando um modelo local de LLM via Ollama.

## Stack Técnica

- Python 3.12+
- UV
- Ollama
- Playwright
- Pandas
- PyArrow

## Estrutura do Projeto

```
contato-vereadores-nacional/
├── data/
│   ├── bronze/
│   │   └── contato.csv
│   ├── silver/
│   │   ├── prefeituras.csv
│   │   └── vereadores-completo.json
├── docs/
│   └── HISTORY.md
├── src/
│   └── ai_finder_cli.py
├── pyproject.toml
├── README.md
└── LICENSE
```

## Instalação

1. Clone o repositório:

```bash
git clone https://github.com/tekoryu/contato-vereadores-nacional.git
cd contato-vereadores-nacional
```

2. Crie e ative um ambiente virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Instale as dependências do projeto:

```bash
pip install .
```

## Uso

Execute a CLI principal com a URL inicial do site a ser rastreado:

```bash
python src/ai_finder_cli.py \
  --url https://www.exemplo.gov.br \
  --model llama3 \
  --host http://localhost:11434 \
  --max-depth 3 \
  --max-pages 15
```

Opções principais:

- `--url`: URL de início do rastreamento
- `--model`: nome do modelo local Ollama (padrão: `llama3`)
- `--host`: endpoint do Ollama API (padrão: `http://localhost:11434`)
- `--max-depth`: profundidade máxima de links a seguir
- `--max-pages`: número máximo de páginas a visitar
- `--verbose`: habilita logs de depuração

## Notas

- Garanta que o servidor Ollama esteja em execução antes de usar a CLI.
- Os dados resultantes podem ser organizados manualmente na pasta `data/`.
