"""Testes do Robô de BI: confere os números do robô contra um cálculo independente,
feito direto nos arquivos brutos (sem passar pelo motor).

Rodar:  python -m pytest tests/test_robo_bi.py -q     (ou)     python tests/test_robo_bi.py
"""
import csv
import json
import random
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from robo_bi import generico, motor, seguros  # noqa: E402
from robo_bi.exemplo import escrever_xlsx, gerar_saude, gerar_seguradora  # noqa: E402
from robo_bi.formatos import ler_arquivo, ler_pasta  # noqa: E402
from robo_bi.leitura import classificar, para_competencia, para_data, para_numero  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="robo_bi_teste_"))


def _br(s):
    return float(s.replace(".", "").replace(",", "."))


def test_conversores():
    assert para_numero("1.234,56") == 1234.56
    assert para_numero("1,234.56") == 1234.56
    assert para_numero("R$ 10,5") == 10.5
    assert para_numero("(100,00)") == -100.0
    assert para_numero("1.234") == 1234.0
    assert para_numero("abc") is None
    assert para_competencia("jan/25") == (2025, 1)
    assert para_competencia("03/2026") == (2026, 3)
    assert para_competencia("2026-03") == (2026, 3)
    assert str(para_data("31/12/2025")) == "2025-12-31"
    assert para_data("31/02/2026") is None


