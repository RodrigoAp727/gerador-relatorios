"""Camada de seguros multirramo (vida, auto, residencial, empresarial, grandes riscos,
saúde, rural, transportes, garantia, RC, previdência...).

O robô reconhece as colunas típicas do mercado segurador em QUALQUER tabela,
junta produção (prêmios) e sinistros — mesmo vindos de arquivos diferentes —
numa única tabela-fato padronizada e entrega ao motor de BI as métricas do
setor, todas calculadas como soma ÷ soma:

  Sinistralidade = Σ sinistros ÷ Σ prêmios
  Frequência     = nº de sinistros ÷ nº de apólices
  Severidade     = Σ sinistros ÷ nº de sinistros  (custo médio do sinistro)
  Ticket médio   = Σ prêmios ÷ nº de apólices
  Comissão %     = Σ comissões ÷ Σ prêmios
  Taxa média     = Σ prêmios ÷ Σ importância segurada
  Tempo de aviso = média de (data do aviso − data da ocorrência), em dias

O cruzamento entre produção e sinistros acontece pelas dimensões comuns
(ramo, produto, UF, canal, corretor...) e pelo mês. Se uma dimensão só existe
de um dos lados, o robô avisa no relatório de validação.
"""
from __future__ import annotations

from datetime import date, datetime

from .formatos import Tabela
from .generico import _como_data, _como_numero, _vazio
from .leitura import norm

# (canônico, rótulo, padrões que a coluna normalizada contém, padrões que excluem)
DIMENSOES = [
    ("ramo", "Ramo", ["ramo", "linha_negocio", "linha_de_negocio", "lob", "carteira", "modalidade", "segmento_seguro"], ["cod_ramo_susep"]),
    ("produto", "Produto", ["produto", "plano", "familia_produto"], []),
    ("cobertura", "Cobertura", ["cobertura", "garantia"], ["is_", "importancia", "lmi", "valor"]),
    ("segmento", "Segmento", ["segmento", "mercado", "unidade_negocio", "tipo_cliente", "tipo_pessoa", "porte"], []),
    ("canal", "Canal", ["canal", "origem_venda", "meio_venda"], []),
    ("corretor", "Corretor", ["corretor", "corretora", "intermediario", "produtor", "parceiro", "agente"], ["comiss", "cod_"]),
    ("sucursal", "Sucursal", ["sucursal", "filial", "regional", "diretoria", "agencia"], []),
    ("seguradora", "Seguradora", ["seguradora", "cia", "companhia", "operadora"], []),
    ("uf", "UF", ["uf", "estado", "uf_risco", "uf_segurado"], []),
    ("regiao", "Região", ["regiao"], ["regional"]),
    ("cidade", "Cidade", ["cidade", "municipio"], []),
    ("causa", "Causa", ["causa", "natureza_sinistro", "evento", "motivo", "tipo_sinistro", "tipo_de_sinistro"], ["data", "dt_"]),
    ("status_sinistro", "Situação do sinistro", ["status_sinistro", "situacao_sinistro", "status", "situacao", "fase"], ["apolice"]),
    ("veiculo", "Veículo/Categoria", ["categoria_veiculo", "marca", "modelo", "tipo_veiculo", "categoria_tarifaria"], []),
    ("sexo", "Sexo", ["sexo", "genero"], []),
    ("faixa_etaria", "Faixa etária", ["faixa_etaria", "faixa_idade"], []),
]
MEDIDAS = [
    ("premio", "Prêmio", ["premio_emitido", "premio_liquido", "premio_total", "premio_ganho", "premio_direto", "premio",
                          "valor_premio", "receita_premio"], ["comiss", "taxa", "aliquota", "sinistr", "qtd", "cod"]),
    ("sinistro", "Sinistros", ["sinistro_retido", "sinistro_ocorrido", "sinistro_pago", "sinistros", "valor_sinistro",
                               "indeniz", "sinistro_valor", "valor_indenizado", "valor_pago", "valor_aviso",
                               "sinistro"], ["qtd", "quant", "numero", "num_", "n_", "cod", "id", "data", "dt_", "status",
                                             "situacao", "causa", "tipo", "aviso_data"]),
    ("comissao", "Comissão", ["comissao", "comissoes", "corretagem"], ["percent", "pct", "taxa", "aliquota"]),
    ("is", "Importância segurada", ["importancia_segurada", "lmi", "capital_segurado", "valor_em_risco", "is_total",
                                    "limite_maximo", "capital"], []),
    ("reserva", "Reserva (PSL)", ["reserva", "provisao", "psl", "ibnr"], []),
    ("despesa", "Despesas", ["despesa", "custo_aquisicao", "despesas_administrativas"], []),
    ("salvado", "Salvados/ressarcimentos", ["salvado", "ressarc", "recuper"], []),
    ("qtd_itens", "Itens/vidas seguradas", ["qtd_itens", "itens", "vidas", "qtd_vidas", "segurados", "qtd_veiculos"], []),
]
IDS = [
    ("apolice", "Apólice", ["apolice", "certificado", "contrato", "numero_apolice", "policy"], ["data", "dt_", "status"]),
    ("num_sinistro", "Nº sinistro", ["numero_sinistro", "num_sinistro", "n_sinistro", "sinistro_id", "id_sinistro",
                                    "cod_sinistro", "codigo_sinistro", "aviso", "protocolo"], ["data", "dt_", "valor"]),
]
DATAS = [
    ("competencia", ["competencia", "mes_referencia", "mes_ref", "referencia", "periodo"]),
    ("data_emissao", ["emissao", "data_emissao", "dt_emissao"]),
    ("inicio_vigencia", ["inicio_vigencia", "vigencia_inicio", "inicio_vig", "inicio_de_vigencia", "dt_inicio", "data_inicio"]),
    ("fim_vigencia", ["fim_vigencia", "vigencia_fim", "fim_vig", "fim_de_vigencia", "termino_vigencia", "final_vigencia",
                      "dt_fim", "data_fim", "vencimento_apolice"]),
    ("data_ocorrencia", ["ocorrencia", "data_sinistro", "dt_sinistro", "data_evento", "data_do_sinistro"]),
    ("data_aviso", ["data_aviso", "dt_aviso", "aviso_data", "data_comunicacao", "data_abertura", "data_notificacao"]),
    ("data_pagamento", ["data_pagamento", "dt_pagamento", "data_liquidacao", "pagamento"]),
]


