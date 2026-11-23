import json

import pytest

from rag.avaliar import pontuar
from rag.extracao import pdf
from rag.extracao.markdown import dividir_paginas, md_para_documento
from rag.extracao.pipeline import Pipeline
from rag.schemas import Gabarito, Meta
from tests.conftest import MD_EXEMPLO, META_LLM, VERIF_LLM, ProvedorFalso


def test_markdown_para_documento():
    doc = md_para_documento(MD_EXEMPLO, "x", ["escaneada", "escaneada"])
    assert len(doc.paginas) == 2
    tabela = [e for e in doc.paginas[0].elementos if e.tipo == "tabela"][0]
    assert tabela.linhas == [["Ensaio", "Resultado", "Unidade"], ["Tensão", "220", "V"], ["Corrente", "[incerto: 15]", "A"]]
    assert doc.paginas[0].elementos[0].tipo == "titulo"
    assert any(e.tipo == "imagem" for e in doc.paginas[1].elementos)
    assert set(dividir_paginas(MD_EXEMPLO)) == {1, 2}


def test_diagnostico(pdf_exemplo):
    d = pdf.diagnosticar(pdf_exemplo)
    assert d.paginas == 2 and d.tipos == ["escaneada", "escaneada"] and d.origem == "escaneada"
    assert len(pdf.recortes(pdf_exemplo, [1], dpi=72)) == 3


@pytest.mark.parametrize("modo,anexos", [("pdf", ["pdf"]), ("imagens", ["imagem", "imagem"])])
def test_pipeline_completo(config, pdf_exemplo, extrator, modo, anexos):
    verificador = ProvedorFalso({"VerificacaoLLM": json.dumps(VERIF_LLM)})
    pipe = Pipeline(config, extrator=extrator, verificador=verificador, modo=modo)
    r = pipe.processar(pdf_exemplo)

    pasta = r.pasta
    for nome in ("original.pdf", "document.md", "document.json", "meta.json", "relatorio_extracao.md"):
        assert (pasta / nome).exists(), nome
    assert list((pasta / "raw").glob("transcricao__*.json"))
    assert extrator.chamadas[0]["anexos"] == anexos
    assert extrator.chamadas[0]["temperatura"] == 0.0
    assert verificador.chamadas[0]["anexos"] == ["imagem"] * 3  # recortes da página 1

    meta = Meta.model_validate_json((pasta / "meta.json").read_text())
    assert meta.doc_id == r.doc_id == pasta.name
    assert meta.campos_especificos == {"corrente": "15"}
    assert meta.verificacao and meta.verificacao.divergencias == []
    assert r.validacao_ok, (pasta / "relatorio_extracao.md").read_text()
    assert r.custo_usd > 0
    assert (config.arquivo_uso).exists()


def test_pipeline_pula_e_usa_cache(config, pdf_exemplo, extrator):
    pipe = Pipeline(config, extrator=extrator, verificar=False, modo="pdf")
    pipe.processar(pdf_exemplo)
    n = len(extrator.chamadas)
    assert pipe.processar(pdf_exemplo).pulado
    r = pipe.processar(pdf_exemplo, force=True)
    assert len(extrator.chamadas) == n  # tudo veio do cache raw/
    assert r.custo_usd == 0 and all(c["cache"] for c in r.chamadas)


def test_divergencia_marca_confianca_baixa(config, pdf_exemplo, extrator):
    verif = dict(VERIF_LLM, data_validade={"valor": "2027-08-04", "pagina": 1, "legivel": True})
    verificador = ProvedorFalso({"VerificacaoLLM": json.dumps(verif)})
    r = Pipeline(config, extrator=extrator, verificador=verificador, modo="pdf").processar(pdf_exemplo)
    meta = Meta.model_validate_json((r.pasta / "meta.json").read_text())
    assert meta.rastreabilidade["data_validade"].confianca == "baixa"
    d = meta.verificacao.divergencias[0]
    assert (d.campo, d.leitura_extracao, d.leitura_verificacao) == ("data_validade", "2027-03-04", "2027-08-04")
    assert "2027-08-04" in (r.pasta / "relatorio_extracao.md").read_text()


def test_validacao_detecta_valor_inventado(config, pdf_exemplo):
    meta = dict(META_LLM, numero_documento="2024/9999")
    ext = ProvedorFalso({"texto": MD_EXEMPLO, "MetaLLM": json.dumps(meta)})
    r = Pipeline(config, extrator=ext, verificar=False, modo="pdf").processar(pdf_exemplo)
    assert r.validacao_ok is False
    assert "2024/9999" in (r.pasta / "relatorio_extracao.md").read_text()


def test_pontuar_avaliacao(config, pdf_exemplo, extrator):
    r = Pipeline(config, extrator=extrator, verificar=False, modo="pdf").processar(pdf_exemplo)
    gab = Gabarito(
        doc_id=r.doc_id,
        revisado_por_humano=True,
        campos={"numero_documento": "2024/0157", "data_validade": "2027-03-04", "data_emissao": "2024-03-06"},
        tabelas=[{"pagina": 1, "linhas": [["Ensaio", "Resultado", "Unidade"], ["Tensão", "220", "V"], ["Corrente", "15", "A"]]}],
    )
    p = pontuar("teste", gab, r.pasta)
    assert (p.campos_certos, p.campos_total) == (2, 3)
    assert p.tabelas_encontradas == 1 and p.linhas_certas == 1 and p.colunas_certas == 1
    assert (p.celulas_certas, p.celulas_total) == (8, 9)
    assert p.incertos == 1