def test_formatos_mesma_tabela():
    """A mesma tabela salva em 6 formatos tem que ser lida igual."""
    cab = ["Data", "Ramo", "Prêmio"]
    linhas = [["2026-01-05", "Auto", 100.5], ["2026-02-10", "Vida", 200.25]]
    d = TMP / "fmt"
    d.mkdir()
    with open(d / "a.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=";").writerows([cab] + linhas)
    (d / "b.json").write_text(json.dumps([dict(zip(cab, l)) for l in linhas]), encoding="utf-8")
    (d / "c.xml").write_text("<r>" + "".join(f"<item><Data>{a}</Data><Ramo>{b}</Ramo><Prêmio>{c}</Prêmio></item>"
                                            for a, b, c in linhas) + "</r>", encoding="utf-8")
    (d / "d.html").write_text("<table><tr>" + "".join(f"<th>{c}</th>" for c in cab) + "</tr>" +
                              "".join("<tr>" + "".join(f"<td>{v}</td>" for v in l) + "</tr>" for l in linhas) +
                              "</table>", encoding="utf-8")
    escrever_xlsx(d / "e.xlsx", {"Plan1": [["Título qualquer"], [], cab] + linhas})
    with open(d / "f.txt", "w", newline="", encoding="cp1252") as f:
        csv.writer(f, delimiter="\t").writerows([cab] + [[a, b, str(c).replace(".", ",")] for a, b, c in linhas])
    tabelas, erros = ler_pasta(d)
    assert not erros, erros
    assert len(tabelas) == 6
    for t in tabelas:
        assert [c.lower() for c in t.cabecalho][:3] == ["data", "ramo", "prêmio"], (t.nome, t.cabecalho)
        assert t.n == 2, t.nome
        assert round(sum(para_numero(r[2]) for r in t.linhas), 2) == 300.75, t.nome


def test_saude_bate_com_calculo_independente():
    pasta = TMP / "saude"
    gerar_saude(pasta, random.Random(7))
    tabelas, _ = ler_pasta(pasta)
    por = defaultdict(list)
    for t in tabelas:
        tipo, _ = classificar(t)
        if tipo:
            por[tipo].append(t)
    assert set(por) == {"beneficiarios", "faturamento", "sinistros", "contratos"}
    d = motor.processar(dict(por), motor.Parametros())
    meses = d["meta"]["meses"]
    janela = set(meses[-12:])

    # --- independente: sinistros válidos da janela (lendo o CSV bruto)
    benef = {}
    with open(pasta / "cadastro_beneficiarios.csv", encoding="utf-8-sig") as f:
        for r in list(csv.DictReader(f, delimiter=";")):
            if r["Matrícula"] not in benef and para_data(r["Data de Nascimento"]):
                benef[r["Matrícula"]] = r
    sin_ind = 0.0
    with open(pasta / "utilizacao_sinistros.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            dt = para_data(r["Data do Atendimento"])
            if dt and f"{dt.year:04d}-{dt.month:02d}" in janela:
                sin_ind += _br(r["Valor Pago"])
    prem_ind = 0.0
    t_fat = por["faturamento"][0]
    for r in t_fat.linhas:
        c = para_competencia(r[0])
        if c and f"{c[0]:04d}-{c[1]:02d}" in janela:
            prem_ind += float(r[4])

    idx = [meses.index(m) for m in janela]
    sin_robo = sum(d["cubo"]["sinistro"][u][m] for u in range(len(d["unidades"])) for m in idx)
    prem_robo = sum(d["cubo"]["premio"][u][m] for u in range(len(d["unidades"])) for m in idx)
    assert abs(sin_robo - sin_ind) < 0.05, (sin_robo, sin_ind)
    assert abs(prem_robo - prem_ind) < 0.05, (prem_robo, prem_ind)
    # vidas do último mês: ativos no último dia, contados à mão
    from datetime import date, timedelta
    y, mo = map(int, meses[-1].split("-"))
    fim = date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
    ativos = sum(1 for r in benef.values() if para_data(r["Data de Inclusão"]) <= fim and
                 not (r["Data de Exclusão"] and para_data(r["Data de Exclusão"]) <= fim))
    vidas_robo = sum(d["cubo"]["vidas"][u][-1] for u in range(len(d["unidades"])))
    assert vidas_robo == ativos, (vidas_robo, ativos)
    # robô pegou os erros plantados
    regras = {r["regra"]: r for r in d["validacao"]}
    assert regras["IDs únicos"]["qtd"] == 1
    assert regras["Data de nascimento válida"]["qtd"] == 1
    assert regras["Data de atendimento válida"]["qtd"] == 1
    assert regras["Valores negativos (estornos/glosas)"]["qtd"] == 6
    assert regras["Sinistros após a exclusão"]["qtd"] >= 10
    assert d["meta"]["status"] == "APROVADO COM RESSALVAS"


def test_seguros_premio_ganho_fecha_com_emitido():
    pasta = TMP / "seg"
    gerar_seguradora(pasta, random.Random(3))
    tabelas, _ = ler_pasta(pasta)
    fato, regras, perfil = seguros.montar_fato(tabelas)
    assert fato is not None
    nomes = [m["nome"] for m in perfil["metricas"]]
    for m in ("Prêmio", "Sinistros", "Prêmio ganho", "Sinistralidade", "Frequência", "Severidade", "Ticket médio"):
        assert m in nomes, m
    i_p, i_g, i_s = (fato.cabecalho.index(c) for c in ("Prêmio", "Prêmio ganho", "Sinistros"))
    emitido = sum(r[i_p] or 0 for r in fato.linhas)
    ganho = sum(r[i_g] or 0 for r in fato.linhas)
    ppng = next(r for r in regras if r["regra"].startswith("Prêmio ganho"))["detalhe"]
    a_apropriar = float(ppng.split("R$ ")[2].split(" ")[0].replace(",", ""))
    assert abs(emitido - (ganho + a_apropriar)) < 1.0, (emitido, ganho, a_apropriar)
    # sinistros: soma do fato == soma do CSV bruto (o valor 'abc' fica fora)
    bruto = 0.0
    with open(pasta / "sinistros_avisados.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = para_numero(r["valor_indenizado"])
            bruto += v or 0
    assert abs(sum(r[i_s] or 0 for r in fato.linhas) - bruto) < 0.05
    tb = generico.montar_tabela(fato, perfil, {c: "categoria" for c in perfil["dims"]})
    assert tb["data"] == "Mês" and "Ramo" in tb["categorias"]


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("OK", nome)
