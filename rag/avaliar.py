"""CLI do experimento pdf × imagens (ver CLAUDE.md, "Investigação obrigatória").

python -m rag.avaliar --combinacao gemini:MODELO:pdf --combinacao gemini:MODELO:imagens ...

Para cada documento com gabarito em `gabarito/{doc_id}.json` e cada combinação
provedor × modelo × modo, roda transcrição + metadados (sem verificação) em
`avaliacao/{combinacao}/{doc_id}/` e mede:
- acerto nos campos críticos;
- fidelidade das tabelas (linhas, colunas e células);
- quantidade de [ilegível] e [incerto: …];
- tokens, custo e tempo.
O resultado vai para `avaliacao/resultado.md` e `avaliacao/resultado.json`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import typer

from .config import RAIZ, obter_config
from .extracao.markdown import tabelas_md
from .extracao.pipeline import Pipeline, mesmo_valor, valor_campo
from .extracao.relatorio import contar_marcas
from .extracao.validacao import normalizar
from .llm import criar_provedor
from .schemas import CAMPOS_CRITICOS, Gabarito, Meta, TabelaGabarito

app = typer.Typer(add_completion=False, help=__doc__)
DIR_AVALIACAO = RAIZ / "avaliacao"


@dataclass
class Pontuacao:
    combinacao: str
    doc_id: str
    campos_certos: int = 0
    campos_total: int = 0
    erros_campos: list[str] = field(default_factory=list)
    tabelas_esperadas: int = 0
    tabelas_encontradas: int = 0
    linhas_certas: int = 0  # tabelas com nº de linhas igual ao gabarito
    colunas_certas: int = 0
    celulas_certas: int = 0
    celulas_total: int = 0
    ilegiveis: int = 0
    incertos: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    custo_usd: float = 0.0
    segundos: float = 0.0
    falha: str | None = None


def comparar_tabela(esperada: TabelaGabarito, candidatas: list[list[list[str]]]) -> tuple[bool, bool, int, int] | None:
    """Compara com a candidata mais parecida. Retorna (linhas_ok, colunas_ok, células certas, células totais)."""
    if not candidatas:
        return None
    esp = esperada.linhas
    total = sum(len(l) for l in esp)

    def acertos(cand: list[list[str]]) -> int:
        n = 0
        for i, linha in enumerate(esp):
            for j, cel in enumerate(linha):
                if i < len(cand) and j < len(cand[i]) and normalizar(cand[i][j]) == normalizar(cel):
                    n += 1
        return n

    melhor = max(candidatas, key=acertos)
    linhas_ok = len(melhor) == len(esp)
    colunas_ok = all(len(a) == len(b) for a, b in zip(melhor, esp)) and linhas_ok
    return linhas_ok, colunas_ok, acertos(melhor), total


def pontuar(combinacao: str, gab: Gabarito, pasta: Path) -> Pontuacao:
    p = Pontuacao(combinacao, gab.doc_id)
    meta = Meta.model_validate_json((pasta / "meta.json").read_text(encoding="utf-8"))
    md = (pasta / "document.md").read_text(encoding="utf-8")

    for campo in CAMPOS_CRITICOS:
        if campo not in gab.campos:
            continue
        p.campos_total += 1
        obtido = valor_campo(meta, campo)
        if mesmo_valor(campo, obtido, gab.campos[campo]):
            p.campos_certos += 1
        else:
            p.erros_campos.append(f"{campo}: esperado {gab.campos[campo]!r}, obtido {obtido!r}")

    from .extracao.markdown import dividir_paginas

    por_pagina = dividir_paginas(md)
    for t in gab.tabelas:
        p.tabelas_esperadas += 1
        cmp = comparar_tabela(t, tabelas_md(por_pagina.get(t.pagina, ""))) or comparar_tabela(t, tabelas_md(md))
        p.celulas_total += sum(len(l) for l in t.linhas)
        if cmp:
            p.tabelas_encontradas += 1
            p.linhas_certas += cmp[0]
            p.colunas_certas += cmp[1]
            p.celulas_certas += cmp[2]

    p.ilegiveis, p.incertos = contar_marcas(md)
    return p


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}% ({a}/{b})" if b else "—"


def tabela_resumo(pontos: list[Pontuacao]) -> str:
    combos: dict[str, list[Pontuacao]] = {}
    for p in pontos:
        combos.setdefault(p.combinacao, []).append(p)
    L = [
        "| Combinação | Campos críticos | Células de tabela | Tabelas c/ linhas certas | Tabelas c/ colunas certas | [ilegível] | [incerto] | Tokens (ent+saí) | Custo US$ | Tempo s | Falhas |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c, ps in combos.items():
        s = lambda k: sum(getattr(x, k) for x in ps)  # noqa: E731
        L.append(
            f"| {c} | {_pct(s('campos_certos'), s('campos_total'))} | {_pct(s('celulas_certas'), s('celulas_total'))} "
            f"| {_pct(s('linhas_certas'), s('tabelas_esperadas'))} | {_pct(s('colunas_certas'), s('tabelas_esperadas'))} "
            f"| {s('ilegiveis')} | {s('incertos')} | {s('tokens_entrada')}+{s('tokens_saida')} "
            f"| {s('custo_usd'):.4f} | {s('segundos'):.0f} | {sum(1 for x in ps if x.falha)} |"
        )
    return "\n".join(L)


@app.command()
def main(
    combinacao: list[str] = typer.Option(..., help="provedor:modelo:modo (repita a opção)"),
    docs: list[str] = typer.Option(None, help="doc_ids (padrão: todos os gabaritos revisados)"),
    incluir_nao_revisados: bool = typer.Option(False, help="Usa também gabaritos sem revisão humana"),
    sim: bool = typer.Option(False, "--sim", "-y"),
) -> None:
    config = obter_config()
    gabaritos = []
    for arq in sorted(config.dir_gabarito.glob("*.json")):
        g = Gabarito.model_validate_json(arq.read_text(encoding="utf-8"))
        if docs and g.doc_id not in docs:
            continue
        if not g.revisado_por_humano and not incluir_nao_revisados:
            typer.secho(f"Pulando {g.doc_id}: gabarito ainda não revisado por humano", fg="yellow")
            continue
        if not (config.dir_corpus / g.doc_id / "original.pdf").exists():
            typer.secho(f"Pulando {g.doc_id}: corpus/{g.doc_id}/original.pdf não existe", fg="yellow")
            continue
        gabaritos.append(g)
    if not gabaritos:
        raise typer.Exit("Nenhum gabarito utilizável em gabarito/.")

    combos = []
    for c in combinacao:
        prov, modelo, modo = c.split(":", 2) if c.count(":") >= 2 else (*c.split(":", 1), "pdf")
        modo = modo if modo in ("pdf", "imagens") else "pdf"
        combos.append((prov, modelo, modo))

    typer.echo(f"{len(gabaritos)} documento(s) × {len(combos)} combinação(ões) = {len(gabaritos) * len(combos)} extrações.")
    if not sim:
        typer.confirm("Continuar? (chamadas pagas; respostas em cache não são cobradas de novo)", abort=True)

    pontos: list[Pontuacao] = []
    for prov, modelo, modo in combos:
        nome = f"{prov}:{modelo}:{modo}"
        pipe = Pipeline(
            config,
            extrator=criar_provedor(prov, modelo, config),
            modo=modo,
            verificar=False,
            dir_saida=DIR_AVALIACAO / nome.replace("/", "_").replace(":", "__"),
        )
        for g in gabaritos:
            typer.echo(f"→ {nome} / {g.doc_id}")
            original = config.dir_corpus / g.doc_id / "original.pdf"
            try:
                r = pipe.processar(original, force=True)
                p = pontuar(nome, g, r.pasta)
                p.tokens_entrada, p.tokens_saida = r.tokens_entrada, r.tokens_saida
                p.custo_usd = r.custo_usd
                # latência real (do cache, se for o caso)
                p.segundos = sum(c["latencia_s"] for c in r.chamadas)
            except Exception as erro:
                p = Pontuacao(nome, g.doc_id, falha=str(erro))
                typer.secho(f"  falhou: {erro}", fg="red")
            pontos.append(p)

    DIR_AVALIACAO.mkdir(exist_ok=True)
    (DIR_AVALIACAO / "resultado.json").write_text(
        json.dumps([asdict(p) for p in pontos], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    resumo = tabela_resumo(pontos)
    detalhes = "\n".join(
        f"- {p.combinacao} / {p.doc_id}: " + ("; ".join(p.erros_campos) or "sem erros") + (f" — FALHA: {p.falha}" if p.falha else "")
        for p in pontos
    )
    (DIR_AVALIACAO / "resultado.md").write_text(
        f"# Resultado do experimento pdf × imagens\n\n{resumo}\n\n## Erros por documento\n\n{detalhes}\n",
        encoding="utf-8",
    )
    typer.echo(resumo)
    typer.echo(f"\nDetalhes em {DIR_AVALIACAO / 'resultado.md'}. Registre a decisão em docs/decisoes.md.")


if __name__ == "__main__":
    app()
