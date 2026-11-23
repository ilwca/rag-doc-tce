"""Testes contra APIs reais (pagos). Rode com: uv run pytest -m api"""

import pytest

from rag.config import obter_config
from rag.llm import provedor_para
from rag.schemas import Parte

pytestmark = pytest.mark.api


@pytest.mark.parametrize("papel", ["extracao", "verificacao"])
def test_saida_estruturada_real(papel):
    cfg = obter_config()
    try:
        provedor = provedor_para(papel, cfg)
    except ValueError as e:
        pytest.skip(str(e))
    r = provedor.gerar(
        "Extraia nome e identificador de: 'Empresa Alfa Ltda., CNPJ 11.222.333/0001-81'.",
        schema=Parte,
        contexto={"etapa": "teste_api"},
    )
    assert r.dados.identificador and "0001-81" in r.dados.identificador
