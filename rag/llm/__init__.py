"""Camada de provedores de LLM, compartilhada entre extração e consulta."""

from __future__ import annotations

from ..config import Config, Papel, carregar_precos, obter_config
from .base import Anexo, ErroProvedor, ErroSaidaInvalida, LLMProvider, RegistroUso, Resposta, Uso


def criar_provedor(provedor: str, modelo: str, config: Config | None = None) -> LLMProvider:
    config = config or obter_config()
    registro = RegistroUso(config.arquivo_uso, carregar_precos(config))
    if provedor == "anthropic":
        from .claude import AnthropicProvider

        return AnthropicProvider(
            modelo,
            api_key=config.chave("anthropic"),
            effort=config.anthropic_effort,
            fallbacks=config.anthropic_fallbacks,
            registro=registro,
        )
    if provedor == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(
            modelo,
            api_key=config.chave("gemini"),
            resolucao=config.gemini_resolucao_pdf,
            registro=registro,
        )
    if provedor == "openrouter":
        from .openrouter import OpenRouterProvider

        return OpenRouterProvider(
            modelo,
            api_key=config.chave("openrouter"),
            zdr=config.openrouter_zdr,
            pdf_engine=config.openrouter_pdf_engine,
            registro=registro,
        )
    raise ValueError(f"Provedor desconhecido: {provedor!r} (use anthropic | gemini | openrouter)")


def provedor_para(papel: Papel, config: Config | None = None) -> LLMProvider:
    """Provedor configurado no .env para um papel (extracao, verificacao, consulta...)."""
    config = config or obter_config()
    provedor, modelo = config.provedor_e_modelo(papel)
    return criar_provedor(provedor, modelo, config)


__all__ = [
    "Anexo",
    "ErroProvedor",
    "ErroSaidaInvalida",
    "LLMProvider",
    "RegistroUso",
    "Resposta",
    "Uso",
    "criar_provedor",
    "provedor_para",
]
