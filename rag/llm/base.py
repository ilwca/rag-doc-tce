"""Interface comum dos provedores de LLM.

Usada na extração (fase 1) e, depois, na consulta (fase 3). Cada provedor
implementa só `_chamar` (montar a requisição e ler a resposta) e
`_eh_retentavel`. O resto fica aqui: retentativas, validação da saída
estruturada e registro de uso.
"""

from __future__ import annotations

import copy
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)


@dataclass
class Anexo:
    tipo: Literal["pdf", "imagem"]
    dados: bytes
    mime: str
    nome: str = "anexo"
    rotulo: str | None = None  # texto enviado antes do anexo, ex.: "Página 3"

    @classmethod
    def pdf(cls, dados: bytes, nome: str = "documento.pdf") -> "Anexo":
        return cls("pdf", dados, "application/pdf", nome)

    @classmethod
    def png(cls, dados: bytes, nome: str = "pagina.png", rotulo: str | None = None) -> "Anexo":
        return cls("imagem", dados, "image/png", nome, rotulo)


@dataclass
class Uso:
    tokens_entrada: int = 0
    tokens_saida: int = 0
    tokens_raciocinio: int = 0
    custo_usd: float | None = None  # informado pelo provedor, quando houver


@dataclass
class Resposta:
    texto: str
    uso: Uso
    provedor: str
    modelo: str
    latencia_s: float
    dados: BaseModel | None = None  # saída estruturada validada
    bruto: dict[str, Any] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)


class ErroProvedor(RuntimeError):
    """Erro de API que não deve ser repetido (ou que esgotou as tentativas)."""


class ErroSaidaInvalida(ErroProvedor):
    def __init__(self, mensagem: str, texto: str):
        super().__init__(mensagem)
        self.texto = texto


class RegistroUso:
    """Acrescenta uma linha por chamada em logs/uso.jsonl. Nunca registra chaves."""

    def __init__(self, caminho: Path, precos: dict[str, dict[str, float]] | None = None):
        self.caminho = caminho
        self.precos = precos or {}

    def estimar_custo(self, provedor: str, modelo: str, uso: Uso) -> float | None:
        if uso.custo_usd is not None:
            return uso.custo_usd
        preco = self.precos.get(f"{provedor}/{modelo}") or self.precos.get(modelo)
        if not preco:
            return None
        saida = uso.tokens_saida + uso.tokens_raciocinio
        return (uso.tokens_entrada * preco["entrada"] + saida * preco["saida"]) / 1_000_000

    def registrar(self, resposta: Resposta, contexto: dict[str, Any] | None = None) -> dict:
        linha = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provedor": resposta.provedor,
            "modelo": resposta.modelo,
            **asdict(resposta.uso),
            "custo_usd": self.estimar_custo(resposta.provedor, resposta.modelo, resposta.uso),
            "custo_fonte": "provedor" if resposta.uso.custo_usd is not None else "tabela",
            "latencia_s": round(resposta.latencia_s, 2),
            "avisos": resposta.avisos,
            **(contexto or {}),
        }
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.caminho.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
        return linha


# ---------------------------------------------------------------- JSON Schema


def schema_estrito(modelo: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema do modelo Pydantic, no formato aceito em modo estrito.

    Resolve `$ref`, remove `default`/`title` e marca todos os campos como
    obrigatórios com `additionalProperties: false`. Campos opcionais continuam
    aceitando `null`, que é como representamos "ausente".
    """
    bruto = modelo.model_json_schema()
    defs = bruto.pop("$defs", {})

    def resolver(no: Any) -> Any:
        if isinstance(no, dict):
            if "$ref" in no:
                nome = no["$ref"].split("/")[-1]
                return resolver(copy.deepcopy(defs[nome]))
            novo = {k: resolver(v) for k, v in no.items() if k not in ("default", "title")}
            if novo.get("type") == "object" and "properties" in novo:
                novo["required"] = list(novo["properties"].keys())
                novo["additionalProperties"] = False
            return novo
        if isinstance(no, list):
            return [resolver(x) for x in no]
        return no

    return resolver(bruto)


def extrair_json(texto: str) -> Any:
    """Lê JSON da resposta, tolerando cercas ```json que alguns modelos acrescentam."""
    texto = texto.strip()
    cerca = re.match(r"^```(?:json)?\s*(.*?)\s*```$", texto, re.S)
    if cerca:
        texto = cerca.group(1)
    return json.loads(texto)


# ---------------------------------------------------------------- provedor


class LLMProvider(ABC):
    nome: str = "base"

    def __init__(
        self,
        modelo: str,
        *,
        registro: RegistroUso | None = None,
        tentativas: int = 5,
        espera_inicial: float = 2.0,
    ):
        if not modelo:
            raise ValueError("Modelo não configurado (veja o .env)")
        self.modelo = modelo
        self.registro = registro
        self.tentativas = tentativas
        self.espera_inicial = espera_inicial

    # --- a implementar por provedor

    @abstractmethod
    def _chamar(
        self,
        prompt: str,
        anexos: list[Anexo],
        *,
        sistema: str | None,
        schema: dict[str, Any] | None,
        nome_schema: str,
        temperatura: float,
        max_tokens: int | None,
    ) -> Resposta: ...

    @abstractmethod
    def _eh_retentavel(self, erro: BaseException) -> bool: ...

    # --- API pública

    def gerar(
        self,
        prompt: str,
        anexos: list[Anexo] | None = None,
        *,
        sistema: str | None = None,
        schema: type[BaseModel] | None = None,
        temperatura: float = 0.0,
        max_tokens: int | None = None,
        contexto: dict[str, Any] | None = None,
    ) -> Resposta:
        """Faz uma chamada (com retentativas) e valida a saída estruturada, se houver."""
        anexos = anexos or []
        schema_json = schema_estrito(schema) if schema else None

        @retry(
            retry=retry_if_exception(self._eh_retentavel),
            wait=wait_exponential_jitter(initial=self.espera_inicial, max=60, jitter=self.espera_inicial),
            stop=stop_after_attempt(self.tentativas),
            reraise=True,
        )
        def tentar() -> Resposta:
            inicio = time.perf_counter()
            resposta = self._chamar(
                prompt,
                anexos,
                sistema=sistema,
                schema=schema_json,
                nome_schema=schema.__name__ if schema else "",
                temperatura=temperatura,
                max_tokens=max_tokens,
            )
            resposta.latencia_s = time.perf_counter() - inicio
            return resposta

        resposta = tentar()
        if self.registro:
            self.registro.registrar(resposta, contexto)

        if schema:
            try:
                resposta.dados = schema.model_validate(extrair_json(resposta.texto))
            except (json.JSONDecodeError, ValidationError) as erro:
                raise ErroSaidaInvalida(
                    f"Saída de {self.nome}/{self.modelo} não segue o schema {schema.__name__}: {erro}",
                    resposta.texto,
                ) from erro
        return resposta
