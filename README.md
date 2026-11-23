# RAG sobre documentos PDF (certificados e tabelas)

Sistema de perguntas e respostas em português sobre uma base de 50 a 70 PDFs, a maioria **escaneada**, com muitos **certificados** e **tabelas**.

O projeto está na **fase 1: extração**. Cada PDF vira três arquivos:

- `document.md`: transcrição fiel, com marcadores de página e tabelas em Markdown;
- `document.json`: elementos por página, gerados a partir do `document.md`;
- `meta.json`: metadados estruturados, como emissor, titular, datas, número e tabelas.

Esses arquivos passam por validação local e ganham um relatório de extração.

| Fase | Escopo | Situação |
|---|---|---|
| 1. Extração | PDF → md/json/meta + validação | **em andamento** |
| 2. Indexação | catálogo SQLite + embeddings (sqlite-vec) + busca híbrida | planejada |
| 3. Consulta | agente LLM com ferramentas, que cita documento e página | planejada |
| 4. Interface | API FastAPI + front-end | planejada |

O contexto completo e as decisões de arquitetura estão em [`CLAUDE.md`](CLAUDE.md). As decisões tomadas na prática ficam em [`docs/decisoes.md`](docs/decisoes.md).

## Princípio: fidelidade acima de tudo

O modelo nunca corrige, completa ou deduz valores:

| Situação | Como registrar |
|---|---|
| Campo ausente | `null` |
| Trecho ilegível | `[ilegível]` |
| Leitura incerta | `[incerto: X]` |

## Requisitos

