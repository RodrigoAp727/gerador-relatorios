"""Motor do robô: valida os dados, cruza as bases e monta o cubo de métricas.

Regras de ouro (o que torna o BI "100% concreto"):
  * Nenhum número é estimado, interpolado ou inventado. Se falta dado, a métrica
    aparece como "—" e o motivo vai para o relatório de validação.
  * Razões (sinistralidade, per capita, frequência) são sempre calculadas como
    soma ÷ soma no período — nunca média de percentuais.
  * Toda linha descartada é contada e explicada no relatório de validação.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .formatos import Tabela
from .leitura import (OBRIGATORIAS, OPCIONAIS, TIPOS, TIPOS_LABEL, mapear_colunas, norm,
                      para_competencia, para_data, para_numero, para_sexo, para_tipo,
                      para_titularidade)

FAIXAS = ["0-18", "19-23", "24-28", "29-33", "34-38", "39-43", "44-48", "49-53", "54-58", "59+"]
LIMITES_FAIXA = [18, 23, 28, 33, 38, 43, 48, 53, 58]  # faixas etárias ANS (RN 63/2003)

class ErroCritico(Exception):
    """Falha que impede gerar um BI confiável."""


@dataclass
class Parametros:
    corretora: str = "Corretora"
    limite_padrao: float = 0.75
    vcmh_padrao: float = 0.0
    idade_limite_dependente: int = 24
    tolerancia_vidas: float = 0.05
    valor_alerta_evento: float = 150_000.0


@dataclass
class Validador:
    regras: list[dict] = field(default_factory=list)

    def add(self, grupo, regra, status, qtd=0, detalhe="", amostra=None):
        self.regras.append({"grupo": grupo, "regra": regra, "status": status, "qtd": qtd,
                            "detalhe": detalhe, "amostra": (amostra or [])[:8]})

    def ok(self, grupo, regra, detalhe=""):
        self.add(grupo, regra, "OK", 0, detalhe)

    def checa(self, grupo, regra, problemas, status_se_erro, detalhe_ok, detalhe_erro, amostra=None):
        if problemas:
            self.add(grupo, regra, status_se_erro, problemas, detalhe_erro, amostra)
        else:
            self.ok(grupo, regra, detalhe_ok)

    @property
    def status_geral(self):
        st = {r["status"] for r in self.regras}
        if "ERRO" in st:
            return "REPROVADO"
        if "ALERTA" in st:
            return "APROVADO COM RESSALVAS"
        return "APROVADO"


def _ym(d) -> int:
    return d[0] * 12 + (d[1] - 1) if isinstance(d, tuple) else d.year * 12 + (d.month - 1)


def _ym_str(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _fim_mes(i: int) -> date:
    y, m = i // 12, i % 12 + 1
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _idade(nasc: date, em: date) -> int:
    return em.year - nasc.year - ((em.month, em.day) < (nasc.month, nasc.day))


def _faixa(idade: int) -> int:
    for i, lim in enumerate(LIMITES_FAIXA):
        if idade <= lim:
            return i
    return 9


def _carregar(tabelas: list[Tabela], tipo: str, val: Validador):
    """Junta todas as tabelas (arquivos/abas) do mesmo tipo em linhas canônicas."""
    linhas, mapa_total = [], {}
    for t in tabelas:
        mapa = mapear_colunas(t.cabecalho, OBRIGATORIAS[tipo] + OPCIONAIS[tipo])
        mapa_total.update(mapa)
        val.ok(f"Arquivo {tipo}", "Tabela reconhecida",
               f"{t.nome}: {t.n} linhas · colunas usadas: "
               + ", ".join(f"{t.cabecalho[i]}→{c}" for c, i in mapa.items()))
        for r in t.linhas:
            reg = {}
            for c, i in mapa.items():
                v = r[i] if i < len(r) else None
                reg[c] = v.strip() if isinstance(v, str) else v
            linhas.append(reg)
    return linhas, mapa_total


def processar(classificadas: dict[str, list[Tabela]], params: Parametros, val: Validador | None = None) -> dict:
    val = val or Validador()
    faltam = [t for t in ("beneficiarios", "faturamento", "sinistros") if not classificadas.get(t)]
    for t in faltam:
        val.add("Arquivos", f"Tabela de {t}", "ERRO", 1, f"Nenhuma tabela de {t} reconhecida")
    if faltam:
        raise ErroCritico("Tabelas obrigatórias ausentes", val)
    try:
        return _processar(classificadas, params, val)
    except ErroCritico as e:
        raise ErroCritico(str(e), val) from None


def _processar(arquivos, params: Parametros, val: Validador) -> dict:
    # ============================================================ BENEFICIÁRIOS
    rows, mapa_b = _carregar(arquivos["beneficiarios"], "beneficiarios", val)
    G = "Beneficiários"
    benef: dict[str, dict] = {}
    dup, nasc_inv, incl_inv, excl_antes, sexo_inv, sem_emp, tit_inv = [], [], [], [], [], [], []
    hoje = date.today()
    for i, r in enumerate(rows, start=2):
        bid = str(r.get("id_beneficiario") or "").strip()
        emp = str(r.get("empresa") or "").strip()
        if not bid or not emp:
            sem_emp.append(f"linha {i}")
            continue
        if bid in benef:
            dup.append(bid)
            continue
        nasc = para_data(r.get("data_nascimento"))
        if nasc is None or nasc > hoje or _idade(nasc, hoje) > 110:
            nasc_inv.append(f"{bid} ({r.get('data_nascimento')})")
            continue
        inc = para_data(r.get("data_inclusao"))
        if inc is None:
            incl_inv.append(f"{bid} ({r.get('data_inclusao')})")
            continue
        exc = para_data(r.get("data_exclusao")) if r.get("data_exclusao") else None
        if exc is not None and exc < inc:
            excl_antes.append(f"{bid} (inc {inc:%d/%m/%Y}, exc {exc:%d/%m/%Y})")
            continue
        sexo = para_sexo(r.get("sexo")) if "sexo" in mapa_b else None
        if "sexo" in mapa_b and sexo is None:
            sexo_inv.append(f"{bid} ({r.get('sexo')})")
        tit = para_titularidade(r.get("titularidade")) if "titularidade" in mapa_b else None
        if tit is None and "parentesco" in mapa_b:
            p = norm(r.get("parentesco"))
            tit = "T" if p.startswith("tit") or p == "proprio" else ("D" if p else None)
        if "titularidade" in mapa_b and tit is None:
            tit_inv.append(f"{bid} ({r.get('titularidade')})")
        benef[bid] = {"id": bid, "empresa": emp, "operadora": str(r.get("operadora") or "").strip(),
                      "plano": str(r.get("plano") or "").strip(), "nasc": nasc, "sexo": sexo,
                      "tit": tit, "parentesco": norm(r.get("parentesco")), "inc": inc, "exc": exc}

    val.checa(G, "Identificação e empresa preenchidas", len(sem_emp), "ALERTA",
              "Todas as linhas têm ID e empresa", "Linhas sem ID ou empresa foram descartadas", sem_emp)
    val.checa(G, "IDs únicos", len(dup), "ALERTA", "Nenhum ID duplicado",
              "IDs repetidos: mantida a 1ª ocorrência, demais descartadas", dup)
    val.checa(G, "Data de nascimento válida", len(nasc_inv), "ALERTA", "Todas válidas",
              "Datas vazias, futuras ou idade > 110 anos: linhas descartadas", nasc_inv)
    val.checa(G, "Data de inclusão válida", len(incl_inv), "ALERTA", "Todas válidas",
              "Inclusão vazia/ilegível: linhas descartadas", incl_inv)
    val.checa(G, "Exclusão posterior à inclusão", len(excl_antes), "ALERTA", "Todas coerentes",
              "Exclusão anterior à inclusão: linhas descartadas", excl_antes)
    if "sexo" in mapa_b:
        val.checa(G, "Sexo informado", len(sexo_inv), "ALERTA", "Todos M/F",
                  "Sexo não reconhecido: vidas contadas, mas fora da pirâmide etária", sexo_inv)
    else:
        val.add(G, "Sexo informado", "ALERTA", len(benef), "Coluna sexo ausente: pirâmide etária indisponível")
    if "titularidade" in mapa_b:
        val.checa(G, "Titularidade reconhecida", len(tit_inv), "ALERTA", "Todos titular/dependente",
                  "Titularidade não reconhecida", tit_inv)
    if not benef:
        raise ErroCritico("Nenhum beneficiário válido")
    descartados = len(rows) - len(benef)
    val.add(G, "Beneficiários aprovados", "OK", len(benef),
            f"{len(benef)} de {len(rows)} linhas aprovadas ({descartados} descartadas)")

    # ============================================================ FATURAMENTO
    rows, mapa_f = _carregar(arquivos["faturamento"], "faturamento", val)
    G = "Faturamento"
    fat: dict[tuple, dict] = defaultdict(lambda: {"premio": 0.0, "vidas": 0.0, "n": 0})
    comp_inv, premio_inv, premio_neg = [], [], []
    for i, r in enumerate(rows, start=2):
        comp = para_competencia(r.get("competencia"))
        emp = str(r.get("empresa") or "").strip()
        if comp is None or not emp:
            comp_inv.append(f"linha {i} ({r.get('competencia')})")
            continue
        pr = para_numero(r.get("premio"))
        if pr is None:
            premio_inv.append(f"linha {i} ({r.get('premio')})")
            continue
        if pr <= 0:
            premio_neg.append(f"{emp} {comp[1]:02d}/{comp[0]} = {pr:,.2f}")
        op = str(r.get("operadora") or "").strip()
        k = (emp, op, _ym(comp))
        fat[k]["premio"] += pr
        fat[k]["vidas"] += para_numero(r.get("vidas")) or 0
        fat[k]["n"] += 1
    multiplas = [f"{k[0]} {_ym_str(k[2])} ({v['n']} linhas)" for k, v in fat.items() if v["n"] > 1]
    val.checa(G, "Competência e empresa válidas", len(comp_inv), "ALERTA", "Todas válidas",
              "Linhas descartadas por competência/empresa ilegível", comp_inv)
    val.checa(G, "Prêmio numérico", len(premio_inv), "ALERTA", "Todos numéricos",
              "Linhas descartadas por prêmio ilegível", premio_inv)
    val.checa(G, "Prêmio positivo", len(premio_neg), "ALERTA", "Todos positivos",
              "Prêmios zerados/negativos (estornos?) mantidos — conferir", premio_neg)
    val.checa(G, "Uma fatura por empresa/competência", len(multiplas), "ALERTA",
              "Uma linha por empresa/competência", "Mais de uma linha: valores somados (faturas complementares?)",
              multiplas)
    if not fat:
        raise ErroCritico("Nenhuma linha de faturamento válida")

    # operadora da empresa: faturamento > contrato > cadastro
    emp_op: dict[str, str] = {}
    for (emp, op, _), _v in fat.items():
        if op:
            emp_op.setdefault(emp, op)

    # ============================================================ CONTRATOS
    contratos: dict[str, dict] = {}
    if arquivos.get("contratos"):
        rows, _ = _carregar(arquivos["contratos"], "contratos", val)
        G = "Contratos"
        inv = []
        for i, r in enumerate(rows, start=2):
            emp = str(r.get("empresa") or "").strip()
            if not emp:
                continue
            lim = para_numero(r.get("limite_sinistralidade"))
            if lim is not None and lim > 1.5:
                lim = lim / 100
            vc = para_numero(r.get("vcmh"))
            if vc is not None and vc > 1:
                vc = vc / 100
            mr = r.get("mes_renovacao")
            mes = None
            if mr not in (None, ""):
                n = para_numero(mr)
                if n is not None and 1 <= n <= 12:
                    mes = int(n)
                else:
                    c = para_competencia(mr)
                    s = norm(mr)[:3]
                    mes = c[1] if c else {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7,
                                          "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}.get(s)
                if mes is None:
                    inv.append(f"{emp}: mês de renovação '{mr}'")
            if lim is not None and not (0.3 <= lim <= 1.2):
                inv.append(f"{emp}: limite {lim:.0%} fora do intervalo 30%–120%")
                lim = None
            contratos[emp] = {"operadora": str(r.get("operadora") or "").strip(), "limite": lim,
                              "renov": mes, "vcmh": vc}
            if contratos[emp]["operadora"]:
                emp_op.setdefault(emp, contratos[emp]["operadora"])
        val.checa(G, "Parâmetros de contrato válidos", len(inv), "ALERTA", f"{len(contratos)} contratos lidos",
                  "Parâmetros inválidos ignorados (usado o padrão)", inv)
    else:
        val.add("Contratos", "Arquivo de contratos", "ALERTA", 1,
                f"Sem arquivo de contratos: limite padrão {params.limite_padrao:.0%} para todas as empresas e "
                "mês de renovação desconhecido")

    for b in benef.values():
        if not b["operadora"]:
            b["operadora"] = emp_op.get(b["empresa"], "")
        emp_op.setdefault(b["empresa"], b["operadora"])

    # ============================================================ PERÍODO
    comps = sorted({k[2] for k in fat})
    ini, fim = comps[0], comps[-1]
    meses = list(range(ini, fim + 1))
    buracos = [_ym_str(m) for m in meses if m not in set(comps)]
    val.checa("Faturamento", "Competências contínuas", len(buracos), "ALERTA",
              f"{_ym_str(ini)} a {_ym_str(fim)} sem lacunas", "Meses sem nenhum faturamento", buracos)
    NM = len(meses)
    midx = {m: i for i, m in enumerate(meses)}

    # unidades = (empresa, operadora)
    unidades: list[tuple[str, str]] = []
    uidx: dict[tuple[str, str], int] = {}

    def unidade(emp, op) -> int:
        op = op or emp_op.get(emp, "") or "Não informada"
        k = (emp, op)
        if k not in uidx:
            uidx[k] = len(unidades)
            unidades.append(k)
        return uidx[k]

    for (emp, op, _m) in fat:
        unidade(emp, op)
    for b in benef.values():
        b["u"] = unidade(b["empresa"], b["operadora"])

    def novo():
        return [[0.0] * NM for _ in range(len(unidades))]

    # ============================================================ SINISTROS
    rows, mapa_s = _carregar(arquivos["sinistros"], "sinistros", val)
    G = "Sinistros"
    sin_data_inv, sin_val_inv, sin_sem_benef, sin_fora, sin_neg, tipo_nr, alto = [], [], [], [], [], [], []
    pos_exc, pre_inc = [], []
    eventos = []  # (u, mi, tipo, valor, qtd, diarias, bid)
    sem_tipo = "tipo_evento" not in mapa_s
    for i, r in enumerate(rows, start=2):
        bid = str(r.get("id_beneficiario") or "").strip()
        d = para_data(r.get("data_atendimento"))
        if d is None:
            sin_data_inv.append(f"linha {i} ({r.get('data_atendimento')})")
            continue
        v = para_numero(r.get("valor"))
        if v is None:
            sin_val_inv.append(f"linha {i} ({r.get('valor')})")
            continue
        if v == 0:
            continue
        b = benef.get(bid)
        emp = str(r.get("empresa") or "").strip()
        if b is None and not emp:
            sin_sem_benef.append(f"{bid or 'linha ' + str(i)} R$ {v:,.2f}")
            continue
        if b is None:
            sin_sem_benef.append(f"{bid} ({emp}) R$ {v:,.2f} — mantido na empresa")
        mi = midx.get(_ym(d))
        if mi is None:
            sin_fora.append(f"{bid} {d:%d/%m/%Y} R$ {v:,.2f}")
            continue
        if v < 0:
            sin_neg.append(f"{bid} {d:%d/%m/%Y} R$ {v:,.2f}")
        tipo, reconhecido = ("outros", False) if sem_tipo else para_tipo(r.get("tipo_evento"))
        if not reconhecido and not sem_tipo:
            tipo_nr.append(str(r.get("tipo_evento")))
        if v >= params.valor_alerta_evento:
            alto.append(f"{bid} {d:%d/%m/%Y} {TIPOS_LABEL[tipo]} R$ {v:,.2f}")
        u = b["u"] if b else unidade(emp, "")
        qtd = para_numero(r.get("quantidade")) if "quantidade" in mapa_s else None
        qtd = 1.0 if qtd is None else qtd
        if v < 0:
            qtd = 0.0  # estorno não é evento
        dias = para_numero(r.get("dias_internacao")) or 0.0
        eventos.append((u, mi, tipo, v, qtd, dias, bid if b else None))
        if b and v > 0:
            if b["exc"] and d > b["exc"]:
                pos_exc.append({"id": bid, "u": u, "exc": b["exc"].isoformat(), "atend": d.isoformat(),
                                "valor": round(v, 2), "tipo": TIPOS_LABEL[tipo], "m": mi})
            elif d < b["inc"]:
                pre_inc.append({"id": bid, "u": u, "inc": b["inc"].isoformat(), "atend": d.isoformat(),
                                "valor": round(v, 2), "tipo": TIPOS_LABEL[tipo], "m": mi})

    val.checa(G, "Data de atendimento válida", len(sin_data_inv), "ALERTA", "Todas válidas",
              "Linhas descartadas por data ilegível", sin_data_inv)
    val.checa(G, "Valor numérico", len(sin_val_inv), "ALERTA", "Todos numéricos",
              "Linhas descartadas por valor ilegível", sin_val_inv)
    val.checa(G, "Beneficiário existe no cadastro", len(sin_sem_benef), "ALERTA",
              "Todos os sinistros cruzam com o cadastro",
              "Sinistros sem cadastro: mantidos na empresa quando informada, fora das análises por pessoa",
              sin_sem_benef)
    val.checa(G, "Atendimento dentro do período faturado", len(sin_fora), "ALERTA",
              "Todos dentro do período", "Fora do período faturado: excluídos (não há prêmio para comparar)",
              sin_fora)
    val.checa(G, "Valores negativos (estornos/glosas)", len(sin_neg), "ALERTA", "Nenhum valor negativo",
              "Mantidos como redução de custo", sin_neg)
    if sem_tipo:
        val.add(G, "Tipo de evento", "ALERTA", len(eventos),
                "Coluna de tipo ausente: análises de uso e internação indisponíveis")
    else:
        nr = sorted(set(tipo_nr))
        val.checa(G, "Tipo de evento reconhecido", len(tipo_nr), "ALERTA", "Todos classificados",
                  f"Classificados como 'Outros': {', '.join(nr[:10])}", nr)
    val.checa(G, f"Eventos ≥ R$ {params.valor_alerta_evento:,.0f}", len(alto), "INFO",
              "Nenhum evento acima do valor de corte", "Mantidos — conferir se não são lançamentos duplicados",
              sorted(alto, key=lambda s: -float(s.rsplit('R$ ', 1)[1].replace(',', '')))[:8])
    val.add(G, "Sinistros aprovados", "OK", len(eventos),
            f"{len(eventos)} de {len(rows)} linhas aprovadas · R$ {sum(e[3] for e in eventos):,.2f}")

    # ============================================================ CUBO
    nT = len(TIPOS)
    cubo = {k: novo() for k in ("vidas", "vidas_fat", "premio", "sinistro", "incl", "excl", "tit", "dep",
                                "v59", "soma_idade", "dias", "sem_uso", "pos_exc_v", "pos_exc_n")}
    cubo["tv"] = [[[0.0] * nT for _ in range(NM)] for _ in unidades]
    cubo["tq"] = [[[0.0] * nT for _ in range(NM)] for _ in unidades]
    cubo["pir"] = [[[0] * 20 for _ in range(NM)] for _ in unidades]

    for (emp, op, m), v in fat.items():
        u = unidade(emp, op)
        cubo["premio"][u][midx[m]] += v["premio"]
        cubo["vidas_fat"][u][midx[m]] += v["vidas"]

    ti = {t: i for i, t in enumerate(TIPOS)}
    custo_b = defaultdict(float)  # (bid, mi) -> valor
    int_b = set()
    for (u, mi, tipo, v, qtd, dias, bid) in eventos:
        cubo["sinistro"][u][mi] += v
        cubo["tv"][u][mi][ti[tipo]] += v
        cubo["tq"][u][mi][ti[tipo]] += qtd
        if tipo == "internacao":
            cubo["dias"][u][mi] += dias
        if bid:
            custo_b[(bid, mi)] += v
            if tipo == "internacao" and v > 0:
                int_b.add((bid, mi))
    for p in pos_exc:
        cubo["pos_exc_v"][p["u"]][p["m"]] += p["valor"]
        cubo["pos_exc_n"][p["u"]][p["m"]] += 1

    fins = [_fim_mes(m) for m in meses]
    usou = defaultdict(set)
    for (bid, mi), v in custo_b.items():
        if v > 0:
            usou[bid].add(mi)
    ben_list, bidx = [], {}
    for b in benef.values():
        u = b["u"]
        bidx[b["id"]] = len(ben_list)
        ben_list.append([b["id"], u, {"M": 0, "F": 1}.get(b["sexo"], 2),
                         b["nasc"].year * 12 + b["nasc"].month - 1])
        inc_ym = _ym(b["inc"])
        if ini <= inc_ym <= fim:
            cubo["incl"][u][midx[inc_ym]] += 1
        if b["exc"]:
            exc_ym = _ym(b["exc"])
            if ini <= exc_ym <= fim:
                cubo["excl"][u][midx[exc_ym]] += 1
        meses_uso = usou.get(b["id"], set())
        for mi, fm in enumerate(fins):
            if b["inc"] > fm or (b["exc"] is not None and b["exc"] <= fm):
                continue
            idade = _idade(b["nasc"], fm)
            f = _faixa(idade)
            cubo["vidas"][u][mi] += 1
            cubo["soma_idade"][u][mi] += idade
            if f == 9:
                cubo["v59"][u][mi] += 1
            if b["tit"] == "T":
                cubo["tit"][u][mi] += 1
            elif b["tit"] == "D":
                cubo["dep"][u][mi] += 1
            if b["sexo"] in ("M", "F"):
                cubo["pir"][u][mi][(0 if b["sexo"] == "M" else 10) + f] += 1
            if mi >= 11 and not any(k in meses_uso for k in range(mi - 11, mi + 1)):
                cubo["sem_uso"][u][mi] += 1

    custo_lista = [[bidx[bid], mi, round(v, 2), 1 if (bid, mi) in int_b else 0]
                   for (bid, mi), v in custo_b.items()]

    # vidas cadastro x faturadas
    diverg = []
    if "vidas" in mapa_f:
        for u in range(len(unidades)):
            for mi in range(NM):
                vf, vc = cubo["vidas_fat"][u][mi], cubo["vidas"][u][mi]
                if vf > 0 and abs(vc - vf) / vf > params.tolerancia_vidas:
                    diverg.append(f"{unidades[u][0]} {_ym_str(meses[mi])}: cadastro {vc:.0f} × fatura {vf:.0f}")
        val.checa("Cruzamento", f"Vidas cadastro × faturadas (tolerância {params.tolerancia_vidas:.0%})",
                  len(diverg), "ALERTA", "Cadastro bate com a fatura em todas as competências",
                  "Divergências — o BI usa as vidas do cadastro", diverg)
    sem_premio = []
    for u in range(len(unidades)):
        for mi in range(NM):
            if cubo["sinistro"][u][mi] > 0 and cubo["premio"][u][mi] <= 0:
                sem_premio.append(f"{unidades[u][0]} {_ym_str(meses[mi])}: sinistro R$ "
                                  f"{cubo['sinistro'][u][mi]:,.2f} sem prêmio")
    val.checa("Cruzamento", "Sinistro com prêmio correspondente", len(sem_premio), "ALERTA",
              "Toda competência com sinistro tem prêmio", "Sinistralidade dessas competências fica distorcida",
              sem_premio)
    sem_contrato = sorted({e for e, _ in unidades if e not in contratos})
    if contratos:
        val.checa("Cruzamento", "Empresa com contrato cadastrado", len(sem_contrato), "ALERTA",
                  "Todas as empresas têm contrato", f"Usado limite padrão {params.limite_padrao:.0%}", sem_contrato)
    val.checa("Elegibilidade", "Sinistros após a exclusão", len(pos_exc), "ALERTA",
              "Nenhum atendimento após exclusão",
              f"R$ {sum(p['valor'] for p in pos_exc):,.2f} em atendimentos após a data de exclusão — contestar",
              [f"{p['id']} exc {p['exc']} atend {p['atend']} R$ {p['valor']:,.2f}" for p in pos_exc])
    val.checa("Elegibilidade", "Sinistros antes da inclusão", len(pre_inc), "ALERTA",
              "Nenhum atendimento antes da inclusão",
              f"R$ {sum(p['valor'] for p in pre_inc):,.2f} em atendimentos antes da data de inclusão",
              [f"{p['id']} inc {p['inc']} atend {p['atend']} R$ {p['valor']:,.2f}" for p in pre_inc])

    # dependentes acima da idade limite (filhos) na última competência
    dep_acima = []
    fm = fins[-1]
    for b in benef.values():
        if b["inc"] > fm or (b["exc"] is not None and b["exc"] <= fm):
            continue
        filho = any(k in b["parentesco"] for k in ("filh", "enteado", "enteada")) or \
            (not b["parentesco"] and b["tit"] == "D")
        if b["tit"] != "T" and filho:
            idade = _idade(b["nasc"], fm)
            if idade > params.idade_limite_dependente and (b["parentesco"] or b["tit"] == "D"):
                if not b["parentesco"] and idade < 30:
                    pass  # sem parentesco: só sinaliza a partir de 30 para evitar falso positivo (cônjuge)
                if b["parentesco"] or idade >= 30:
                    dep_acima.append({"id": b["id"], "u": b["u"], "idade": idade,
                                      "parentesco": b["parentesco"] or "dependente (não informado)",
                                      "inc": b["inc"].isoformat()})
    dep_acima.sort(key=lambda d: -d["idade"])

    unid_out = []
    for (emp, op) in unidades:
        c = contratos.get(emp, {})
        unid_out.append({"empresa": emp, "operadora": op,
                         "limite": c.get("limite") or params.limite_padrao,
                         "limite_padrao": c.get("limite") is None,
                         "renov": c.get("renov"),
                         "vcmh": c.get("vcmh") if c.get("vcmh") is not None else params.vcmh_padrao})

    def arred(x):
        if isinstance(x, list):
            return [arred(y) for y in x]
        if isinstance(x, float):
            return round(x, 2) if not x.is_integer() else int(x)
        return x

    return {
        "meta": {"corretora": params.corretora, "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                 "meses": [_ym_str(m) for m in meses], "status": val.status_geral,
                 "idade_limite_dependente": params.idade_limite_dependente,
                 "tem_vidas_fat": "vidas" in mapa_f, "tem_tipo": not sem_tipo,
                 "tem_sexo": "sexo" in mapa_b, "tem_titularidade": "titularidade" in mapa_b or "parentesco" in mapa_b},
        "tipos": TIPOS, "tipos_label": [TIPOS_LABEL[t] for t in TIPOS], "faixas": FAIXAS,
        "unidades": unid_out,
        "cubo": {k: arred(v) for k, v in cubo.items()},
        "ben": ben_list,
        "custo": custo_lista,
        "eleg": {"dep_acima": dep_acima, "pos_exc": pos_exc, "pre_inc": pre_inc},
        "validacao": val.regras,
    }
