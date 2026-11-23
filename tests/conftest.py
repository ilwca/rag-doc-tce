from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from rag.config import Config, RAIZ
from rag.llm.base import Anexo, LLMProvider, RegistroUso, Resposta, Uso

MD_EXEMPLO = """<!-- página 1 -->
# CERTIFICADO DE CONFORMIDADE Nº 2024/0157

Emitido por: Laboratório Alfa Ltda. — CNPJ 11.222.333/0001-81

Titular: Beta Indústria S.A.

Data de emissão: 05/03/2024
Válido até: 04/03/2027

| Ensaio | Resultado | Unidade |
|---|---|---|
| Tensão | 220 | V |
| Corrente | [incerto: 15] | A |

<!-- página 2 -->
Responsável técnico: João da Silva — CREA 12345/D

[assinatura]

São Paulo, 05 de março de 2024
"""

META_LLM = {
    "idioma": "pt",
    "tipo_documento": "certificado",
    "titulo": "CERTIFICADO DE CONFORMIDADE Nº 2024/0157",
    "resumo": "Certificado de conformidade emitido pelo Laboratório Alfa para a Beta Indústria.",
    "palavras_chave": ["certificado", "conformidade"],
    "emissor": {"nome": "Laboratório Alfa Ltda.", "identificador": "11.222.333/0001-81"},
    "titular": {"nome": "Beta Indústria S.A.", "identificador": None},
    "numero_documento": "2024/0157",
    "data_emissao": "2024-03-05",
    "data_validade": "2027-03-04",
    "local": "São Paulo",
    "responsaveis": [{"nome": "João da Silva", "cargo": "Responsável técnico", "registro": "CREA 12345/D"}],
    "campos_especificos": [{"campo": "corrente", "valor": "15"}],
    "tabelas": [{"pagina": 1, "titulo": None, "descricao": "Ensaios", "n_linhas": 2}],
    "rastreabilidade": [
        {"campo": "numero_documento", "pagina": 1, "confianca": "alta"},
        {"campo": "data_validade", "pagina": 1, "confianca": "alta"},
    ],
    "campos_ilegiveis": [],
    "observacoes": None,
}

VERIF_LLM = {
    "numero_documento": {"valor": "2024/0157", "pagina": 1, "legivel": True},
    "data_emissao": {"valor": "2024-03-05", "pagina": 1, "legivel": True},
    "data_validade": {"valor": "2027-03-04", "pagina": 1, "legivel": True},
    "emissor_nome": {"valor": "Laboratório Alfa Ltda.", "pagina": 1, "legivel": True},
    "emissor_identificador": {"valor": "11222333000181", "pagina": 1, "legivel": True},
    "titular_nome": {"valor": "Beta Indústria S.A.", "pagina": 1, "legivel": True},
    "titular_identificador": {"valor": None, "pagina": None, "legivel": True},
}


class ProvedorFalso(LLMProvider):
    """Responde conforme o schema pedido; registra as chamadas."""

    nome = "falso"

    def __init__(self, respostas: dict[str, str], **kw):
        super().__init__("modelo-falso", **kw)
        self.respostas = respostas
        self.chamadas: list[dict] = []

    def _eh_retentavel(self, erro: BaseException) -> bool:
        return isinstance(erro, TimeoutError)

    def _chamar(self, prompt, anexos: list[Anexo], *, sistema, schema, nome_schema, temperatura, max_tokens):
        self.chamadas.append({"nome_schema": nome_schema, "anexos": [a.tipo for a in anexos], "temperatura": temperatura})
        return Resposta(
            texto=self.respostas[nome_schema or "texto"],
            uso=Uso(tokens_entrada=1000, tokens_saida=200),
            provedor=self.nome,
            modelo=self.modelo,
            latencia_s=0.0,
        )


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        _env_file=None,
        extracao_provedor="gemini",
        extracao_modelo="x",
        dir_corpus=tmp_path / "corpus",
        dir_gabarito=tmp_path / "gabarito",
        dir_prompts=RAIZ / "prompts",
        arquivo_uso=tmp_path / "uso.jsonl",
        arquivo_precos=tmp_path / "precos.json",
    )


@pytest.fixture
def pdf_exemplo(tmp_path: Path) -> Path:
    caminho = tmp_path / "entrada" / "cert.pdf"
    caminho.parent.mkdir()
    doc = pymupdf.open()
    for texto in ("Certificado (pagina escaneada simulada)", ""):
        p = doc.new_page()
        if texto:
            p.insert_text((72, 72), texto)
    doc.save(caminho)
    return caminho


@pytest.fixture
def extrator(config: Config) -> ProvedorFalso:
    return ProvedorFalso(
        {"texto": MD_EXEMPLO, "MetaLLM": json.dumps(META_LLM)},
        registro=RegistroUso(config.arquivo_uso, {"falso/modelo-falso": {"entrada": 1.0, "saida": 2.0}}),
    )
