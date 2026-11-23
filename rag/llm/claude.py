"""Provedor Anthropic (Claude), via SDK oficial `anthropic`."""

from __future__ import annotations

import base64
from typing import Any

import anthropic

from .base import Anexo, ErroProvedor, LLMProvider, Resposta, Uso

BETA_FALLBACK = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    """Claude via Messages API.

    - Usa streaming (`get_final_message`) para saídas longas sem estourar o timeout HTTP.
    - Não envia `temperature`: os modelos atuais rejeitam parâmetros de amostragem.
      O controle de custo é feito pelo `effort` (low | medium | high | xhigh | max).
    - Saída estruturada via `output_config.format` (JSON Schema).
    - `fallbacks="default"`: se o modelo recusar por política de segurança, a API
      refaz a mesma chamada em outro modelo, dentro da mesma requisição.
    """

    nome = "anthropic"

    def __init__(
        self,
        modelo: str,
        *,
        api_key: str,
        effort: str = "low",
        fallbacks: bool = True,
        **kwargs,
    ):
        super().__init__(modelo, **kwargs)
        # max_retries=0: as retentativas ficam com o tenacity (base.py), que registra cada uma.
        self.cliente = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self.effort = effort
        self.fallbacks = fallbacks

    def _eh_retentavel(self, erro: BaseException) -> bool:
        if isinstance(erro, (anthropic.RateLimitError, anthropic.APIConnectionError)):
            return True  # APITimeoutError é subclasse de APIConnectionError
        if isinstance(erro, anthropic.APIStatusError):
            return erro.status_code >= 500  # inclui 529 (overloaded)
        return False

    @staticmethod
    def _bloco(anexo: Anexo) -> dict[str, Any]:
        dados = base64.standard_b64encode(anexo.dados).decode()
        tipo = "document" if anexo.tipo == "pdf" else "image"
        return {"type": tipo, "source": {"type": "base64", "media_type": anexo.mime, "data": dados}}

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
    ) -> dict[str, Any]:
        """Monta a requisição. Separado de `_chamar` para poder ser testado sem rede."""
        conteudo: list[dict[str, Any]] = []
        for anexo in anexos:  # documentos e imagens antes do texto
            if anexo.rotulo:
                conteudo.append({"type": "text", "text": anexo.rotulo})
            conteudo.append(self._bloco(anexo))
        conteudo.append({"type": "text", "text": prompt})

        output_config: dict[str, Any] = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        corpo: dict[str, Any] = {
            "model": self.modelo,
            "max_tokens": max_tokens or 32000,
            "messages": [{"role": "user", "content": conteudo}],
            "output_config": output_config,
        }
        if sistema:
            corpo["system"] = sistema
        if self.fallbacks:
            corpo["betas"] = [BETA_FALLBACK]
            corpo["fallbacks"] = "default"
        return corpo

    def _chamar(self, prompt: str, anexos: list[Anexo], **kwargs) -> Resposta:
        corpo = self.montar_corpo(prompt, anexos, **kwargs)
        try:
            with self.cliente.beta.messages.stream(**corpo) as stream:
                msg = stream.get_final_message()
        except anthropic.APIStatusError as erro:
            if self._eh_retentavel(erro):
                raise
            raise ErroProvedor(f"Anthropic recusou a requisição ({erro.status_code}): {erro.message}") from erro

        bruto = msg.model_dump(mode="json", exclude_none=True)
        avisos: list[str] = []
        if msg.stop_reason == "refusal":
            categoria = msg.stop_details.category if msg.stop_details else None
            raise ErroProvedor(f"Claude recusou o pedido (categoria: {categoria})")
        if msg.stop_reason == "max_tokens":
            avisos.append("stop_reason=max_tokens (saída truncada)")
        if msg.model and msg.model != self.modelo:
            avisos.append(f"respondido_por={msg.model} (fallback)")

        u = msg.usage
        entrada = (u.input_tokens or 0) + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
        uso = Uso(tokens_entrada=entrada, tokens_saida=u.output_tokens or 0)

        texto = "".join(b.text for b in msg.content if b.type == "text")
        if not texto:
            raise ErroProvedor(f"Claude devolveu resposta sem texto (stop_reason={msg.stop_reason})")

        return Resposta(
            texto=texto,
            uso=uso,
            provedor=self.nome,
            modelo=self.modelo,
            latencia_s=0.0,
            bruto=bruto,
            avisos=avisos,
        )
