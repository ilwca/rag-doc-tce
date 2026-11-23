"""CLI: python -m rag.extrair <arquivo|pasta> [--provedor] [--modelo] [--modo] [--force]"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

from .config import carregar_precos, obter_config
from .extracao import pdf
from .llm import criar_provedor

app = typer.Typer(add_completion=False, help="Extrai PDFs para corpus/{doc_id}/")

# Valores aproximados para a estimativa prévia de custo (não para cobrança).
TOKENS_POR_PAGINA = 1100  # PDF em resolução alta ou PNG a 200 DPI
TOKENS_SAIDA_POR_PAGINA = 900
TOKENS_PROMPTS = 3000


def estimar_custo(paginas: int, provedor: str, modelo: str) -> float | None:
    precos = carregar_precos()
    p = precos.get(f"{provedor}/{modelo}") or precos.get(modelo)
    if not p:
        return None
    entrada = paginas * TOKENS_POR_PAGINA + TOKENS_PROMPTS  # transcrição
    saida = paginas * TOKENS_SAIDA_POR_PAGINA
    entrada += saida + TOKENS_PROMPTS  # metadados lê a transcrição
    saida += 2000
    return (entrada * p["entrada"] + saida * p["saida"]) / 1_000_000


def listar_pdfs(alvo: Path) -> list[Path]:
    if alvo.is_file():
        return [alvo]
    return sorted(p for p in alvo.rglob("*") if p.suffix.lower() == ".pdf")


@app.command()
def main(
    alvo: Path = typer.Argument(..., exists=True, help="Arquivo PDF ou pasta"),
    provedor: Optional[str] = typer.Option(None, help="anthropic | gemini | openrouter (padrão: EXTRACAO_PROVEDOR)"),
    modelo: Optional[str] = typer.Option(None, help="padrão: EXTRACAO_MODELO"),
    modo: Optional[str] = typer.Option(None, help="pdf | imagens | auto (padrão: EXTRACAO_MODO_ENTRADA)"),
    force: bool = typer.Option(False, "--force", help="Reprocessa documentos já extraídos"),
    sem_cache: bool = typer.Option(False, "--sem-cache", help="Ignora as respostas guardadas em raw/ (paga de novo)"),
    sem_verificacao: bool = typer.Option(False, "--sem-verificacao", help="Pula a 2ª leitura"),
    sim: bool = typer.Option(False, "--sim", "-y", help="Não pede confirmação para lotes"),
) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    config = obter_config()
    prov = provedor or config.extracao_provedor
    mod = modelo or (config.extracao_modelo if prov == config.extracao_provedor else "")
    if not mod:
        raise typer.BadParameter("Informe --modelo ou configure EXTRACAO_MODELO no .env")

    arquivos = listar_pdfs(alvo)
    pendentes = []
    for a in arquivos:
        doc_id = pdf.doc_id_de(pdf.sha256_arquivo(a))
        if force or not (config.dir_corpus / doc_id / "meta.json").exists():
            pendentes.append(a)
    typer.echo(f"{len(arquivos)} PDF(s), {len(pendentes)} a processar com {prov}/{mod}.")
    if not pendentes:
        return

    if len(pendentes) > 1 and not sim:
        paginas = sum(pdf.diagnosticar(a).paginas for a in pendentes)
        est = estimar_custo(paginas, prov, mod)
        est_txt = f"≈ US$ {est:.2f}" if est is not None else "desconhecido (modelo fora de precos.json)"
        typer.echo(f"Total: {paginas} páginas. Custo estimado da extração (sem verificação): {est_txt}")
        typer.confirm("Processar todos?", abort=True)

    from .extracao.pipeline import Pipeline

    pipe = Pipeline(
        config,
        extrator=criar_provedor(prov, mod, config),
        modo=modo,
        verificar=not sem_verificacao,
        usar_cache=not sem_cache,
    )
    total = 0.0
    for a in pendentes:
        typer.echo(f"→ {a.name}")
        try:
            r = pipe.processar(a, force=force)
        except Exception as erro:
            typer.secho(f"  FALHOU: {erro}", fg="red")
            continue
        if r.pulado:
            typer.echo(f"  {r.doc_id}: já processado (use --force)")
            continue
        total += r.custo_usd
        estado = "validação OK" if r.validacao_ok else "validação COM ERROS"
        typer.echo(
            f"  {r.doc_id}: {estado}; {r.tokens_entrada}+{r.tokens_saida} tokens; "
            f"US$ {r.custo_usd:.4f}; {r.segundos:.0f}s → {r.pasta / 'relatorio_extracao.md'}"
        )
        for f in r.falhas:
            typer.secho(f"  aviso: {f}", fg="yellow")
    typer.echo(f"Custo total desta execução: US$ {total:.4f}")


if __name__ == "__main__":
    app()
