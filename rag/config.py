"""Configuração do projeto, carregada do `.env` (pydantic-settings).

Nenhum ID de modelo fica fixo no código: tudo vem do `.env`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent.parent

Provedor = Literal["anthropic", "gemini", "openrouter"]
ModoEntrada = Literal["pdf", "imagens", "auto"]
ModoVerificacao = Literal["pdf", "imagens", "recortes"]
Papel = Literal["extracao", "verificacao", "consulta", "embedding"]


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    extracao_provedor: Provedor = "anthropic"
    extracao_modelo: str = ""
    verificacao_provedor: Provedor | None = None
    verificacao_modelo: str = ""
    consulta_provedor: Provedor | None = None
    consulta_modelo: str = ""
    embedding_provedor: Provedor | None = None
    embedding_modelo: str = ""

    # Claude: nível de esforço (low | medium | high | xhigh | max). Mais alto = mais tokens de raciocínio.
    anthropic_effort: str = "low"
    # Fallback do lado do servidor quando o modelo recusa por política de segurança.
    anthropic_fallbacks: bool = True

    openrouter_zdr: bool = True
    # Engine para PDFs no OpenRouter: native | mistral-ocr | cloudflare-ai.
    # Só "native" fica dentro do ZDR.
    openrouter_pdf_engine: str = "native"

    extracao_modo_entrada: ModoEntrada = "auto"
    verificacao_modo_entrada: ModoVerificacao = "recortes"

    # Resolução das páginas de PDF no Gemini: low | medium | high | "" (padrão do modelo)
    gemini_resolucao_pdf: str = "high"

    dpi_imagens: int = 200
    dpi_recortes: int = 350
    temperatura_extracao: float = 0.0
    max_tokens_saida: int = 32000

    dir_corpus: Path = RAIZ / "corpus"
    dir_gabarito: Path = RAIZ / "gabarito"
    dir_prompts: Path = RAIZ / "prompts"
    arquivo_uso: Path = RAIZ / "logs" / "uso.jsonl"
    arquivo_precos: Path = RAIZ / "precos.json"

    @field_validator("verificacao_provedor", "consulta_provedor", "embedding_provedor", mode="before")
    @classmethod
    def _vazio_e_nulo(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    def provedor_e_modelo(self, papel: Papel) -> tuple[str, str]:
        provedor = getattr(self, f"{papel}_provedor")
        modelo = getattr(self, f"{papel}_modelo")
        if not provedor or not modelo:
            raise ValueError(
                f"Configure {papel.upper()}_PROVEDOR e {papel.upper()}_MODELO no .env"
            )
        return provedor, modelo

    def chave(self, provedor: str) -> str:
        segredo = {
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
        }[provedor]
        if segredo is None or not segredo.get_secret_value():
            raise ValueError(f"Chave de API ausente para o provedor '{provedor}' no .env")
        return segredo.get_secret_value()


@lru_cache
def obter_config() -> Config:
    return Config()


def carregar_prompt(nome: str, config: Config | None = None) -> str:
    config = config or obter_config()
    return (config.dir_prompts / f"{nome}.md").read_text(encoding="utf-8")


def carregar_precos(config: Config | None = None) -> dict[str, dict[str, float]]:
    """Tabela de preços (USD por 1M tokens), mantida em `precos.json`, fora do código."""
    config = config or obter_config()
    if not config.arquivo_precos.exists():
        return {}
    dados = json.loads(config.arquivo_precos.read_text(encoding="utf-8"))
    return {k: v for k, v in dados.items() if not k.startswith("_")}
