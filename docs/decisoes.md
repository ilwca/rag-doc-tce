# Registro de decisões

Formato: contexto → decisão → consequências. Decisões provisórias ficam marcadas até serem confirmadas por dados.

## D-001 — `document.json` derivado localmente do `document.md` (2026-11-23)

**Contexto.** O CLAUDE.md prevê a transcrição (passo 3) e a estruturação em `document.json` (passo 4). Pedir as duas coisas ao LLM custaria uma chamada a mais e abriria espaço para os dois arquivos divergirem.

**Decisão.** O LLM transcreve uma vez, em Markdown livre (melhor para a fidelidade do que texto escapado dentro de JSON). O `document.json` (páginas → elementos: título, parágrafo, tabela com células, lista, imagem) é gerado em Python a partir do Markdown (`rag/extracao/markdown.py`). O `meta.json` vem de uma segunda chamada, **só com texto** (a transcrição), com saída estruturada.

**Consequências.** `document.md` e `document.json` nunca se contradizem. A chamada de metadados é barata (sem imagens). A regra "todo valor do meta.json aparece no document.md" fica coerente por construção, e quem lê a imagem de novo é a verificação (passo 5).

## D-002 — Extensões ao schema do `meta.json` (2026-11-23)

- Campo `verificacao` (`{provedor, modelo, modo_entrada, campos_conferidos, divergencias[{campo, leitura_extracao, leitura_verificacao}]}`), que guarda as duas leituras quando divergem, como pede o passo 5.
- Na saída estruturada do LLM, `campos_especificos` e `rastreabilidade` são listas de pares (`[{campo, valor}]`, `[{campo, pagina, confianca}]`), porque o modo estrito de JSON Schema não aceita dicionários livres. Elas são convertidas em dicionários no `meta.json` final, que segue o CLAUDE.md.
- `confianca` assume `alta | media | baixa`.

## D-003 — `EXTRACAO_MODO_ENTRADA=auto` usa `pdf` até o experimento rodar (PROVISÓRIO, 2026-11-23)

**Contexto.** O modo `auto` deve ser decidido pelo experimento `python -m rag.avaliar`, que depende de gabaritos revisados e de chaves de API, ainda indisponíveis.

**Decisão provisória.** `auto` → `pdf` (`rag/extracao/pipeline.py::resolver_modo`). É o modo mais barato de implementar e de chamar, e é a hipótese do CLAUDE.md. **Não é uma conclusão**: substituir pelo resultado do experimento.

## D-004 — Verificação com recortes em faixas (2026-11-23)

**Contexto.** A hipótese do CLAUDE.md é que recortes ampliados (300–400 DPI) ajudam a segunda leitura. Recortar só a região do campo exigiria coordenadas que a transcrição não fornece.

**Decisão.** `VERIFICACAO_MODO_ENTRADA=recortes` rasteriza a 350 DPI as páginas onde a extração localizou os campos críticos (pela `rastreabilidade`). Cada página vira 3 faixas horizontais com 12% de sobreposição. O verificador **não recebe** os valores da primeira leitura (leitura independente). A comparação é feita em Python. Também dá para verificar com `pdf` ou `imagens`, para comparar.

## D-005 — Modelos sugeridos no `.env.example` (2026-11-23)

Consultados em aistudio.google.com/docs/models (ai.google.dev redireciona para lá) e em openrouter.ai/models:

- Extração: `gemini-3.8-flash` (estável, US$ 0,75/3,75 por 1M tokens até 2026-12-31). Candidato mais forte para o experimento: `gemini-4-carbon`. Ficou de fora `gemini-2.5-*`, descontinuado (redirecionado em 2026-12-01).
- Verificação: `mistralai/pixtral-large-2502` via OpenRouter (slug e preço confirmados, aceita imagens e documentos; é um modelo de fev/2025). Não foi possível confirmar slugs atuais de visão da Anthropic ou da Qwen: vale escolher outro verificador direto em openrouter.ai/models (filtro "image input").
- Atenção (OpenRouter, desde 2026-10-18): modelos Google servidos via Vertex usam "Safety Retention" e **ficam excluídos** quando `zdr: true`. Não use modelos Google pelo OpenRouter com ZDR, e use o Gemini direto.
- Atenção (OpenRouter): a engine `mistral-ocr` do plugin `file-parser` fica fora do ZDR. Quando usada, o aviso `plugin_fora_do_zdr` vai para `logs/uso.jsonl` e para o relatório.

## D-006 — Resultado do experimento pdf × imagens (PENDENTE)

Preencher com a tabela de `avaliacao/resultado.md` e a decisão final do modo `auto`.

## D-007 — Extração passa a usar a API do Claude (Anthropic) (2026-11-23)

**Contexto.** O usuário pediu para usar a API do Claude. A primeira tentativa com `gemini-3.8-flash` falhou com 503 (modelo sobrecarregado).

**Decisão.** Novo provedor `anthropic` em `rag/llm/claude.py` (SDK oficial `anthropic`), agora o padrão de `EXTRACAO_PROVEDOR`. Gemini e OpenRouter continuam disponíveis. O CLAUDE.md previa só esses dois: **atualizar a seção "Provedores de LLM" do CLAUDE.md** se a mudança for mantida.

- Modelo: `claude-opus-5-5` (US$ 4 / 20 por 1M tokens). Alternativas mais baratas, a avaliar no experimento: `claude-sonnet-5-5` (US$ 2 / 10) e `claude-haiku-5-5` (US$ 0,10 / 0,50).
- `ANTHROPIC_EFFORT=low`: o raciocínio do Claude não pode ser desligado nesse modelo. O nível `low` reduz os tokens de raciocínio, cobrados como saída. Se a transcrição perder qualidade, subir para `medium`.
- **Temperatura:** os modelos atuais do Claude rejeitam `temperature`, então o provedor não a envia. A regra "temperatura 0 na extração" do CLAUDE.md não se aplica a esse provedor. O determinismo fica por conta dos prompts e do cache em `raw/`.
- PDF enviado como bloco `document` (base64, até 32 MB / 600 páginas). Imagens como blocos `image`.
- Saída estruturada via `output_config.format` (JSON Schema estrito, o mesmo gerado de Pydantic).
- `fallbacks="default"` (beta `server-side-fallback-2026-07-01`): se o Claude recusar por política de segurança, a API refaz a chamada em outro modelo. Desligável com `ANTHROPIC_FALLBACKS=false`.
- Retentativas: 429, 5xx (inclui 529 "overloaded") e erros de conexão, pelo tenacity.

## D-008 — Rasterizar acima da resolução nativa não ajuda (PROPOSTA, 2026-11-23)

**Contexto.** No primeiro documento real (`588562505942`, declaração do TCE-TO), o PDF contém uma única imagem de 1526×2191 px numa página de 1527×2192 pt, o que dá ≈ 72 DPI efetivos. Rasterizar a 200 DPI gerou um PNG de 13 MB, e a 350 DPI, faixas de 7–10 MB cada, sem nenhum detalhe a mais: é só ampliação.

**Proposta.** Calcular a resolução nativa da imagem de cada página escaneada (`page.get_images()` + dimensões da página) e rasterizar em `min(DPI configurado, ~2× nativo)`. Em PDFs escaneados, considerar extrair a imagem original direto, sem reamostrar. Isso reduz tamanho, tokens no modo `imagens` e no envio de recortes. Confirmar com mais documentos antes de implementar.
