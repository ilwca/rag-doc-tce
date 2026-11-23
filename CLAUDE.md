# Projeto: RAG sobre documentos PDF (certificados e tabelas)

## Visão geral

Sistema de perguntas e respostas em linguagem natural (português) sobre uma base de documentos PDF.

- **Base:** 50 a 70 PDFs, a maioria **escaneada** (sem camada de texto), com muitas **tabelas** e **certificados**.
- **Tamanho:** cerca de 4 mil tokens por documento, ~280 mil tokens no total. É uma base pequena, e isso orienta várias decisões abaixo.
- **Idioma dos documentos e das respostas:** português.

### Fases do projeto

1. **Extração** (fase atual): PDF → `document.md` + `document.json` + `meta.json`, com validação.
2. **Indexação:** catálogo SQL com os metadados + embeddings + busca híbrida.
3. **Consulta:** agente LLM com ferramentas que responde em linguagem natural citando documento e página.
4. **Interface:** feita depois, sobre uma API (FastAPI).

**O escopo agora é a fase 1.** Mas o código deve ser pensado para ser reaproveitado nas fases 2 e 3, principalmente a camada de provedores de LLM, que será usada tanto na extração quanto na consulta.

## Decisões de arquitetura já tomadas

- **Recuperação por documento, não por fragmento pequeno.** Com 4 mil tokens por documento, o agente encontra o documento certo e o lê inteiro. O chunking só existe por seção, como apoio à busca.
- **Certificados viram campos estruturados.** Perguntas como "quais vencem em 2027?" são respondidas por consulta SQL, não por busca vetorial.
- **Banco:** SQLite (+ sqlite-vec na fase 2). Não usar Postgres/Qdrant: é infraestrutura demais para ~70 documentos.
- **Fidelidade acima de tudo:** o modelo nunca corrige, completa ou deduz valores. Campo ausente = `null`. Trecho ilegível = `[ilegível]`. Leitura incerta = `[incerto: X]`.

## Provedores de LLM

Dois provedores, ambos configuráveis por `.env`:

- **Google GenAI** (SDK `google-genai`, variável `GEMINI_API_KEY`)
- **OpenRouter** (API compatível com OpenAI, variável `OPENROUTER_API_KEY`; usar o SDK `openai` com `base_url="https://openrouter.ai/api/v1"` ou `httpx`)

Requisitos da camada de provedores (`rag/llm/`):
- Uma interface comum (`LLMProvider`) com implementações `GeminiProvider` e `OpenRouterProvider`.
- Entrada multimodal: texto + PDF inteiro **ou** texto + imagens de página.
- **Saída estruturada** via JSON Schema gerado a partir de modelos Pydantic, validada na resposta.
- Temperatura 0 por padrão na extração.
- Retentativas com backoff exponencial (`tenacity`) para erros 429/5xx.
- Registro de uso por chamada (tokens de entrada/saída, modelo, provedor, latência) em `logs/uso.jsonl`, para estimar custo.
- **Nenhum ID de modelo fixo no código.** Os modelos vêm do `.env`. Os nomes de modelo mudam com frequência: antes de sugerir um padrão para o `.env.example`, consulte a documentação atual de cada provedor (ai.google.dev e openrouter.ai/models).
- **Privacidade no OpenRouter:** por padrão, enviar `provider: {"zdr": true, "data_collection": "deny"}`. Atenção: o ZDR vale só para o roteamento de inferência, **não para plugins** como o `file-parser`/`mistral-ocr`. Se esse plugin for usado, registre isso no log e documente.

Configuração separada por papel, porque cada etapa pode usar um modelo diferente:

```
GEMINI_API_KEY=
OPENROUTER_API_KEY=

EXTRACAO_PROVEDOR=gemini            # gemini | openrouter
EXTRACAO_MODELO=
VERIFICACAO_PROVEDOR=openrouter     # 2ª leitura dos campos críticos, de preferência outro modelo
VERIFICACAO_MODELO=
CONSULTA_PROVEDOR=                  # fase 3
CONSULTA_MODELO=
EMBEDDING_PROVEDOR=                 # fase 2
EMBEDDING_MODELO=

OPENROUTER_ZDR=true
EXTRACAO_MODO_ENTRADA=auto          # pdf | imagens | auto (decidido pela investigação abaixo)
```

## Investigação obrigatória: a conversão para PNG é necessária?

Não assuma a resposta. Implemente os dois modos de entrada e meça.

- **Modo `pdf`:** envia o PDF inteiro ao provedor.
  - Gemini: aceita PDF nativamente, inline ou pela Files API, e processa as páginas com visão.
  - OpenRouter: aceita PDF via conteúdo `file`. O processamento depende do modelo (engine `native`) ou do plugin `file-parser` (`mistral-ocr`, que fica fora do ZDR). Verifique na documentação atual quais modelos têm suporte nativo.
- **Modo `imagens`:** rasteriza localmente com PyMuPDF (200 DPI) e envia uma imagem por página.

**Protocolo do experimento** (`python -m rag.avaliar`):
1. Use 5 documentos de amostra, incluindo o pior escaneado e um com tabela grande.
2. Gabarito: `gabarito/{doc_id}.json` com os campos críticos conferidos manualmente (ver "Gabarito" abaixo).
3. Para cada combinação provedor × modo, meça:
   - acerto nos campos críticos (número, datas, identificadores, valores de tabela);
   - fidelidade das tabelas (linhas e colunas completas);
   - quantidade de `[ilegível]`;
   - tokens, custo estimado e tempo.
4. Registre o resultado e a decisão em `docs/decisoes.md`.

