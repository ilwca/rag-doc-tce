"""Provedor OpenRouter (API compatível com OpenAI, via SDK `openai`)."""

from __future__ import annotations

import base64
from typing import Any

import openai

from .base import Anexo, ErroProvedor, LLMProvider, Resposta, Uso

BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(LLMProvider):
    nome = "openrouter"

    def __init__(
        self,
        modelo: str,
        *,
        api_key: str,
        zdr: bool = True,
        pdf_engine: str = "native",
        **kwargs,
    ):
        super().__init__(modelo, **kwargs)
        # max_retries=0: as retentativas ficam com o tenacity (base.py).
        self.cliente = openai.OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=0)
        self.zdr = zdr
        self.pdf_engine = pdf_engine

    def _eh_retentavel(self, erro: BaseException) -> bool:
        if isinstance(erro, openai.APIStatusError):
            return erro.status_code == 429 or erro.status_code >= 500
        return isinstance(erro, (openai.APIConnectionError, openai.APITimeoutError))

    @staticmethod
    def _data_url(anexo: Anexo) -> str:
        return f"data:{anexo.mime};base64,{base64.b64encode(anexo.dados).decode()}"

    def _parte(self, anexo: Anexo) -> dict[str, Any]:
        if anexo.tipo == "pdf":
            return {"type": "file", "file": {"filename": anexo.nome, "file_data": self._data_url(anexo)}}
        return {"type": "image_url", "image_url": {"url": self._data_url(anexo)}}

    def montar_corpo(
        self,
        prompt: str,
        anexos: list[Anexo],
        *,
        sistema: str | None,
        schema: dict[str, Any] | None,
        nome_schema: str,
        temperatura: float,
        max_tokens: int | None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Monta a requisição. Separado de `_chamar` para poder ser testado sem rede."""
        conteudo: list[dict[str, Any]] = []
        for anexo in anexos:
            if anexo.rotulo:
                conteudo.append({"type": "text", "text": anexo.rotulo})
            conteudo.append(self._parte(anexo))
        conteudo.append({"type": "text", "text": prompt})

        mensagens: list[dict[str, Any]] = []
        if sistema:
            mensagens.append({"role": "system", "content": sistema})
        mensagens.append({"role": "user", "content": conteudo})

        provider: dict[str, Any] = {}
        if self.zdr:
            provider.update({"zdr": True, "data_collection": "deny"})
        extra: dict[str, Any] = {}
        avisos: list[str] = []

        if any(a.tipo == "pdf" for a in anexos):
            extra["plugins"] = [{"id": "file-parser", "pdf": {"engine": self.pdf_engine}}]
            if self.pdf_engine != "native":
                # O ZDR não cobre plugins: o PDF passa pelo serviço do parser.
                avisos.append(f"plugin_fora_do_zdr=file-parser/{self.pdf_engine}")

        corpo: dict[str, Any] = {
            "model": self.modelo,
            "messages": mensagens,
            "temperature": temperatura,
        }
        if max_tokens:
            corpo["max_tokens"] = max_tokens
        if schema:
            corpo["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": nome_schema or "saida", "strict": True, "schema": schema},
            }
            provider["require_parameters"] = True
        if provider:
            extra["provider"] = provider
        corpo["extra_body"] = extra
        return corpo, avisos

    def _chamar(self, prompt: str, anexos: list[Anexo], **kwargs) -> Resposta:
        corpo, avisos = self.montar_corpo(prompt, anexos, **kwargs)
        try:
            r = self.cliente.chat.completions.create(**corpo)
        except openai.APIStatusError as erro:
            if self._eh_retentavel(erro):
                raise
            raise ErroProvedor(
                f"OpenRouter recusou a requisição ({erro.status_code}): {erro.message}"
            ) from erro

        bruto = r.model_dump(mode="json", exclude_none=True)
        if not r.choices:
            raise ErroProvedor(f"OpenRouter devolveu resposta sem conteúdo: {bruto.get('error')}")
        escolha = r.choices[0]
        if escolha.finish_reason not in (None, "stop"):
            avisos.append(f"finish_reason={escolha.finish_reason}")

        u = bruto.get("usage") or {}
        detalhes = u.get("completion_tokens_details") or {}
        raciocinio = detalhes.get("reasoning_tokens") or 0
        uso = Uso(
            tokens_entrada=u.get("prompt_tokens") or 0,
            tokens_saida=(u.get("completion_tokens") or 0) - raciocinio,
            tokens_raciocinio=raciocinio,
            custo_usd=u.get("cost"),  # o OpenRouter informa o custo (créditos ≈ USD)
        )
        texto = escolha.message.content or ""
        if not texto:
            raise ErroProvedor(f"OpenRouter devolveu texto vazio (finish_reason={escolha.finish_reason})")

        return Resposta(
            texto=texto,
            uso=uso,
            provedor=self.nome,
            modelo=bruto.get("model") or self.modelo,
            latencia_s=0.0,
            bruto=bruto,
            avisos=avisos,
        )
