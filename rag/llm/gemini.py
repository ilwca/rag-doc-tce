"""Provedor Google GenAI (SDK `google-genai`)."""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import errors, types

from .base import Anexo, ErroProvedor, LLMProvider, Resposta, Uso

# Acima disso o PDF vai pela Files API (o limite inline da requisição é ~20 MB).
LIMITE_INLINE_BYTES = 18 * 1024 * 1024

_RESOLUCOES = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}


class GeminiProvider(LLMProvider):
    nome = "gemini"

    def __init__(self, modelo: str, *, api_key: str, resolucao: str = "", **kwargs):
        super().__init__(modelo, **kwargs)
        self.cliente = genai.Client(api_key=api_key)
        self.resolucao = _RESOLUCOES.get(resolucao.lower()) if resolucao else None

    def _eh_retentavel(self, erro: BaseException) -> bool:
        if isinstance(erro, errors.APIError):
            return erro.code == 429 or (erro.code or 0) >= 500
        return isinstance(erro, (TimeoutError, ConnectionError))

    def _parte(self, anexo: Anexo) -> types.Part:
        if anexo.tipo == "pdf" and len(anexo.dados) > LIMITE_INLINE_BYTES:
            import io

            arquivo = self.cliente.files.upload(
                file=io.BytesIO(anexo.dados),
                config=types.UploadFileConfig(mime_type=anexo.mime, display_name=anexo.nome),
            )
            return types.Part.from_uri(file_uri=arquivo.uri, mime_type=anexo.mime)
        return types.Part.from_bytes(data=anexo.dados, mime_type=anexo.mime)

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
    ) -> Resposta:
        partes: list[types.Part] = []
        for anexo in anexos:
            if anexo.rotulo:
                partes.append(types.Part.from_text(text=anexo.rotulo))
            partes.append(self._parte(anexo))
        partes.append(types.Part.from_text(text=prompt))

        config = types.GenerateContentConfig(
            system_instruction=sistema,
            temperature=temperatura,
            max_output_tokens=max_tokens,
            media_resolution=self.resolucao,
        )
        if schema:
            config.response_mime_type = "application/json"
            config.response_json_schema = schema

        try:
            r = self.cliente.models.generate_content(
                model=self.modelo,
                contents=[types.Content(role="user", parts=partes)],
                config=config,
            )
        except errors.APIError as erro:
            if self._eh_retentavel(erro):
                raise
            raise ErroProvedor(f"Gemini recusou a requisição ({erro.code}): {erro.message}") from erro

        um = r.usage_metadata
        uso = Uso(
            tokens_entrada=(um.prompt_token_count or 0) if um else 0,
            tokens_saida=(um.candidates_token_count or 0) if um else 0,
            tokens_raciocinio=(um.thoughts_token_count or 0) if um else 0,
        )
        avisos = []
        motivo = r.candidates[0].finish_reason if r.candidates else None
        if motivo and str(motivo).split(".")[-1] not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
            avisos.append(f"finish_reason={motivo}")
        texto = r.text or ""
        if not texto:
            raise ErroProvedor(f"Gemini devolveu resposta vazia (finish_reason={motivo})")

        return Resposta(
            texto=texto,
            uso=uso,
            provedor=self.nome,
            modelo=self.modelo,
            latencia_s=0.0,
            bruto=r.model_dump(mode="json", exclude_none=True),
            avisos=avisos,
        )