def _match(chave: str, padroes, excl) -> bool:
    if any(e in chave for e in excl):
        return False
    for p in padroes:
        if chave == p or chave.startswith(p + "_") or chave.endswith("_" + p) or f"_{p}_" in f"_{chave}_" \
                or (len(p) >= 5 and p in chave):
            return True
    return False


def mapear(t: Tabela) -> dict:
    """Retorna {canônico: índice da coluna} para a tabela."""
    chaves = [norm(c) for c in t.cabecalho]
    usados, mapa = set(), {}

    def tentar(lista, com_excl=True):
        for item in lista:
            can, padroes = item[0], item[2] if len(item) == 4 else item[1]
            excl = item[3] if len(item) == 4 else []
            if can in mapa:
                continue
            for j, k in enumerate(chaves):
                if j not in usados and _match(k, padroes, excl if com_excl else []):
                    mapa[can] = j
                    usados.add(j)
                    break

    tentar(DATAS)
    tentar(IDS)
    tentar(MEDIDAS)
    tentar(DIMENSOES)
    # confirma tipos pelo conteúdo: medida tem que ser numérica, data tem que ser data
    for can in list(mapa):
        j = mapa[can]
        amostra = [r[j] for r in t.linhas[:400] if j < len(r) and not _vazio(r[j])]
        if not amostra:
            del mapa[can]
            continue
        if can in {m[0] for m in MEDIDAS}:
            if sum(_como_numero(v) is not None for v in amostra) < 0.9 * len(amostra):
                del mapa[can]
        elif can in {d[0] for d in DATAS}:
            if sum(_como_data(v) is not None for v in amostra) < 0.8 * len(amostra):
                del mapa[can]
    return mapa


def eh_tabela_seguro(mapa: dict) -> str | None:
    tem_p, tem_s = "premio" in mapa, "sinistro" in mapa
    contexto = any(k in mapa for k in ("ramo", "apolice", "num_sinistro", "data_ocorrencia", "produto",
                                       "is", "comissao", "corretor", "cobertura"))
    if not contexto:
        return None
    if tem_p and tem_s:
        return "misto"
    if tem_p:
        return "producao"
    if tem_s or "num_sinistro" in mapa or "data_ocorrencia" in mapa:
        return "sinistros"
    return None


