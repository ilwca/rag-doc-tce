"""Orquestra os passos 1–8 da extração de um PDF."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import Config, carregar_prompt, obter_config
from ..llm import Anexo, LLMProvider, criar_provedor
from ..llm.base import Resposta, Uso
from ..schemas import (
    CAMPOS_CRITICOS,
    Divergencia,
    InfoExtracao,
    InfoVerificacao,
    Meta,
    MetaLLM,
    Rastro,
    VerificacaoLLM,
)
from . import pdf
from .markdown import md_para_documento
from .relatorio import gerar_relatorio
from .validacao import normalizar, so_alfanum, validar

log = logging.getLogger(__name__)


@dataclass
class Resultado:
    doc_id: str
    pasta: Path
    pulado: bool = False
    validacao_ok: bool | None = None
    custo_usd: float = 0.0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    segundos: float = 0.0
    chamadas: list[dict[str, Any]] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- cache


def _slug(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", texto)


def chamar_com_cache(
    provedor: LLMProvider,
    dir_raw: Path,
    etapa: str,
    modo: str,
    prompt: str,
    anexos: list[Anexo],
    *,
    schema: type[BaseModel] | None = None,
    temperatura: float = 0.0,
    max_tokens: int | None = None,
    usar_cache: bool = True,
    contexto: dict[str, Any] | None = None,
) -> tuple[Resposta, bool]:
    """Chama o provedor, guardando a resposta bruta em `raw/`.

    A chave do cache inclui o prompt e os anexos: mudar o prompt invalida o cache.
    Retorna (resposta, veio_do_cache).
    """
    h = hashlib.sha256(prompt.encode())
    for a in anexos:
        h.update(a.dados)
    h.update(f"{temperatura}|{schema.__name__ if schema else ''}".encode())
    chave = h.hexdigest()[:10]
    arquivo = dir_raw / f"{etapa}__{provedor.nome}__{_slug(provedor.modelo)}__{modo}__{chave}.json"

    if usar_cache and arquivo.exists():
        salvo = json.loads(arquivo.read_text(encoding="utf-8"))
        r = Resposta(
            texto=salvo["texto"],
            uso=Uso(**salvo["uso"]),
            provedor=salvo["provedor"],
            modelo=salvo["modelo"],
            latencia_s=salvo["latencia_s"],
            bruto=salvo.get("bruto", {}),
            avisos=salvo.get("avisos", []),
        )
        if schema:
            from ..llm.base import extrair_json

            r.dados = schema.model_validate(extrair_json(r.texto))
        return r, True

    r = provedor.gerar(
        prompt, anexos, schema=schema, temperatura=temperatura, max_tokens=max_tokens, contexto=contexto
    )
    dir_raw.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(
        json.dumps(
            {
                "etapa": etapa,
                "modo": modo,
                "provedor": r.provedor,
                "modelo": r.modelo,
                "data": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "latencia_s": r.latencia_s,
                "uso": r.uso.__dict__,
                "avisos": r.avisos,
                "texto": r.texto,
                "bruto": r.bruto,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return r, False


# ---------------------------------------------------------------- entradas


def resolver_modo(modo: str, diag: pdf.Diagnostico) -> str:
    """`auto` → modo decidido pelo experimento (ver docs/decisoes.md).

    Enquanto o experimento não rodar, `auto` usa `pdf` (provisório, D-003).
    """
    if modo != "auto":
        return modo
    return "pdf"


def anexos_documento(caminho: Path, modo: str, dpi: int, dir_paginas: Path | None = None) -> list[Anexo]:
    if modo == "pdf":
        return [Anexo.pdf(caminho.read_bytes(), nome=caminho.name)]
    if modo == "imagens":
        imagens = pdf.rasterizar(caminho, dpi=dpi)
        if dir_paginas:
            dir_paginas.mkdir(parents=True, exist_ok=True)
            for n, png in imagens:
                (dir_paginas / f"pagina_{n:03d}.png").write_bytes(png)
        return [Anexo.png(png, f"pagina_{n:03d}.png", rotulo=f"Página {n}:") for n, png in imagens]
    raise ValueError(f"Modo de entrada inválido: {modo}")


def anexos_recortes(caminho: Path, paginas: list[int], dpi: int, dir_paginas: Path | None) -> list[Anexo]:
    saida = []
    for n, faixa, png in pdf.recortes(caminho, paginas, dpi=dpi):
        nome = f"recorte_p{n:03d}_f{faixa}.png"
        if dir_paginas:
            dir_paginas.mkdir(parents=True, exist_ok=True)
            (dir_paginas / nome).write_bytes(png)
        saida.append(Anexo.png(png, nome, rotulo=f"Página {n}, faixa {faixa} de 3 (de cima para baixo):"))
    return saida


# ---------------------------------------------------------------- etapas


def meta_de_llm(m: MetaLLM, base: dict[str, Any]) -> Meta:
    return Meta(
        **base,
        idioma=m.idioma,
        tipo_documento=m.tipo_documento,
        titulo=m.titulo,
        resumo=m.resumo,
        palavras_chave=m.palavras_chave,
        emissor=m.emissor,
        titular=m.titular,
        numero_documento=m.numero_documento,
        data_emissao=m.data_emissao,
        data_validade=m.data_validade,
        local=m.local,
        responsaveis=m.responsaveis,
        campos_especificos={c.campo: c.valor for c in m.campos_especificos},
        tabelas=m.tabelas,
        rastreabilidade={r.campo: Rastro(pagina=r.pagina, confianca=r.confianca) for r in m.rastreabilidade},
        campos_ilegiveis=m.campos_ilegiveis,
        observacoes=m.observacoes,
    )


def valor_campo(meta: Meta, campo: str) -> str | None:
    if "." in campo:
        parte, sub = campo.split(".")
        return getattr(getattr(meta, parte), sub)
    return getattr(meta, campo)


def mesmo_valor(campo: str, a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if campo.startswith("data_"):
        return a.strip() == b.strip()
    if campo.endswith("identificador") or campo == "numero_documento":
        return so_alfanum(a) == so_alfanum(b)
    return normalizar(a) == normalizar(b)


def comparar_verificacao(meta: Meta, v: VerificacaoLLM) -> list[Divergencia]:
    divergencias = []
    for campo in CAMPOS_CRITICOS:
        leitura = getattr(v, campo.replace(".", "_"))
        valor_v = leitura.valor if leitura.legivel else None
        valor_e = valor_campo(meta, campo)
        if not mesmo_valor(campo, valor_e, valor_v):
            divergencias.append(Divergencia(campo=campo, leitura_extracao=valor_e, leitura_verificacao=valor_v))
    return divergencias


# ---------------------------------------------------------------- pipeline


class Pipeline:
    def __init__(
        self,
        config: Config | None = None,
        *,
        extrator: LLMProvider | None = None,
        verificador: LLMProvider | None = None,
        modo: str | None = None,
        verificar: bool = True,
        usar_cache: bool = True,
        dir_saida: Path | None = None,
    ):
        self.config = config or obter_config()
        if extrator is None:
            prov, modelo = self.config.provedor_e_modelo("extracao")
            extrator = criar_provedor(prov, modelo, self.config)
        if verificador is None and verificar and self.config.verificacao_provedor and self.config.verificacao_modelo:
            prov, modelo = self.config.provedor_e_modelo("verificacao")
            verificador = criar_provedor(prov, modelo, self.config)
        self.extrator = extrator
        self.verificador = verificador if verificar else None
        self.modo = modo or self.config.extracao_modo_entrada
        self.usar_cache = usar_cache
        self.dir_saida = dir_saida or self.config.dir_corpus

    def processar(self, caminho: Path, force: bool = False) -> Resultado:
        inicio = datetime.now()
        # 1. Identificação
        sha = pdf.sha256_arquivo(caminho)
        doc_id = pdf.doc_id_de(sha)
        pasta = self.dir_saida / doc_id
        res = Resultado(doc_id=doc_id, pasta=pasta)
        if (pasta / "meta.json").exists() and not force:
            res.pulado = True
            return res
        pasta.mkdir(parents=True, exist_ok=True)
        original = pasta / "original.pdf"
        if not original.exists():
            shutil.copy2(caminho, original)
        dir_raw = pasta / "raw"
        dir_paginas = pasta / "paginas"
        ctx = {"doc_id": doc_id}

        def registrar(etapa: str, r: Resposta, do_cache: bool) -> None:
            custo = None
            if self.extrator.registro:
                custo = self.extrator.registro.estimar_custo(r.provedor, r.modelo, r.uso)
            res.chamadas.append(
                {
                    "etapa": etapa,
                    "provedor": r.provedor,
                    "modelo": r.modelo,
                    "cache": do_cache,
                    "tokens_entrada": r.uso.tokens_entrada,
                    "tokens_saida": r.uso.tokens_saida + r.uso.tokens_raciocinio,
                    "custo_usd": custo,
                    "latencia_s": round(r.latencia_s, 1),
                    "avisos": r.avisos,
                }
            )
            if not do_cache:
                res.tokens_entrada += r.uso.tokens_entrada
                res.tokens_saida += r.uso.tokens_saida + r.uso.tokens_raciocinio
                res.custo_usd += custo or 0.0

        # 2. Diagnóstico
        diag = pdf.diagnosticar(original)
        modo = resolver_modo(self.modo, diag)

        # 3. Transcrição
        prompt_t = carregar_prompt("transcricao", self.config).replace("{{PAGINAS}}", str(diag.paginas))
        anexos = anexos_documento(original, modo, self.config.dpi_imagens, dir_paginas)
        r_t, c = chamar_com_cache(
            self.extrator, dir_raw, "transcricao", modo, prompt_t, anexos,
            temperatura=self.config.temperatura_extracao, max_tokens=self.config.max_tokens_saida,
            usar_cache=self.usar_cache, contexto={**ctx, "etapa": "transcricao", "modo": modo},
        )
        registrar("transcricao", r_t, c)
        md = r_t.texto.strip()
        md = re.sub(r"^```(?:markdown|md)?\s*\n(.*)\n```$", r"\1", md, flags=re.S)
        (pasta / "document.md").write_text(md + "\n", encoding="utf-8")

        # 4. Estruturação: document.json (local) + meta.json (LLM sobre a transcrição)
        documento = md_para_documento(md, doc_id, diag.tipos)
        (pasta / "document.json").write_text(
            documento.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
        )
        prompt_m = carregar_prompt("metadados", self.config) + "\n\n<documento>\n" + md + "\n</documento>\n"
        r_m, c = chamar_com_cache(
            self.extrator, dir_raw, "metadados", "texto", prompt_m, [], schema=MetaLLM,
            temperatura=self.config.temperatura_extracao, max_tokens=self.config.max_tokens_saida,
            usar_cache=self.usar_cache, contexto={**ctx, "etapa": "metadados"},
        )
        registrar("metadados", r_m, c)
        meta = meta_de_llm(
            r_m.dados,  # type: ignore[arg-type]
            {
                "doc_id": doc_id,
                "sha256": sha,
                "arquivo_original": caminho.name,
                "paginas": diag.paginas,
                "origem": diag.origem,
                "extracao": InfoExtracao(
                    provedor=self.extrator.nome,
                    modelo=self.extrator.modelo,
                    modo_entrada=modo,
                    data=date.today().isoformat(),
                ),
            },
        )

        # 5. Verificação independente dos campos críticos
        if self.verificador:
            modo_v = self.config.verificacao_modo_entrada
            if modo_v == "recortes":
                paginas_v = sorted(
                    {meta.rastreabilidade[c].pagina for c in CAMPOS_CRITICOS
                     if c in meta.rastreabilidade and meta.rastreabilidade[c].pagina}
                ) or [1]
                paginas_v = [p for p in paginas_v if 1 <= p <= diag.paginas] or [1]
                anexos_v = anexos_recortes(original, paginas_v, self.config.dpi_recortes, dir_paginas)
            else:
                anexos_v = anexos_documento(original, modo_v, self.config.dpi_imagens, dir_paginas)
            try:
                r_v, c = chamar_com_cache(
                    self.verificador, dir_raw, "verificacao", modo_v,
                    carregar_prompt("verificacao", self.config), anexos_v, schema=VerificacaoLLM,
                    temperatura=self.config.temperatura_extracao, usar_cache=self.usar_cache,
                    contexto={**ctx, "etapa": "verificacao", "modo": modo_v},
                )
                registrar("verificacao", r_v, c)
                divergencias = comparar_verificacao(meta, r_v.dados)  # type: ignore[arg-type]
                for d in divergencias:
                    rastro = meta.rastreabilidade.get(d.campo) or Rastro()
                    rastro.confianca = "baixa"
                    meta.rastreabilidade[d.campo] = rastro
                meta.verificacao = InfoVerificacao(
                    provedor=self.verificador.nome,
                    modelo=self.verificador.modelo,
                    modo_entrada=modo_v,
                    campos_conferidos=list(CAMPOS_CRITICOS),
                    divergencias=divergencias,
                )
            except Exception as erro:  # a verificação falhar não perde a extração
                log.exception("Falha na verificação de %s", doc_id)
                res.falhas.append(f"verificação: {erro}")

        meta_dict = json.loads(meta.model_dump_json())
        (pasta / "meta.json").write_text(json.dumps(meta_dict, ensure_ascii=False, indent=2), encoding="utf-8")

        # 6. Validação local
        validacao = validar(meta_dict, json.loads(documento.model_dump_json()), md, diag.paginas)
        res.validacao_ok = validacao.ok

        # 7. Relatório
        res.segundos = (datetime.now() - inicio).total_seconds()
        (pasta / "relatorio_extracao.md").write_text(
            gerar_relatorio(meta, md, diag, validacao, res), encoding="utf-8"
        )
        return res