Hipótese a testar: o modo `pdf` basta para a transcrição, mas **recortes ampliados** (crop a 300–400 DPI) continuam úteis na segunda leitura dos campos críticos. Se for assim, a rasterização fica restrita à verificação. Se os dados mostrarem outra coisa, siga os dados.

## Pipeline de extração (fase 1)

Para cada PDF:

1. **Identificação:** SHA-256 → `doc_id` = primeiros 12 caracteres. Copie o arquivo para `corpus/{doc_id}/original.pdf`. Se o documento já foi processado, pule (a menos que receba `--force`).
2. **Diagnóstico:** número de páginas e se cada página é `digital` ou `escaneada` (texto extraível com PyMuPDF).
3. **Transcrição:** gerar `document.md`, com marcadores `<!-- página N -->` e as tabelas em Markdown, seguindo as regras de `prompts/transcricao.md`.
4. **Estruturação:** gerar `document.json` (elementos por página) e `meta.json` com saída estruturada (Pydantic), seguindo `prompts/metadados.md`.
5. **Verificação:** segunda leitura independente dos campos críticos com o modelo de verificação. Se divergir, use `confianca: "baixa"` e guarde as duas leituras.
6. **Validação local (Python, sem LLM):**
   - JSON válido contra o schema;
   - datas ISO válidas e `validade >= emissao`;
   - dígitos verificadores de CNPJ/CPF;
   - número de páginas consistente entre os arquivos;
   - todo valor do `meta.json` aparece no `document.md`.
7. **Relatório:** `relatorio_extracao.md` com campos, confiança, trechos ilegíveis, divergências e falhas.
8. **Cache:** guardar as respostas brutas das APIs em `corpus/{doc_id}/raw/`, para reprocessar sem pagar de novo.

### Estrutura de saída

```
corpus/{doc_id}/
  original.pdf
  document.md
  document.json
  meta.json
  relatorio_extracao.md
  raw/                 # respostas brutas das APIs
  paginas/             # PNGs, só se o modo imagens ou os recortes forem usados
```

### Schema do `meta.json` (implementar como Pydantic em `rag/schemas.py`)

Campos: `versao_schema`, `doc_id`, `sha256`, `arquivo_original`, `paginas`, `origem` (escaneada|digital|mista), `idioma`, `tipo_documento` (certificado|laudo|relatorio|tabela|declaracao|contrato|outro), `titulo`, `resumo`, `palavras_chave`, `emissor`/`titular` (`{nome, identificador}`), `numero_documento`, `data_emissao`, `data_validade`, `local`, `responsaveis` (`[{nome, cargo, registro}]`), `campos_especificos` (dict), `tabelas` (`[{pagina, titulo, descricao, n_linhas}]`), `rastreabilidade` (campo → `{pagina, confianca}`), `campos_ilegiveis`, `observacoes`, `extracao` (`{provedor, modelo, modo_entrada, data}`).

Datas em ISO (`AAAA-MM-DD`; `AAAA-MM` quando só houver mês/ano). Ausente = `null`. Ilegível = `null` + nome do campo em `campos_ilegiveis`.

### Gabarito

O comando `/extrair-documento` (em `.claude/commands/`) gera uma extração de referência feita pelo próprio Claude Code. Depois de **revisada por um humano**, ela vira o gabarito em `gabarito/` para avaliar os provedores.

## Fases 2 e 3 (para orientar o design, não implementar agora)

- O catálogo SQLite terá as tabelas `documentos` (campos do `meta.json`), `tabelas_extraidas` e `secoes` (com embeddings).
- O agente de consulta usará a mesma camada `rag/llm/` com três ferramentas:
  - `consultar_catalogo(filtros)`: SQL sobre os metadados;
  - `buscar_documentos(pergunta)`: busca híbrida, retorna `doc_id`s;
  - `ler_documento(doc_id)`: retorna o `document.md` completo.
- As respostas devem citar `doc_id` e página, e dizer "não encontrei" quando a informação não estiver na base.

## Estrutura do repositório

```
rag/
  config.py          # carrega .env (pydantic-settings)
  schemas.py         # modelos Pydantic (meta, document.json, gabarito)
  llm/
    base.py          # LLMProvider
    gemini.py
    openrouter.py
  extracao/
    pdf.py           # diagnóstico, rasterização, recortes
    pipeline.py      # orquestra os passos 1–8
    validacao.py     # validações locais
    relatorio.py
  extrair.py         # CLI: python -m rag.extrair <arquivo|pasta> [--provedor] [--modo] [--force]
  avaliar.py         # CLI do experimento pdf vs imagens
prompts/
  transcricao.md
  metadados.md
  verificacao.md
gabarito/
corpus/
docs/decisoes.md
tests/
.env.example
```

## Convenções

- Python 3.11+, `uv` para dependências. Bibliotecas: `google-genai`, `openai`, `pymupdf`, `pydantic`, `pydantic-settings`, `typer`, `tenacity`, `pytest`.
- Os prompts ficam em arquivos `.md` em `prompts/`, nunca como strings no código, para serem versionados e ajustados sem mexer na lógica.
- Testes com provedores simulados (mock). Nenhum teste deve chamar uma API real por padrão. Testes reais ficam marcados com `@pytest.mark.api`.
- `.env`, `corpus/` e `gabarito/` vão no `.gitignore`: os documentos podem ser sensíveis.
- Nunca imprimir nem registrar chaves de API.

## Como trabalhar comigo

- Antes de processar vários documentos com API paga, rode em **um** documento, mostre o resultado e o custo estimado, e espere minha confirmação.
- Ao terminar cada etapa, informe o que foi feito e o que ficou pendente.
- Se uma decisão daqui se mostrar errada na prática, proponha a mudança e registre em `docs/decisoes.md`. Não mude em silêncio.
