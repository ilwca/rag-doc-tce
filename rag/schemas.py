"""Modelos Pydantic: meta.json, document.json, saídas dos LLMs e gabarito.

Os modelos com sufixo `LLM` são o que o modelo preenche (saída estruturada).
Eles evitam dicionários livres (`dict[str, ...]`), porque o modo estrito de
JSON Schema exige `additionalProperties: false`. A conversão para o formato
final (`Meta`) é feita em Python.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VERSAO_SCHEMA = "1.0"

TipoDocumento = Literal[
    "certificado", "laudo", "relatorio", "tabela", "declaracao", "contrato", "outro"
]
Origem = Literal["escaneada", "digital", "mista"]
Confianca = Literal["alta", "media", "baixa"]

# Campos críticos: conferidos na verificação e no experimento.
CAMPOS_CRITICOS = (
    "numero_documento",
    "data_emissao",
    "data_validade",
    "emissor.nome",
    "emissor.identificador",
    "titular.nome",
    "titular.identificador",
)


# ---------------------------------------------------------------- comuns


class Parte(BaseModel):
    nome: str | None = None
    identificador: str | None = None


class Responsavel(BaseModel):
    nome: str | None = None
    cargo: str | None = None
    registro: str | None = None


class TabelaResumo(BaseModel):
    pagina: int
    titulo: str | None = None
    descricao: str | None = None
    n_linhas: int | None = None


class Rastro(BaseModel):
    pagina: int | None = None
    confianca: Confianca = "media"


class InfoExtracao(BaseModel):
    provedor: str
    modelo: str
    modo_entrada: str
    data: str


class Divergencia(BaseModel):
    campo: str
    leitura_extracao: str | None
    leitura_verificacao: str | None


class InfoVerificacao(BaseModel):
    provedor: str
    modelo: str
    modo_entrada: str
    campos_conferidos: list[str] = Field(default_factory=list)
    divergencias: list[Divergencia] = Field(default_factory=list)


# ---------------------------------------------------------------- meta.json


class Meta(BaseModel):
    versao_schema: str = VERSAO_SCHEMA
    doc_id: str
    sha256: str
    arquivo_original: str
    paginas: int
    origem: Origem
    idioma: str | None = None
    tipo_documento: TipoDocumento = "outro"
    titulo: str | None = None
    resumo: str | None = None
    palavras_chave: list[str] = Field(default_factory=list)
    emissor: Parte = Field(default_factory=Parte)
    titular: Parte = Field(default_factory=Parte)
    numero_documento: str | None = None
    data_emissao: str | None = None
    data_validade: str | None = None
    local: str | None = None
    responsaveis: list[Responsavel] = Field(default_factory=list)
    campos_especificos: dict[str, Any] = Field(default_factory=dict)
    tabelas: list[TabelaResumo] = Field(default_factory=list)
    rastreabilidade: dict[str, Rastro] = Field(default_factory=dict)
    campos_ilegiveis: list[str] = Field(default_factory=list)
    observacoes: str | None = None
    extracao: InfoExtracao
    # Extensão ao schema do CLAUDE.md: resultado da 2ª leitura (passo 5).
    verificacao: InfoVerificacao | None = None


# ---------------------------------------------------------------- saídas LLM


class CampoValor(BaseModel):
    campo: str
    valor: str | None


class RastroLLM(BaseModel):
    campo: str
    pagina: int | None
    confianca: Confianca


class MetaLLM(BaseModel):
    """O que o modelo preenche a partir da transcrição (prompts/metadados.md)."""

    idioma: str | None
    tipo_documento: TipoDocumento
    titulo: str | None
    resumo: str | None
    palavras_chave: list[str]
    emissor: Parte
    titular: Parte
    numero_documento: str | None
    data_emissao: str | None
    data_validade: str | None
    local: str | None
    responsaveis: list[Responsavel]
    campos_especificos: list[CampoValor]
    tabelas: list[TabelaResumo]
    rastreabilidade: list[RastroLLM]
    campos_ilegiveis: list[str]
    observacoes: str | None


class LeituraCampo(BaseModel):
    valor: str | None
    pagina: int | None
    legivel: bool


class VerificacaoLLM(BaseModel):
    """Segunda leitura independente dos campos críticos (prompts/verificacao.md)."""

    numero_documento: LeituraCampo
    data_emissao: LeituraCampo
    data_validade: LeituraCampo
    emissor_nome: LeituraCampo
    emissor_identificador: LeituraCampo
    titular_nome: LeituraCampo
    titular_identificador: LeituraCampo


# ---------------------------------------------------------------- document.json

TipoElemento = Literal["titulo", "paragrafo", "tabela", "lista", "imagem", "outro"]


class Elemento(BaseModel):
    tipo: TipoElemento
    texto: str
    nivel: int | None = None  # para títulos
    linhas: list[list[str]] | None = None  # para tabelas (1ª linha = cabeçalho)


class PaginaDoc(BaseModel):
    numero: int
    tipo: Literal["digital", "escaneada"]
    elementos: list[Elemento] = Field(default_factory=list)


class Documento(BaseModel):
    versao_schema: str = VERSAO_SCHEMA
    doc_id: str
    paginas: list[PaginaDoc]


# ---------------------------------------------------------------- gabarito


class TabelaGabarito(BaseModel):
    pagina: int
    titulo: str | None = None
    linhas: list[list[str]]  # 1ª linha = cabeçalho


class Gabarito(BaseModel):
    doc_id: str
    revisado_por_humano: bool = False
    campos: dict[str, str | None]  # chaves de CAMPOS_CRITICOS
    tabelas: list[TabelaGabarito] = Field(default_factory=list)
    observacoes: str | None = None
