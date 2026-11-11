"""BI automático para QUALQUER tabela, de qualquer área.

O robô olha o conteúdo de cada coluna e decide, por regra explícita, o papel dela:
  data       ≥ 80% das células preenchidas viram data/competência
  numero     ≥ 90% viram número (e não é código/ID)
  categoria  texto com poucos valores distintos (dá para agrupar)
  id         código/identificador (quase um valor por linha, ou nome tipo id/cpf/cnpj/cep)
  texto      texto livre (só aparece na aba de dados)

Nada é estimado: as métricas são somas, contagens, médias e percentis dos
valores que existem. Células que não puderam ser lidas são contadas no
relatório de qualidade e ficam fora dos cálculos.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime

from .formatos import Tabela
from .leitura import norm, para_competencia, para_data, para_numero

MAX_LINHAS = 250_000
MAX_CATEGORIAS = 400

_HINT_ID = re.compile(r"(^|_)(id|cod|codigo|cpf|cnpj|cep|rg|telefone|fone|celular|matricula|carteirinha|"
                      r"chave|uuid|nf|nota|pedido|protocolo|documento|registro|numero|n|num|seq)($|_)")
_HINT_METRICA = ["valor", "total", "receita", "faturamento", "venda", "custo", "preco", "lucro", "margem",
                 "despesa", "saldo", "premio", "sinistro", "pago", "montante", "quantidade", "qtd", "volume",
                 "peso", "horas", "salario"]
_HINT_DATA = ["data", "dt", "dia", "competencia", "mes", "periodo", "emissao", "vencimento", "criado", "inicio"]


def _vazio(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _como_data(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        return None  # número puro não é data (evita confundir valores com seriais)
    d = para_data(v)
    if d:
        return d
    c = para_competencia(v)
    if c and not re.fullmatch(r"\d{4}", str(v).strip()):
        return date(c[0], c[1], 1)
    return None


def _como_numero(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, (date, datetime)):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", s) or re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", s):
        return None
    return para_numero(s)


def perfilar(t: Tabela) -> list[dict]:
    n = t.n
    perfis = []
    for j, nome in enumerate(t.cabecalho):
        vals = [r[j] if j < len(r) else None for r in t.linhas]
        cheios = [v for v in vals if not _vazio(v)]
        nc = len(cheios)
        p = {"nome": nome, "chave": norm(nome), "preenchidos": nc, "vazios": n - nc, "invalidos": 0,
             "distintos": len({str(v).strip().lower() for v in cheios})}
        if nc == 0:
            p["tipo"] = "vazia"
            perfis.append(p)
            continue
        amostra = cheios if nc <= 5000 else cheios[:: max(1, nc // 5000)]
        n_data = sum(_como_data(v) is not None for v in amostra)
        n_num = sum(_como_numero(v) is not None for v in amostra)
        k = p["chave"]
        dica_data = any(h in k for h in _HINT_DATA)
        dica_id = bool(_HINT_ID.search(k))
        if n_data >= 0.8 * len(amostra) or (dica_data and n_data >= 0.5 * len(amostra)):
            p["tipo"] = "data"
            p["invalidos"] = sum(_como_data(v) is None for v in cheios)
        elif n_num >= 0.9 * len(amostra):
            inteiros = all(float(x).is_integer() for x in (_como_numero(v) for v in amostra) if x is not None)
            if dica_id and not any(h in k for h in _HINT_METRICA) and inteiros:
                p["tipo"] = "id"
            elif inteiros and p["distintos"] <= 12 and n > 50 and not any(h in k for h in _HINT_METRICA):
                p["tipo"] = "categoria"  # ex.: ano, nota 1-5, trimestre
            else:
                p["tipo"] = "numero"
                p["invalidos"] = sum(_como_numero(v) is None for v in cheios)
        else:
            d = p["distintos"]
            if dica_id and d > 0.5 * nc:
                p["tipo"] = "id"
            elif d <= MAX_CATEGORIAS and (d <= 30 or d <= 0.5 * nc):
                p["tipo"] = "categoria"
            elif d > 0.9 * nc:
                p["tipo"] = "id" if max(len(str(v)) for v in amostra) <= 40 else "texto"
            else:
                p["tipo"] = "categoria" if d <= 2000 else "texto"
        perfis.append(p)
    return perfis


def _escolher_metricas(perfis):
    nums = [p for p in perfis if p["tipo"] == "numero"]

    def peso(p):
        k = p["chave"]
        dica = next((i for i, h in enumerate(_HINT_METRICA) if h in k), len(_HINT_METRICA))
        return (dica, -p["preenchidos"])

    return sorted(nums, key=peso)


def _escolher_data(perfis):
    datas = [p for p in perfis if p["tipo"] == "data"]
    if not datas:
        return None

    def peso(p):
        k = p["chave"]
        ruim = any(h in k for h in ("nasc", "aniversario", "validade", "vencimento_cnh", "admissao_anterior"))
        return (2 if ruim else 0 if any(h in k for h in ("competencia", "emissao", "venda", "atendimento", "ocorrencia",
                                                         "inclusao", "data")) else 1,
                -p["preenchidos"])

    return sorted(datas, key=peso)[0]


_DINHEIRO = ["valor", "receita", "faturamento", "venda", "custo", "preco", "lucro", "despesa", "saldo", "premio",
             "sinistro", "pago", "montante", "salario", "comissao", "indeniz", "brl", "reais", "desconto", "frete",
             "imposto", "juros", "multa", "tarifa", "ticket", "orcamento", "investimento", "pagar", "receber"]


def _fmt_coluna(chave: str) -> str:
    return "brl" if any(re.search(rf"(^|_){h}", chave) for h in _DINHEIRO) else "num"


def montar_tabela(t: Tabela, perfil: dict | None = None, tipos: dict | None = None) -> dict:
    truncada = t.n > MAX_LINHAS
    linhas = t.linhas[:MAX_LINHAS]
    perfis = perfilar(Tabela(t.arquivo, t.nome, t.cabecalho, linhas))
    for p in perfis:
        if tipos and p["nome"] in tipos:
            p["tipo"] = tipos[p["nome"]]
    regras = []

    def regra(regra_txt, status, qtd, detalhe, amostra=None):
        regras.append({"grupo": t.nome, "regra": regra_txt, "status": status, "qtd": qtd,
                       "detalhe": detalhe, "amostra": (amostra or [])[:8]})

    regra("Leitura", "OK", t.n, f"{t.n} linhas × {len(t.cabecalho)} colunas" +
          (f" — analisadas as primeiras {MAX_LINHAS}" if truncada else ""))
    if truncada:
        regra("Tamanho", "ALERTA", t.n - MAX_LINHAS, f"Tabela acima de {MAX_LINHAS} linhas: excedente não analisado")

    colunas = []
    idx_col = {p["nome"]: j for j, p in enumerate(perfis)}
    for p in perfis:
        j = idx_col[p["nome"]]
        bruto = [r[j] if j < len(r) else None for r in linhas]
        col = {k: p[k] for k in ("nome", "tipo", "preenchidos", "vazios", "invalidos", "distintos")}
        if p["tipo"] == "data":
            ds = [_como_data(v) if not _vazio(v) else None for v in bruto]
            col["v"] = [d.isoformat() if d else None for d in ds]
            ruins = [str(v) for v, d in zip(bruto, ds) if d is None and not _vazio(v)]
            if ruins:
                regra(f"Coluna '{p['nome']}' (data)", "ALERTA", len(ruins),
                      "Valores que não são data: ficaram fora da linha do tempo", ruins)
        elif p["tipo"] == "numero":
            ns = [_como_numero(v) if not _vazio(v) else None for v in bruto]
            col["v"] = [round(x, 6) if x is not None else None for x in ns]
            ruins = [str(v) for v, x in zip(bruto, ns) if x is None and not _vazio(v)]
            if ruins:
                regra(f"Coluna '{p['nome']}' (número)", "ALERTA", len(ruins),
                      "Valores não numéricos: ignorados nas somas", ruins)
            validos = sorted(x for x in ns if x is not None)
            if len(validos) >= 20 and p["nome"] != "Prêmio ganho":
                q1, q3 = validos[len(validos) // 4], validos[3 * len(validos) // 4]
                iqr = q3 - q1
                if iqr > 0:
                    lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
                    out = [x for x in validos if x < lo or x > hi]
                    if out:
                        regra(f"Coluna '{p['nome']}' (outliers)", "INFO", len(out),
                              f"Valores fora de [{lo:,.2f}; {hi:,.2f}] (3×IQR) — mantidos, conferir",
                              [f"{x:,.2f}" for x in sorted(out, key=abs, reverse=True)])
            neg = sum(1 for x in validos if x < 0)
            if neg:
                regra(f"Coluna '{p['nome']}' (negativos)", "INFO", neg, "Valores negativos mantidos (estornos?)")
        elif p["tipo"] == "categoria":
            textos = [None if _vazio(v) else (v.strftime("%d/%m/%Y") if isinstance(v, (date, datetime))
                                              else str(v).strip()) for v in bruto]
            cont = Counter(x for x in textos if x is not None)
            # unifica variações de caixa/acento/espaço ("São Paulo" × "sao paulo ")
            canon = {}
            for valor, _q in cont.most_common():
                canon.setdefault(norm(valor), valor)
            unificados = [v for v in cont if canon[norm(v)] != v]
            if unificados:
                regra(f"Coluna '{p['nome']}' (grafias)", "ALERTA", len(unificados),
                      "Variações de maiúscula/acento/espaço unificadas",
                      [f"'{v}' → '{canon[norm(v)]}'" for v in unificados])
            dic = sorted({canon[norm(x)] for x in cont}, key=lambda s: s.lower())
            pos = {v: i for i, v in enumerate(dic)}
            col["dic"] = dic
            col["v"] = [pos[canon[norm(x)]] if x is not None else -1 for x in textos]
        else:
            col["v"] = [None if _vazio(v) else (v.isoformat() if isinstance(v, (date, datetime)) else str(v))
                        for v in bruto]
        colunas.append(col)

    # duplicidades e linhas vazias
    chaves = Counter(tuple(str(x) for x in r) for r in linhas)
    dups = sum(q - 1 for q in chaves.values() if q > 1)
    if dups:
        regra("Linhas duplicadas", "ALERTA", dups, "Linhas idênticas encontradas — mantidas (podem ser lançamentos "
              "legítimos repetidos); conferir na origem")
    else:
        regra("Linhas duplicadas", "OK", 0, "Nenhuma linha 100% repetida")
    vazias_altas = [p["nome"] for p in perfis if p["preenchidos"] and p["vazios"] / max(1, len(linhas)) > 0.3]
    if vazias_altas and not (perfil and perfil.get("tipo") == "seguros"):
        regra("Preenchimento", "ALERTA", len(vazias_altas), "Colunas com mais de 30% de células vazias",
              vazias_altas)
    ids = [p for p in perfis if p["tipo"] == "id"]
    for p in ids:
        j = idx_col[p["nome"]]
        rep = len(linhas) - p["distintos"] - p["vazios"]
        if p["distintos"] > 0.98 * p["preenchidos"] and rep > 0:
            regra(f"Coluna '{p['nome']}' (ID)", "ALERTA", rep, "Identificador quase único com repetições")

    metricas = _escolher_metricas(perfis)
    data = _escolher_data(perfis)
    specs = list((perfil or {}).get("metricas", []))
    nomes = {m["nome"] for m in specs}
    for m in metricas:
        if m["nome"] in nomes or (perfil and perfil.get("tipo") == "seguros" and m["nome"] in ("Qtd. sinistros",)):
            continue
        specs.append({"nome": m["nome"], "fmt": _fmt_coluna(m["chave"]), "num": {"op": "sum", "col": m["nome"]}})
    specs.append({"nome": "Registros", "fmt": "int", "num": {"op": "count"}})
    for m in metricas[:6]:
        if not perfil or m["nome"] not in nomes:
            fm = _fmt_coluna(m["chave"])
            specs.append({"nome": f"Média de {m['nome']}", "fmt": "brl" if fm == "brl" else "num",
                          "num": {"op": "avg", "col": m["nome"]}})

    # mês incompleto no fim da série (ex.: dados só até dia 02 do último mês)
    parcial = (perfil or {}).get("parcial")
    if data and not parcial:
        datas = [x for x in next(c for c in colunas if c["nome"] == data["nome"])["v"] if x]
        if datas:
            ult = max(datas)[:10]
            mesmo_mes = {d[:10] for d in datas if d[:7] == ult[:7]}
            if len(ult) == 10 and any(d[8:10] != "01" for d in mesmo_mes):
                y, mo, dia = int(ult[:4]), int(ult[5:7]), int(ult[8:10])
                ultimo_dia = (date(y + (mo == 12), mo % 12 + 1, 1) - date(y, mo, 1)).days
                if dia <= ultimo_dia * 0.8:  # tolera bases que fecham no dia 28/dia útil
                    parcial = {"mes": ult[:7], "ate": ult}
    if parcial:
        regra("Último mês incompleto", "INFO", 1,
              f"Dados de {parcial['mes']} vão só até {parcial['ate']}: o mês fica fora do período padrão para não "
              "ser comparado com meses fechados (pode ser incluído no filtro 'Até')")
    return {
        "parcial": parcial,
        "nome": t.nome, "arquivo": t.arquivo, "n": len(linhas), "n_original": t.n,
        "colunas": colunas, "specs": specs, "perfil": perfil or {"tipo": "generico"},
        "metricas": [m["nome"] for m in metricas],
        "data": data["nome"] if data else None,
        "categorias": sorted([p["nome"] for p in perfis if p["tipo"] == "categoria"],
                             key=lambda nm: next(p["distintos"] for p in perfis if p["nome"] == nm)),
        "validacao": regras,
    }


def montar(tabelas: list[Tabela], titulo: str, erros_leitura: list[str], extras: list[dict] | None = None,
           regras_extra: list[dict] | None = None) -> dict:
    out = []
    regras_gerais = []
    for e in erros_leitura:
        regras_gerais.append({"grupo": "Leitura de arquivos", "regra": "Arquivo não lido", "status": "ERRO",
                              "qtd": 1, "detalhe": e, "amostra": []})
    for t in tabelas:
        if t.n == 0:
            continue
        out.append(montar_tabela(t))
    out.sort(key=lambda x: -x["n"] * (1 + len(x["metricas"])))
    out = (extras or []) + out
    return {"meta": {"titulo": titulo, "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M")},
            "tabelas": out, "validacao_geral": regras_gerais + (regras_extra or [])}