def montar_fato(tabelas: list[Tabela]) -> tuple[Tabela | None, list[dict], dict]:
    """Une as tabelas de seguros numa tabela-fato padronizada."""
    regras, usadas = [], []
    for t in tabelas:
        m = mapear(t)
        papel = eh_tabela_seguro(m)
        if papel:
            usadas.append((t, m, papel))
    if not usadas:
        return None, regras, {}

    dims_por_papel: dict[str, set] = {"producao": set(), "sinistros": set()}
    dims_presentes = []
    for t, m, papel in usadas:
        rot = {**{d[0]: d[1] for d in DIMENSOES}, **{x[0]: x[1] for x in MEDIDAS}, **{x[0]: x[1] for x in IDS}}
        regras.append({"grupo": "Seguros", "regra": f"Tabela de {papel}", "status": "OK", "qtd": t.n,
                       "detalhe": f"{t.nome}: " + ", ".join(f"{t.cabecalho[j]}→{rot.get(c, c)}" for c, j in m.items()),
                       "amostra": []})
        for d in DIMENSOES:
            if d[0] in m:
                if d[0] not in dims_presentes:
                    dims_presentes.append(d[0])
                for p in (("producao", "sinistros") if papel == "misto" else (papel,)):
                    dims_por_papel[p].add(d[0])

    tem_p = any(p in ("producao", "misto") for _, _, p in usadas)
    tem_s = any(p in ("sinistros", "misto") for _, _, p in usadas)
    if tem_p and tem_s:
        so_um = [d for d in dims_presentes if (d in dims_por_papel["producao"]) != (d in dims_por_papel["sinistros"])]
        rot = {d[0]: d[1] for d in DIMENSOES}
        if so_um:
            regras.append({"grupo": "Seguros", "regra": "Dimensões comuns a prêmios e sinistros", "status": "ALERTA",
                           "qtd": len(so_um),
                           "detalhe": "Só existem de um lado (sinistralidade por elas fica incompleta): "
                                      + ", ".join(rot[d] for d in so_um), "amostra": []})
        else:
            regras.append({"grupo": "Seguros", "regra": "Dimensões comuns a prêmios e sinistros", "status": "OK",
                           "qtd": 0, "detalhe": "Prêmios e sinistros cruzam por: " + ", ".join(rot[d] for d in dims_presentes),
                           "amostra": []})
    elif not tem_p:
        regras.append({"grupo": "Seguros", "regra": "Tabela de prêmios", "status": "ALERTA", "qtd": 1,
                       "detalhe": "Nenhuma tabela de prêmios: sinistralidade, ticket e comissão indisponíveis", "amostra": []})
    elif not tem_s:
        regras.append({"grupo": "Seguros", "regra": "Tabela de sinistros", "status": "ALERTA", "qtd": 1,
                       "detalhe": "Nenhuma tabela de sinistros: sinistralidade, frequência e severidade indisponíveis",
                       "amostra": []})

    med_presentes = [x[0] for x in MEDIDAS if any(x[0] in m for _, m, _ in usadas)]
    id_presentes = [x[0] for x in IDS if any(x[0] in m for _, m, _ in usadas)]
    rot_dim = {d[0]: d[1] for d in DIMENSOES}
    rot_med = {x[0]: x[1] for x in MEDIDAS}
    rot_id = {x[0]: x[1] for x in IDS}
    tem_lag = any("data_aviso" in m and "data_ocorrencia" in m for _, m, _ in usadas)
    tem_qtd_s = tem_s

    cab = ["Mês"] + [rot_dim[d] for d in dims_presentes] + [rot_med[x] for x in med_presentes] + \
          [rot_id[x] for x in id_presentes] + (["Qtd. sinistros"] if tem_qtd_s else []) + \
          (["Dias até o aviso"] if tem_lag else []) + ["Origem"]
    linhas, sem_data, lag_neg = [], 0, 0
    for t, m, papel in usadas:
        ordem_data = (["data_ocorrencia", "data_aviso", "competencia", "data_pagamento", "data_emissao", "inicio_vigencia"]
                      if papel == "sinistros" else
                      ["competencia", "data_emissao", "inicio_vigencia", "data_ocorrencia", "data_aviso", "data_pagamento"])
        col_data = next((c for c in ordem_data if c in m), None)
        for r in t.linhas:
            def g(c):
                j = m.get(c)
                return r[j] if j is not None and j < len(r) else None
            d = _como_data(g(col_data)) if col_data else None
            if d is None:
                sem_data += 1
            mes = date(d.year, d.month, 1) if d else None
            lin = [mes]
            lin += [None if _vazio(g(dm)) else str(g(dm)).strip() for dm in dims_presentes]
            lin += [_como_numero(g(x)) if not _vazio(g(x)) else None for x in med_presentes]
            lin += [None if _vazio(g(x)) else str(g(x)).strip() for x in id_presentes]
            if tem_qtd_s:
                vs = _como_numero(g("sinistro")) if "sinistro" in m and not _vazio(g("sinistro")) else None
                eh_sin = papel == "sinistros" or (papel == "misto" and (vs or 0) > 0)
                lin.append(1 if eh_sin and (vs is None or vs > 0) else None)
            if tem_lag:
                oc, av = _como_data(g("data_ocorrencia")), _como_data(g("data_aviso"))
                lag = (av - oc).days if oc and av else None
                if lag is not None and lag < 0:
                    lag_neg += 1
                    lag = None
                lin.append(lag)
            lin.append(f"{papel} · {t.nome}")
            linhas.append(lin)
    if sem_data:
        regras.append({"grupo": "Seguros", "regra": "Data de referência", "status": "ALERTA", "qtd": sem_data,
                       "detalhe": "Linhas sem data reconhecível: entram nos totais, mas não na linha do tempo",
                       "amostra": []})
    if lag_neg:
        regras.append({"grupo": "Seguros", "regra": "Aviso antes da ocorrência", "status": "ALERTA", "qtd": lag_neg,
                       "detalhe": "Data de aviso anterior à ocorrência: ignoradas no tempo de aviso", "amostra": []})

    # ---------------- prêmio ganho (pro rata die sobre a vigência) ----------------
    tem_ganho = "premio" in med_presentes and any(
        p in ("producao", "misto") and "inicio_vigencia" in m and "fim_vigencia" in m for _, m, p in usadas)
    if tem_ganho:
        datas_mes = [l[0] for l in linhas if l[0]]
        ini_g, fim_g = min(datas_mes), max(datas_mes)
        fim_g = date(fim_g.year + (fim_g.month == 12), fim_g.month % 12 + 1, 1)  # exclusivo
        ganho, a_apropriar, invertidas = {}, 0.0, 0
        for t, m, papel in usadas:
            if papel not in ("producao", "misto") or "inicio_vigencia" not in m or "fim_vigencia" not in m:
                continue
            for r in t.linhas:
                def g(c):
                    j = m.get(c)
                    return r[j] if j is not None and j < len(r) else None
                pr = _como_numero(g("premio")) if not _vazio(g("premio")) else None
                iv, fv = _como_data(g("inicio_vigencia")), _como_data(g("fim_vigencia"))
                if pr is None or iv is None or fv is None:
                    continue
                if fv <= iv:
                    invertidas += 1
                    continue
                total = (fv - iv).days
                chave_dims = tuple(None if _vazio(g(dm)) else str(g(dm)).strip() for dm in dims_presentes)
                cur = date(iv.year, iv.month, 1)
                while cur < fv:
                    prox = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
                    dias = (min(prox, fv) - max(cur, iv)).days
                    if dias > 0:
                        v = pr * dias / total
                        if cur >= fim_g:
                            a_apropriar += v
                        elif cur >= ini_g:
                            k = (cur, chave_dims)
                            ganho[k] = ganho.get(k, 0.0) + v
                    cur = prox
        n_base = len(cab)
        cab.insert(n_base - 1, "Prêmio ganho")
        for l in linhas:
            l.insert(n_base - 1, None)
        for (mes, dims_v), v in sorted(ganho.items(), key=lambda kv: kv[0][0]):
            lin = [mes] + list(dims_v) + [None] * (n_base - 2 - len(dims_v)) + [round(v, 2), "prêmio ganho (pro rata)"]
            linhas.append(lin)
        regras.append({"grupo": "Seguros", "regra": "Prêmio ganho (pro rata die)", "status": "OK", "qtd": len(ganho),
                       "detalhe": f"Calculado dia a dia sobre a vigência de cada apólice: R$ {sum(ganho.values()):,.2f} "
                                  f"ganhos no período; R$ {a_apropriar:,.2f} ainda a apropriar (PPNG) após o último mês. "
                                  "A sinistralidade usa o prêmio ganho, como no padrão contábil do mercado.",
                       "amostra": []})
        if invertidas:
            regras.append({"grupo": "Seguros", "regra": "Vigência coerente", "status": "ALERTA", "qtd": invertidas,
                           "detalhe": "Fim de vigência anterior ou igual ao início: apólices fora do prêmio ganho",
                           "amostra": []})
    elif "premio" in med_presentes and "sinistro" in med_presentes:
        regras.append({"grupo": "Seguros", "regra": "Prêmio ganho", "status": "INFO", "qtd": 0,
                       "detalhe": "Sem início/fim de vigência nas emissões: a sinistralidade usa prêmio emitido. "
                                  "Mensalmente ela oscila mais do que a contábil; informe as vigências para o robô "
                                  "calcular o prêmio ganho.", "amostra": []})

    fato = Tabela("seguros", "Fato de seguros (produção + sinistros)", cab, linhas)

    # métricas do setor
    def soma(col):
        return {"op": "sum", "col": col}

    P, S = rot_med["premio"], rot_med["sinistro"]
    metr = []
    if "premio" in med_presentes:
        metr.append({"nome": "Prêmio", "fmt": "brl", "num": soma(P)})
    if "sinistro" in med_presentes:
        metr.append({"nome": "Sinistros", "fmt": "brl", "num": soma(S)})
    if tem_ganho:
        metr.append({"nome": "Prêmio ganho", "fmt": "brl", "num": soma("Prêmio ganho")})
    if "premio" in med_presentes and "sinistro" in med_presentes:
        metr.append({"nome": "Sinistralidade", "fmt": "pct", "num": soma(S),
                     "den": soma("Prêmio ganho" if tem_ganho else P), "menor_melhor": True})
    if "apolice" in id_presentes:
        metr.append({"nome": "Apólices", "fmt": "int", "num": {"op": "distinct", "col": "Apólice"}})
        if "premio" in med_presentes:
            metr.append({"nome": "Ticket médio", "fmt": "brl", "num": soma(P), "den": {"op": "distinct", "col": "Apólice"}})
    if tem_qtd_s:
        qtd = {"op": "distinct", "col": "Nº sinistro"} if "num_sinistro" in id_presentes else soma("Qtd. sinistros")
        metr.append({"nome": "Nº de sinistros", "fmt": "int", "num": qtd})
        if "sinistro" in med_presentes:
            metr.append({"nome": "Severidade", "fmt": "brl", "num": soma(S), "den": qtd, "menor_melhor": True})
        if "apolice" in id_presentes and "premio" in med_presentes:
            metr.append({"nome": "Frequência", "fmt": "pct", "num": qtd,
                         "den": {"op": "distinct", "col": "Apólice", "filtro_col": P}, "menor_melhor": True})
    if "comissao" in med_presentes and "premio" in med_presentes:
        metr.append({"nome": "Comissão %", "fmt": "pct", "num": soma(rot_med["comissao"]), "den": soma(P)})
    if "is" in med_presentes and "premio" in med_presentes:
        metr.append({"nome": "Taxa média", "fmt": "pct3", "num": soma(P), "den": soma(rot_med["is"])})
    for x in ("is", "reserva", "despesa", "salvado", "qtd_itens", "comissao"):
        if x in med_presentes:
            metr.append({"nome": rot_med[x], "fmt": "int" if x == "qtd_itens" else "brl", "num": soma(rot_med[x])})
    if "despesa" in med_presentes and "premio" in med_presentes and "sinistro" in med_presentes:
        num = [soma(S), soma(rot_med["despesa"])] + ([soma(rot_med["comissao"])] if "comissao" in med_presentes else [])
        metr.append({"nome": "Índice combinado", "fmt": "pct", "num": {"op": "sum_many", "cols": [n["col"] for n in num]},
                     "den": soma("Prêmio ganho" if tem_ganho else P), "menor_melhor": True})
    if tem_lag:
        metr.append({"nome": "Tempo médio de aviso (dias)", "fmt": "dec", "num": {"op": "avg", "col": "Dias até o aviso"},
                     "menor_melhor": True})

    perfil = {"tipo": "seguros", "metricas": metr,
              "kpis": [x["nome"] for x in metr if x["nome"] in ("Prêmio", "Sinistros", "Sinistralidade", "Apólices",
                                                                  "Ticket médio", "Frequência", "Severidade",
                                                                  "Comissão %", "Nº de sinistros")][:6],
              "dim_principal": rot_dim[dims_presentes[0]] if dims_presentes else None,
              "dims": [rot_dim[d] for d in dims_presentes],
              "dims_comuns": [rot_dim[d] for d in dims_presentes
                              if not (tem_p and tem_s) or (d in dims_por_papel["producao"] and d in dims_por_papel["sinistros"])]}
    return fato, regras, perfil