- Python 3.11 ou superior (testado com 3.14).
- [uv](https://docs.astral.sh/uv/) para dependências.
- Chave de API de pelo menos um provedor: Anthropic, Google Gemini ou OpenRouter.

## Instalação

```bash
uv sync                      # cria .venv/ e instala as dependências
cp .env.example .env         # depois preencha as chaves e os modelos
```

> Se o `uv` não estiver no PATH, use `~/.local/bin/uv`.

## Configuração (`.env`)

Cada papel do pipeline tem seu próprio provedor e modelo:

| Variável | Para quê |
|---|---|
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | chaves de API (preencha só as que for usar) |
| `EXTRACAO_PROVEDOR` / `EXTRACAO_MODELO` | transcrição e metadados (`anthropic` \| `gemini` \| `openrouter`) |
| `VERIFICACAO_PROVEDOR` / `VERIFICACAO_MODELO` | segunda leitura independente dos campos críticos |
| `EXTRACAO_MODO_ENTRADA` | `pdf` \| `imagens` \| `auto` (hoje `auto` = `pdf`, de forma provisória) |
| `VERIFICACAO_MODO_ENTRADA` | `pdf` \| `imagens` \| `recortes` |
| `ANTHROPIC_EFFORT` | esforço de raciocínio do Claude (`low` é o mais barato) |
| `OPENROUTER_ZDR` | envia `zdr: true` e `data_collection: deny` (padrão `true`) |
| `OPENROUTER_PDF_ENGINE` | `native` (dentro do ZDR) ou `mistral-ocr` (fora do ZDR) |
| `DPI_IMAGENS`, `DPI_RECORTES` | resolução da rasterização local |

Nenhum ID de modelo fica fixo no código. Os preços usados na estimativa de custo ficam em `precos.json` e devem ser atualizados quando o modelo mudar.

> **Custo:** chamadas à API são cobradas à parte, mesmo para quem assina o Claude Pro. O comando `/extrair-documento` do Claude Code usa os limites do plano, e não a API.

## Uso

### Extrair documentos

```bash
.venv/bin/python -m rag.extrair caminho/arquivo.pdf          # um documento
.venv/bin/python -m rag.extrair caminho/pasta/               # todos os PDFs da pasta
```

| Opção | Efeito |
|---|---|
| `--provedor`, `--modelo`, `--modo` | sobrescrevem o `.env` |
| `--force` | reprocessa documentos já extraídos |
| `--sem-cache` | ignora as respostas guardadas em `raw/` (paga de novo) |
| `--sem-verificacao` | pula a segunda leitura (mais barato) |
| `--sim` / `-y` | não pede confirmação em lotes |

Antes de um lote, o CLI mostra o custo estimado e pede confirmação. Boa prática: rode em **um** documento, confira o resultado e o custo, e só então rode o restante.

### Extração de referência (gabarito) com o Claude Code

```
/extrair-documento @caminho/arquivo.pdf
```

O comando gera a extração completa e um rascunho em `gabarito/{doc_id}.json` com `revisado_por_humano: false`. Depois de conferir os campos contra o original, mude para `true`.

### Experimento pdf × imagens

```bash
.venv/bin/python -m rag.avaliar \
  --combinacao anthropic:claude-opus-5-5:pdf \
  --combinacao anthropic:claude-opus-5-5:imagens
```

O experimento usa os gabaritos revisados. Para cada combinação, mede:

- acerto nos campos críticos e nas células de tabela;
- quantidade de `[ilegível]` e `[incerto]`;
- tokens, custo e tempo.

O resultado vai para `avaliacao/resultado.md`.

## Pipeline (por documento)

1. **Identificação:** o SHA-256 gera o `doc_id` (12 primeiros caracteres), e o PDF é copiado para `corpus/{doc_id}/original.pdf`. Documentos já processados são pulados.
2. **Diagnóstico:** número de páginas e o tipo de cada uma (digital ou escaneada).
3. **Transcrição:** o LLM gera o `document.md`.
4. **Estruturação:**
   - o `document.json` é derivado localmente do Markdown;
   - o `meta.json` vem de uma chamada só com texto, com saída estruturada (Pydantic).
5. **Verificação:** segunda leitura independente dos campos críticos. Se as leituras divergirem, o campo fica com `confianca: "baixa"` e as duas leituras são guardadas.
6. **Validação local (sem LLM):**
   - schema;
   - datas ISO e validade posterior à emissão;
   - dígitos verificadores de CNPJ/CPF;
   - páginas consistentes entre os arquivos;
   - todo valor do `meta.json` aparece no `document.md`.
7. **Relatório:** `relatorio_extracao.md`.
8. **Cache:** respostas brutas das APIs em `raw/`, para reprocessar sem pagar de novo.

Saída por documento:

```
corpus/{doc_id}/
  original.pdf
  document.md
  document.json
  meta.json
  relatorio_extracao.md
  raw/          # respostas brutas das APIs
  paginas/      # PNGs, só no modo imagens ou nos recortes
```

O uso de cada chamada (tokens, custo, latência) fica registrado em `logs/uso.jsonl`.

## Estrutura do repositório

```
rag/
  config.py            # .env (pydantic-settings)
  schemas.py           # meta.json, document.json, gabarito
  llm/                 # LLMProvider: claude.py, gemini.py, openrouter.py
  extracao/            # pdf.py, markdown.py, pipeline.py, validacao.py, relatorio.py
  extrair.py           # CLI de extração
  avaliar.py           # CLI do experimento
prompts/               # transcricao.md, metadados.md, verificacao.md
.claude/commands/      # /extrair-documento
docs/decisoes.md       # registro de decisões
tests/
precos.json            # preços por 1M tokens, usados na estimativa de custo
```

Os prompts ficam em `prompts/*.md`, nunca no código.

## Testes

```bash
.venv/bin/python -m pytest              # só os testes com provedores simulados (padrão)
.venv/bin/python -m pytest -m api       # testes com API real (cobrados)
```

## Privacidade

- `.env`, `corpus/`, `gabarito/`, `avaliacao/` e `logs/` estão no `.gitignore`, porque os documentos podem ser sensíveis.
- Chaves de API nunca são impressas nem registradas.
- No OpenRouter, o ZDR vale só para o roteamento de inferência, **não para plugins** como o `mistral-ocr`. Quando um desses plugins é usado, o log registra o aviso `plugin_fora_do_zdr`.

## Pendências

- Revisar mais gabaritos, incluindo pelo menos um documento com tabela grande e o pior escaneado.
- Rodar o experimento pdf × imagens e registrar o resultado em D-006.
- Avaliar a D-008: rasterizar respeitando a resolução nativa dos escaneados.
- Decidir o provedor e o modelo da verificação.
