"""Conversão local do `document.md` em `document.json` (sem LLM).

A transcrição é feita uma vez, em Markdown livre (melhor para a fidelidade).
O `document.json` é derivado dela de forma determinística, então os dois
arquivos nunca se contradizem.
"""

from __future__ import annotations

import re

from ..schemas import Documento, Elemento, PaginaDoc

MARCADOR_PAGINA = re.compile(r"<!--\s*p[áa]gina\s+(\d+)\s*-->", re.I)
_SEPARADOR_TABELA = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def dividir_paginas(md: str) -> dict[int, str]:
    """Texto de cada página, segundo os marcadores `<!-- página N -->`."""
    partes = MARCADOR_PAGINA.split(md)
    # partes = [antes, n1, texto1, n2, texto2, ...]
    paginas: dict[int, str] = {}
    for i in range(1, len(partes) - 1, 2):
        n = int(partes[i])
        paginas[n] = paginas.get(n, "") + partes[i + 1]
    return paginas


def _celulas(linha: str) -> list[str]:
    linha = linha.strip()
    if linha.startswith("|"):
        linha = linha[1:]
    if linha.endswith("|") and not linha.endswith("\\|"):
        linha = linha[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", linha)]


def tabelas_md(texto: str) -> list[list[list[str]]]:
    """Todas as tabelas Markdown de um texto, como listas de linhas."""
    return [e.linhas for e in elementos(texto) if e.tipo == "tabela" and e.linhas]


def elementos(texto: str) -> list[Elemento]:
    linhas = texto.strip("\n").splitlines()
    saida: list[Elemento] = []
    i = 0
    while i < len(linhas):
        linha = linhas[i]
        s = linha.strip()
        if not s:
            i += 1
            continue
        # tabela: linha com | seguida de separador
        if "|" in s and i + 1 < len(linhas) and _SEPARADOR_TABELA.match(linhas[i + 1].strip()):
            bloco = [linha, linhas[i + 1]]
            i += 2
            while i < len(linhas) and "|" in linhas[i] and linhas[i].strip():
                bloco.append(linhas[i])
                i += 1
            dados = [_celulas(bloco[0])] + [_celulas(b) for b in bloco[2:]]
            saida.append(Elemento(tipo="tabela", texto="\n".join(bloco), linhas=dados))
            continue
        titulo = re.match(r"^(#{1,6})\s+(.*)$", s)
        if titulo:
            saida.append(Elemento(tipo="titulo", texto=titulo.group(2).strip(), nivel=len(titulo.group(1))))
            i += 1
            continue
        if re.match(r"^!\[.*\]\(.*\)$", s) or re.match(r"^\[(imagem|figura|carimbo|assinatura|selo|logotipo)", s, re.I):
            saida.append(Elemento(tipo="imagem", texto=s))
            i += 1
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", s):
            bloco = []
            while i < len(linhas) and re.match(r"^\s*([-*+]|\d+[.)])\s+", linhas[i]):
                bloco.append(linhas[i].rstrip())
                i += 1
            saida.append(Elemento(tipo="lista", texto="\n".join(bloco)))
            continue
        # parágrafo: até a próxima linha em branco ou estrutura
        bloco = []
        while i < len(linhas) and linhas[i].strip():
            prox = linhas[i].strip()
            if bloco and (prox.startswith("#") or ("|" in prox and i + 1 < len(linhas) and _SEPARADOR_TABELA.match(linhas[i + 1].strip()))):
                break
            bloco.append(linhas[i].rstrip())
            i += 1
        saida.append(Elemento(tipo="paragrafo", texto="\n".join(bloco)))
    return saida


def md_para_documento(md: str, doc_id: str, tipos_pagina: list[str]) -> Documento:
    paginas_txt = dividir_paginas(md)
    paginas = []
    for n in range(1, len(tipos_pagina) + 1):
        paginas.append(
            PaginaDoc(numero=n, tipo=tipos_pagina[n - 1], elementos=elementos(paginas_txt.get(n, "")))
        )
    return Documento(doc_id=doc_id, paginas=paginas)
