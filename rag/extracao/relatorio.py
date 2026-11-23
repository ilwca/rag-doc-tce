"""Geração do `relatorio_extracao.md`."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..schemas import CAMPOS_CRITICOS, Meta

if TYPE_CHECKING:
    from .pdf import Diagnostico
    from .pipeline import Resultado
    from .validacao import ResultadoValidacao

_RE_ILEGIVEL = re.compile(r"\[il[eé]g[ií]vel[^\]]*\]", re.I)
_RE_INCERTO = re.compile(r"\[incerto:[^\]]*\]", re.I)


def contar_marcas(md: str) -> tuple[int, int]:
    return len(_RE_ILEGIVEL.findall(md)), len(_RE_INCERTO.findall(md))


def _v(x: object) -> str:
    if x is None:
        return "—"
    return str(x).replace("|", "\\|").replace("\n", " ")


def gerar_relatorio(
    meta: Meta,
    md: str,
    diag: "Diagnostico",
    validacao: "ResultadoValidacao",
    res: "Resultado",
) -> str:
    L: list[str] = []
    a = L.append
    a(f"# Relatório de extração: {meta.doc_id}\n")
    a("| Item | Valor |\n|---|---|")
    a(f"| Arquivo original | {_v(meta.arquivo_original)} |")
    a(f"| Tipo | {meta.tipo_documento} |")
    a(f"| Título | {_v(meta.titulo)} |")
    a(f"| Páginas | {meta.paginas} ({', '.join(f'{i+1}: {t}' for i, t in enumerate(diag.tipos))}) |")
    a(f"| Origem | {meta.origem} |")
    a(f"| Extração | {meta.extracao.provedor} / {meta.extracao.modelo} / modo {meta.extracao.modo_entrada} |")
    if meta.verificacao:
        v = meta.verificacao
        a(f"| Verificação | {v.provedor} / {v.modelo} / modo {v.modo_entrada} |")
    else:
        a("| Verificação | não executada |")
    a(f"| Validação local | {'OK' if validacao.ok else 'COM ERROS'} |")
    a("")

    a("## Campos críticos\n")
    a("| Campo | Valor | Página | Confiança |\n|---|---|---|---|")
    for campo in CAMPOS_CRITICOS:
        parte, _, sub = campo.partition(".")
        valor = getattr(getattr(meta, parte), sub) if sub else getattr(meta, campo)
        r = meta.rastreabilidade.get(campo)
        a(f"| {campo} | {_v(valor)} | {_v(r.pagina if r else None)} | {_v(r.confianca if r else None)} |")
    a("")

    if meta.campos_especificos:
        a("## Campos específicos\n")
        a("| Campo | Valor | Página | Confiança |\n|---|---|---|---|")
        for k, val in meta.campos_especificos.items():
            r = meta.rastreabilidade.get(k) or meta.rastreabilidade.get(f"campos_especificos.{k}")
            a(f"| {_v(k)} | {_v(val)} | {_v(r.pagina if r else None)} | {_v(r.confianca if r else None)} |")
        a("")

    if meta.tabelas:
        a("## Tabelas\n")
        a("| Página | Título | Linhas |\n|---|---|---|")
        for t in meta.tabelas:
            a(f"| {t.pagina} | {_v(t.titulo)} | {_v(t.n_linhas)} |")
        a("")

    ilegiveis, incertos = contar_marcas(md)
    a("## Trechos ilegíveis e incertos\n")
    a(f"- `[ilegível]` no document.md: **{ilegiveis}**")
    a(f"- `[incerto: …]` no document.md: **{incertos}**")
    a(f"- Campos ilegíveis no meta.json: {', '.join(meta.campos_ilegiveis) or 'nenhum'}")
    for m in _RE_INCERTO.findall(md)[:30]:
        a(f"  - {m}")
    a("")

    a("## Divergências da verificação\n")
    if not meta.verificacao:
        a("Verificação não executada.\n")
    elif not meta.verificacao.divergencias:
        a("Nenhuma: a segunda leitura concorda em todos os campos críticos.\n")
    else:
        a("| Campo | Extração | Verificação |\n|---|---|---|")
        for d in meta.verificacao.divergencias:
            a(f"| {d.campo} | {_v(d.leitura_extracao)} | {_v(d.leitura_verificacao)} |")
        a("")

    a("## Validação local\n")
    if not validacao.problemas:
        a("Todas as regras passaram.\n")
    else:
        for p in validacao.problemas:
            a(f"- **{p.nivel}** [{p.regra}] {p.mensagem}")
        a("")

    a("## Chamadas de API e custo\n")
    a("| Etapa | Provedor/modelo | Cache | Tokens entrada | Tokens saída | Custo (USD) | Latência (s) | Avisos |")
    a("|---|---|---|---|---|---|---|---|")
    for c in res.chamadas:
        custo = f"{c['custo_usd']:.4f}" if c["custo_usd"] is not None else "?"
        a(
            f"| {c['etapa']} | {c['provedor']}/{c['modelo']} | {'sim' if c['cache'] else 'não'} | "
            f"{c['tokens_entrada']} | {c['tokens_saida']} | {custo} | {c['latencia_s']} | {_v(', '.join(c['avisos']) or None)} |"
        )
    a(f"\nCusto desta execução (sem cache): **US$ {res.custo_usd:.4f}**, {res.segundos:.0f} s.\n")

    if res.falhas:
        a("## Falhas\n")
        for f in res.falhas:
            a(f"- {f}")
        a("")
    if meta.observacoes:
        a(f"## Observações do modelo\n\n{meta.observacoes}\n")
    return "\n".join(L)
