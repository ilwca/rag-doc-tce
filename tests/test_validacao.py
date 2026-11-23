from rag.extracao.validacao import (
    aparece_no_texto,
    cnpj_valido,
    cpf_valido,
    data_valida,
    normalizar,
    so_alfanum,
    validar,
)


def test_cnpj_cpf():
    assert cnpj_valido("11.222.333/0001-81")
    assert not cnpj_valido("11.222.333/0001-82")
    assert not cnpj_valido("00000000000000")
    assert cpf_valido("529.982.247-25")
    assert not cpf_valido("529.982.247-26")
    assert not cpf_valido("111.111.111-11")


def test_datas():
    assert data_valida("2024-03-05")
    assert data_valida("2024-03")
    assert data_valida("2024-02-30") is None
    assert data_valida("05/03/2024") is None
    assert data_valida("2024") is None


def test_aparece_data_em_formato_brasileiro():
    md = "Válido até: 04/03/2027 e emitido em 5 de março de 2024"
    n, a = normalizar(md), so_alfanum(md)
    assert aparece_no_texto("2027-03-04", n, a, eh_data=True)
    assert aparece_no_texto("2024-03-05", n, a, eh_data=True)
    assert not aparece_no_texto("2027-04-03", n, a, eh_data=True)


def test_aparece_identificador_sem_pontuacao():
    md = "CNPJ 11.222.333/0001-81"
    assert aparece_no_texto("11222333000181", normalizar(md), so_alfanum(md))


def _meta(**kw):
    base = {
        "doc_id": "abc", "sha256": "abc", "arquivo_original": "a.pdf", "paginas": 1, "origem": "escaneada",
        "extracao": {"provedor": "p", "modelo": "m", "modo_entrada": "pdf", "data": "2026-11-23"},
    }
    return {**base, **kw}


def _doc(n=1):
    return {"doc_id": "abc", "paginas": [{"numero": i + 1, "tipo": "escaneada", "elementos": []} for i in range(n)]}


def test_validar_detecta_problemas():
    md = "<!-- página 1 -->\nNº 123\nEmissão 01/01/2025, validade 01/01/2024\nCNPJ 11.222.333/0001-82"
    meta = _meta(
        numero_documento="999",
        data_emissao="2025-01-01",
        data_validade="2024-01-01",
        emissor={"nome": None, "identificador": "11.222.333/0001-82"},
    )
    r = validar(meta, _doc(), md, paginas_pdf=1)
    regras = {p.regra for p in r.problemas if p.nivel == "erro"}
    assert {"data", "cnpj_cpf", "rastreio"} <= regras
    assert not r.ok


def test_validar_paginas_inconsistentes():
    md = "<!-- página 1 -->\ntexto"
    r = validar(_meta(paginas=2), _doc(2), md, paginas_pdf=2)
    assert any(p.regra == "paginas" for p in r.problemas)


def test_validar_ok():
    md = "<!-- página 1 -->\nCertificado nº 123, emitido em 01/01/2024"
    r = validar(_meta(numero_documento="123", data_emissao="2024-01-01"), _doc(), md, paginas_pdf=1)
    assert r.ok, r.problemas
