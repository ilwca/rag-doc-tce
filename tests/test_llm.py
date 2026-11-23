import json

import pytest

from rag.llm.base import Anexo, ErroSaidaInvalida, RegistroUso, schema_estrito
from rag.llm.openrouter import OpenRouterProvider
from rag.schemas import MetaLLM, VerificacaoLLM
from tests.conftest import META_LLM, ProvedorFalso


def test_schema_estrito_sem_refs_e_fechado():
    s = schema_estrito(MetaLLM)
    texto = json.dumps(s)
    assert "$ref" not in texto and "$defs" not in texto
    assert s["additionalProperties"] is False
    assert set(s["required"]) == set(s["properties"])
    emissor = s["properties"]["emissor"]
    assert emissor["additionalProperties"] is False
    assert set(emissor["required"]) == {"nome", "identificador"}


def test_gerar_valida_schema_e_registra_uso(tmp_path):
    reg = RegistroUso(tmp_path / "uso.jsonl", {"falso/modelo-falso": {"entrada": 1.0, "saida": 2.0}})
    p = ProvedorFalso({"MetaLLM": json.dumps(META_LLM)}, registro=reg)
    r = p.gerar("x", schema=MetaLLM, contexto={"etapa": "teste"})
    assert isinstance(r.dados, MetaLLM)
    linha = json.loads((tmp_path / "uso.jsonl").read_text())
    assert linha["tokens_entrada"] == 1000 and linha["etapa"] == "teste"
    assert linha["custo_usd"] == pytest.approx((1000 * 1 + 200 * 2) / 1e6)
    assert "key" not in json.dumps(linha).lower()


def test_gerar_aceita_cerca_json():
    p = ProvedorFalso({"MetaLLM": "```json\n" + json.dumps(META_LLM) + "\n```"})
    assert p.gerar("x", schema=MetaLLM).dados is not None


def test_gerar_rejeita_saida_invalida():
    p = ProvedorFalso({"VerificacaoLLM": '{"numero_documento": 1}'})
    with pytest.raises(ErroSaidaInvalida):
        p.gerar("x", schema=VerificacaoLLM)


def test_retentativa_em_erro_transitorio():
    class Instavel(ProvedorFalso):
        falhas = 2

        def _chamar(self, *a, **k):
            if self.falhas:
                self.falhas -= 1
                raise TimeoutError()
            return super()._chamar(*a, **k)

    p = Instavel({"texto": "ok"}, espera_inicial=0)
    assert p.gerar("x").texto == "ok"
    assert p.falhas == 0


def test_openrouter_corpo_zdr_e_plugin():
    p = OpenRouterProvider("vendor/modelo", api_key="sk-teste", zdr=True, pdf_engine="native")
    corpo, avisos = p.montar_corpo(
        "prompt", [Anexo.pdf(b"%PDF-1.4")], sistema=None, schema=schema_estrito(VerificacaoLLM),
        nome_schema="VerificacaoLLM", temperatura=0.0, max_tokens=None,
    )
    extra = corpo["extra_body"]
    assert extra["provider"]["zdr"] is True
    assert extra["provider"]["data_collection"] == "deny"
    assert extra["plugins"][0]["pdf"]["engine"] == "native"
    assert corpo["response_format"]["json_schema"]["strict"] is True
    assert corpo["messages"][0]["content"][0]["type"] == "file"
    assert avisos == []

    p2 = OpenRouterProvider("vendor/modelo", api_key="sk-teste", pdf_engine="mistral-ocr")
    _, avisos2 = p2.montar_corpo(
        "prompt", [Anexo.pdf(b"%PDF")], sistema=None, schema=None, nome_schema="", temperatura=0, max_tokens=None
    )
    assert any("fora_do_zdr" in a for a in avisos2)


def test_openrouter_imagens_com_rotulo():
    p = OpenRouterProvider("v/m", api_key="k")
    corpo, _ = p.montar_corpo(
        "prompt", [Anexo.png(b"png", rotulo="Página 1:")], sistema="sys", schema=None,
        nome_schema="", temperatura=0, max_tokens=100,
    )
    conteudo = corpo["messages"][1]["content"]
    assert [c["type"] for c in conteudo] == ["text", "image_url", "text"]
    assert "plugins" not in corpo["extra_body"]


def test_anthropic_corpo():
    from rag.llm.claude import AnthropicProvider

    p = AnthropicProvider("claude-opus-5-5", api_key="sk-teste", effort="low")
    corpo = p.montar_corpo(
        "prompt", [Anexo.pdf(b"%PDF-1.4"), Anexo.png(b"png", rotulo="Página 1:")], sistema=None,
        schema=schema_estrito(VerificacaoLLM), nome_schema="VerificacaoLLM", temperatura=0.0, max_tokens=None,
    )
    tipos = [b["type"] for b in corpo["messages"][0]["content"]]
    assert tipos == ["document", "text", "image", "text"]  # anexos antes do prompt
    assert corpo["output_config"]["effort"] == "low"
    assert corpo["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in corpo  # rejeitado pelos modelos atuais
    assert corpo["fallbacks"] == "default" and corpo["betas"]

    sem = AnthropicProvider("m", api_key="k", fallbacks=False).montar_corpo(
        "p", [], sistema="s", schema=None, nome_schema="", temperatura=0, max_tokens=100
    )
    assert "format" not in sem["output_config"] and "fallbacks" not in sem and sem["system"] == "s"


def test_anthropic_retentativas():
    import httpx2 as httpx
    import anthropic

    from rag.llm.claude import AnthropicProvider

    p = AnthropicProvider("m", api_key="k")
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    erro = lambda c: anthropic.APIStatusError("x", response=httpx.Response(c, request=req), body=None)  # noqa: E731
    assert p._eh_retentavel(erro(529)) and p._eh_retentavel(erro(500))
    assert not p._eh_retentavel(erro(400))
