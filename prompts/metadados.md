Você recebe, entre as tags `<documento>`, a transcrição em Markdown de um documento (certificado, laudo, relatório, tabela, declaração, contrato etc.). As páginas estão marcadas com `<!-- página N -->`. Extraia os metadados no JSON Schema fornecido.

## Regras de fidelidade (obrigatórias)

- Use **somente** o que está na transcrição. Nunca corrija, complete ou deduza valores. Não use conhecimento externo.
- Campo que não existe no documento: `null` (ou lista vazia).
- Campo que existe, mas está `[ilegível]`: `null` e o nome do campo em `campos_ilegiveis`.
- Valor marcado como `[incerto: X]`: use X e coloque `confianca: "baixa"` na rastreabilidade desse campo.
- Copie identificadores (número do documento, CNPJ, CPF, registros profissionais) **exatamente como aparecem**, com a mesma pontuação.
- Nomes de pessoas e empresas: copie como estão escritos (mesma grafia; pode ajustar só maiúsculas/minúsculas).

## Campos

- `idioma`: código ISO 639-1 do idioma predominante (`pt`, `en`, …).
- `tipo_documento`: `certificado` | `laudo` | `relatorio` | `tabela` | `declaracao` | `contrato` | `outro`.
- `titulo`: o título do documento como aparece. `resumo`: 1 a 3 frases em português dizendo o que é o documento, sem valores que não estejam nele.
- `palavras_chave`: 3 a 10 termos que ajudem a encontrar o documento.
- `emissor`: quem emite (órgão, empresa, laboratório, certificadora). `titular`: a quem o documento se refere (empresa, pessoa, produto, equipamento). `identificador` = CNPJ, CPF ou outro código de identificação, como escrito.
- `numero_documento`: número do certificado/laudo/contrato, como escrito.
- `data_emissao`, `data_validade`: **converta para ISO** `AAAA-MM-DD`. Se o documento só tiver mês e ano, use `AAAA-MM`. Não invente o dia. Se a validade for dada como prazo ("válido por 12 meses"), deixe `data_validade: null` e registre o prazo em `campos_especificos` (`prazo_validade`). Não calcule datas.
- `local`: cidade/UF onde foi emitido, se houver.
- `responsaveis`: quem assina ou responde tecnicamente: nome, cargo e registro (CREA, CRQ, CRM etc.), como escritos.
- `campos_especificos`: outros dados relevantes e próprios deste tipo de documento (por exemplo: norma, escopo, resultado, classe, lote, modelo, endereço, processo, valor). Um par `campo`/`valor` por item. `campo` em snake_case, `valor` copiado como escrito (datas também podem ficar como escritas aqui).
- `tabelas`: uma entrada por tabela da transcrição: página, título (se houver), descrição curta e número de linhas de dados (sem contar o cabeçalho).
- `rastreabilidade`: para cada campo preenchido entre `numero_documento`, `data_emissao`, `data_validade`, `emissor.nome`, `emissor.identificador`, `titular.nome`, `titular.identificador`, `local` e para cada campo de `campos_especificos`, informe a página onde ele aparece e a confiança (`alta` = legível e inequívoco; `media` = legível mas com alguma ambiguidade de interpretação; `baixa` = marcado como incerto ou de leitura duvidosa).
- `campos_ilegiveis`: nomes dos campos (como no schema) que existem no documento mas estão ilegíveis.
- `observacoes`: problemas relevantes (páginas faltando, documento cortado, rasuras, divergências internas, como duas datas diferentes para o mesmo campo). `null` se não houver.
