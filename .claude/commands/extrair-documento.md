---
description: Extração de referência (rascunho de gabarito) de um PDF, feita pelo próprio Claude Code
argument-hint: <caminho do PDF>
---

Faça a extração de referência do PDF `$ARGUMENTS`, seguindo o CLAUDE.md e as regras de `prompts/transcricao.md`, `prompts/metadados.md` e `prompts/verificacao.md`. Este resultado vira **gabarito** depois de revisado por um humano, então a fidelidade importa mais que a velocidade.

## Passos

1. **Identificação e diagnóstico.** Rode:
   ```
   .venv/bin/python -c "from pathlib import Path; from rag.extracao import pdf; p=Path('$ARGUMENTS'); s=pdf.sha256_arquivo(p); d=pdf.diagnosticar(p); print(pdf.doc_id_de(s), s, d.paginas, d.tipos, d.origem)"
   ```
   Crie `corpus/{doc_id}/` e copie o PDF para `corpus/{doc_id}/original.pdf`. Se já existir um `meta.json` lá, pergunte antes de sobrescrever.

2. **Rasterização.** Gere PNGs a 200 DPI em `corpus/{doc_id}/paginas/pagina_NNN.png` (`rag.extracao.pdf.rasterizar`). Para páginas digitais, salve também o texto extraído (`texto_NNN.txt`) como apoio. Esse texto pode ter erros de ordem, então a imagem prevalece.

3. **Transcrição.** Leia **cada** página com a ferramenta Read (imagem). Onde houver números, datas, CNPJ/CPF, tabelas densas ou letra miúda, gere recortes ampliados (`rag.extracao.pdf.recortes`, 350 DPI) em `corpus/{doc_id}/recortes/` e leia-os também. Escreva `document.md` com `<!-- página N -->`, tabelas em Markdown, `[ilegível]` e `[incerto: X]`, exatamente como em `prompts/transcricao.md`.

4. **document.json.** Gere a partir do Markdown, sem reescrever à mão:
   ```
   .venv/bin/python -c "from pathlib import Path; from rag.extracao import pdf; from rag.extracao.markdown import md_para_documento; d='corpus/DOC_ID'; t=pdf.diagnosticar(Path(d)/'original.pdf').tipos; Path(d,'document.json').write_text(md_para_documento(Path(d,'document.md').read_text(), 'DOC_ID', t).model_dump_json(indent=2, exclude_none=True))"
   ```

5. **meta.json.** Preencha conforme `rag/schemas.py::Meta` e `prompts/metadados.md`, com `extracao = {provedor: "claude-code", modelo: <seu ID de modelo>, modo_entrada: "imagens+recortes", data: <hoje>}`.

6. **Conferência.** Releia nos recortes **cada** campo crítico (número, datas, emissor/titular e identificadores) e cada célula numérica de tabela, comparando dígito a dígito com o que escreveu. Corrija a transcrição se achar erro, e nunca "conserte" o original.

7. **Validação.** Rode a validação local e corrija o que for erro de transcrição:
   ```
   .venv/bin/python -c "import json; from pathlib import Path; from rag.extracao.validacao import validar; d=Path('corpus/DOC_ID'); r=validar(json.loads((d/'meta.json').read_text()), json.loads((d/'document.json').read_text()), (d/'document.md').read_text(), json.loads((d/'meta.json').read_text())['paginas']); [print(p) for p in r.problemas] or print('OK')"
   ```

8. **Relatório.** Escreva `relatorio_extracao.md`: campos com página e confiança, trechos ilegíveis/incertos, pontos que o revisor humano deve conferir com prioridade.

9. **Rascunho de gabarito.** Escreva `gabarito/{doc_id}.json` no formato `rag/schemas.py::Gabarito`, com `revisado_por_humano: false`, os campos críticos de `CAMPOS_CRITICOS` (datas em ISO) e **todas** as tabelas com todas as células (1ª linha = cabeçalho, texto exatamente como no documento.md).

Ao final, mostre o caminho dos arquivos e liste o que o humano precisa revisar antes de mudar `revisado_por_humano` para `true`.
