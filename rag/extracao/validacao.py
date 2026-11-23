"""Validações locais (Python, sem LLM) do resultado da extração."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal

from pydantic import ValidationError

from ..schemas import Documento, Meta
from .markdown import MARCADOR_PAGINA

MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
_RE_DATA_ISO = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")
_RE_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_RE_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")


@dataclass
class Problema:
    nivel: Literal["erro", "aviso"]
    regra: str
    mensagem: str


@dataclass
class ResultadoValidacao:
    problemas: list[Problema] = field(default_factory=list)

    def erro(self, regra: str, msg: str) -> None:
        self.problemas.append(Problema("erro", regra, msg))

    def aviso(self, regra: str, msg: str) -> None:
        self.problemas.append(Problema("aviso", regra, msg))

    @property
    def ok(self) -> bool:
        return not any(p.nivel == "erro" for p in self.problemas)


# ---------------------------------------------------------------- utilidades


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, espaços colapsados, sem marcação Markdown."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[*_`|#>]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip().casefold()


def so_alfanum(texto: str) -> str:
    return re.sub(r"[^0-9a-z]", "", normalizar(texto))


def data_valida(valor: str) -> date | None:
    """Converte AAAA-MM-DD ou AAAA-MM (dia 1) em date; None se inválida."""
    m = _RE_DATA_ISO.match(valor)
    if not m:
        return None
    ano, mes, dia = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def cnpj_valido(numero: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", numero)]
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for tam in (12, 13):
        pesos = list(range(tam - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(a * b for a, b in zip(d[:tam], pesos))
        dv = 0 if soma % 11 < 2 else 11 - soma % 11
        if d[tam] != dv:
            return False
    return True


def cpf_valido(numero: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", numero)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for tam in (9, 10):
        soma = sum(a * b for a, b in zip(d[:tam], range(tam + 1, 1, -1)))
        dv = (soma * 10 % 11) % 10
        if d[tam] != dv:
            return False
    return True


def variantes_data(iso: str) -> list[str]:
    """Formas como uma data ISO pode aparecer no documento."""
    m = _RE_DATA_ISO.match(iso)
    if not m:
        return [iso]
    a, mm, dd = m.group(1), m.group(2), m.group(3)
    mes_nome = MESES[int(mm) - 1]
    if dd is None:
        return [f"{mm}/{a}", f"{mm}.{a}", f"{mm}-{a}", f"{mes_nome} de {a}", f"{mes_nome}/{a}", f"{int(mm)}/{a}"]
    d, m_ = int(dd), int(mm)
    saida = []
    for dia in {dd, str(d)}:
        for mes in {mm, str(m_)}:
            for sep in "/.-":
                saida += [f"{dia}{sep}{mes}{sep}{a}", f"{dia}{sep}{mes}{sep}{a[2:]}"]
        saida += [f"{dia} de {mes_nome} de {a}", f"{dia}/{mes_nome}/{a}", f"{dia} {mes_nome} {a}"]
    saida += [iso, f"{d}º de {mes_nome} de {a}", f"1o de {mes_nome} de {a}" if d == 1 else iso]
    return saida


def aparece_no_texto(valor: Any, md_norm: str, md_alfanum: str, eh_data: bool = False) -> bool:
    if valor is None or isinstance(valor, bool):
        return True
    if isinstance(valor, (int, float)):
        valor = str(valor)
    if not isinstance(valor, str) or not valor.strip():
        return True
    if eh_data:
        return any(normalizar(v) in md_norm for v in variantes_data(valor))
    if normalizar(valor) in md_norm:
        return True
    compacto = so_alfanum(valor)
    return bool(compacto) and compacto in md_alfanum


def _valores_meta(meta: Meta) -> Iterable[tuple[str, Any, bool]]:
    """(campo, valor, é_data) dos campos do meta.json que precisam estar no documento."""
    yield "numero_documento", meta.numero_documento, False
    yield "data_emissao", meta.data_emissao, True
    yield "data_validade", meta.data_validade, True
    yield "local", meta.local, False
    for parte in ("emissor", "titular"):
        p = getattr(meta, parte)
        yield f"{parte}.nome", p.nome, False
        yield f"{parte}.identificador", p.identificador, False
    for i, r in enumerate(meta.responsaveis):
        yield f"responsaveis[{i}].nome", r.nome, False
        yield f"responsaveis[{i}].registro", r.registro, False

    def achatar(prefixo: str, v: Any) -> Iterable[tuple[str, Any, bool]]:
        if isinstance(v, dict):
            for k, x in v.items():
                yield from achatar(f"{prefixo}.{k}", x)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                yield from achatar(f"{prefixo}[{i}]", x)
        else:
            eh_data = isinstance(v, str) and bool(_RE_DATA_ISO.match(v))
            yield prefixo, v, eh_data

    for k, v in meta.campos_especificos.items():
        yield from achatar(f"campos_especificos.{k}", v)


# ---------------------------------------------------------------- validação


def validar(meta_dict: dict, documento_dict: dict, md: str, paginas_pdf: int) -> ResultadoValidacao:
    res = ResultadoValidacao()

    # 1. Schemas
    try:
        meta = Meta.model_validate(meta_dict)
    except ValidationError as e:
        res.erro("schema", f"meta.json inválido: {e}")
        return res
    try:
        documento = Documento.model_validate(documento_dict)
    except ValidationError as e:
        res.erro("schema", f"document.json inválido: {e}")
        documento = None

    # 2. Datas
    datas = {}
    for campo in ("data_emissao", "data_validade"):
        valor = getattr(meta, campo)
        if valor is None:
            continue
        d = data_valida(valor)
        if d is None:
            res.erro("data", f"{campo}={valor!r} não é data ISO válida (AAAA-MM-DD ou AAAA-MM)")
        else:
            datas[campo] = d
    if "data_emissao" in datas and "data_validade" in datas and datas["data_validade"] < datas["data_emissao"]:
        res.erro("data", f"data_validade ({meta.data_validade}) anterior à data_emissao ({meta.data_emissao})")

    # 3. CNPJ/CPF: nos identificadores do meta e em todo o documento.md
    for parte in ("emissor", "titular"):
        ident = getattr(meta, parte).identificador
        if not ident:
            continue
        digitos = re.sub(r"\D", "", ident)
        if len(digitos) == 14 and not cnpj_valido(digitos):
            res.erro("cnpj_cpf", f"{parte}.identificador: CNPJ {ident} com dígito verificador inválido")
        elif len(digitos) == 11 and not cpf_valido(digitos):
            res.erro("cnpj_cpf", f"{parte}.identificador: CPF {ident} com dígito verificador inválido")
    vistos = set()
    for m in _RE_CNPJ.finditer(md):
        if m.group() not in vistos and not cnpj_valido(m.group()):
            res.aviso("cnpj_cpf", f"CNPJ no documento.md com dígito verificador inválido: {m.group()} (erro de leitura?)")
        vistos.add(m.group())
    for m in _RE_CPF.finditer(md):
        texto = m.group()
        if texto in vistos or any(texto in v for v in vistos):
            continue
        vistos.add(texto)
        if not cpf_valido(texto):
            res.aviso("cnpj_cpf", f"Possível CPF no documento.md com dígito verificador inválido: {texto}")

    # 4. Número de páginas
    marcadores = sorted({int(n) for n in MARCADOR_PAGINA.findall(md)})
    contagens = {"PDF": paginas_pdf, "meta.json": meta.paginas, "marcadores no document.md": len(marcadores)}
    if documento is not None:
        contagens["document.json"] = len(documento.paginas)
    if len(set(contagens.values())) > 1:
        res.erro("paginas", "Número de páginas inconsistente: " + ", ".join(f"{k}={v}" for k, v in contagens.items()))
    if marcadores and marcadores != list(range(1, paginas_pdf + 1)):
        res.erro("paginas", f"Marcadores de página fora de ordem ou faltando: {marcadores}")

    # 5. Todo valor do meta.json aparece no document.md
    md_norm = normalizar(md)
    md_alfanum = so_alfanum(md)
    for campo, valor, eh_data in _valores_meta(meta):
        if not aparece_no_texto(valor, md_norm, md_alfanum, eh_data):
            res.erro("rastreio", f"{campo}={valor!r} não aparece no document.md")

    return res
