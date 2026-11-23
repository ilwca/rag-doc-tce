"""Operações locais sobre o PDF: identificação, diagnóstico, rasterização e recortes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Uma página com menos caracteres extraíveis que isso é tratada como escaneada.
MIN_CARACTERES_DIGITAL = 50


@dataclass
class Diagnostico:
    paginas: int
    tipos: list[str]  # "digital" | "escaneada", um por página

    @property
    def origem(self) -> str:
        unicos = set(self.tipos)
        if unicos == {"digital"}:
            return "digital"
        if unicos == {"escaneada"}:
            return "escaneada"
        return "mista"


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def doc_id_de(sha256: str) -> str:
    return sha256[:12]


def diagnosticar(caminho: Path) -> Diagnostico:
    with pymupdf.open(caminho) as doc:
        tipos = [
            "digital" if len(p.get_text("text").strip()) >= MIN_CARACTERES_DIGITAL else "escaneada"
            for p in doc
        ]
        return Diagnostico(paginas=len(tipos), tipos=tipos)


def rasterizar(caminho: Path, dpi: int = 200, paginas: list[int] | None = None) -> list[tuple[int, bytes]]:
    """PNG de cada página (numeração a partir de 1)."""
    saida = []
    with pymupdf.open(caminho) as doc:
        for numero in paginas or range(1, len(doc) + 1):
            pix = doc[numero - 1].get_pixmap(dpi=dpi)
            saida.append((numero, pix.tobytes("png")))
    return saida


def recortes(
    caminho: Path,
    paginas: list[int],
    dpi: int = 350,
    faixas: int = 3,
    sobreposicao: float = 0.12,
) -> list[tuple[int, int, bytes]]:
    """Recortes ampliados: cada página dividida em faixas horizontais sobrepostas.

    Não depende de localizar os campos: a página inteira é coberta em alta
    resolução, e a sobreposição evita cortar uma linha de texto ao meio.
    Retorna (página, faixa, png).
    """
    saida = []
    with pymupdf.open(caminho) as doc:
        for numero in paginas:
            pagina = doc[numero - 1]
            r = pagina.rect
            altura = r.height / faixas
            margem = altura * sobreposicao
            for i in range(faixas):
                y0 = max(r.y0, r.y0 + i * altura - margem)
                y1 = min(r.y1, r.y0 + (i + 1) * altura + margem)
                clip = pymupdf.Rect(r.x0, y0, r.x1, y1)
                pix = pagina.get_pixmap(dpi=dpi, clip=clip)
                saida.append((numero, i + 1, pix.tobytes("png")))
    return saida
