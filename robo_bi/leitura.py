"""Leitura e normalização dos arquivos de entrada (CSV ou XLSX).

Tudo aqui é determinístico: nenhuma linha é "adivinhada". Valores que não
podem ser interpretados viram None e são reportados pelo robô de validação.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- texto


def norm(texto) -> str:
    """'Data de Nascimento ' -> 'data_de_nascimento' (sem acento, minúsculo)."""
    if texto is None:
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s


# Nome canônico -> apelidos aceitos nas planilhas (já normalizados)
ALIASES = {
    # beneficiários
    "id_beneficiario": ["id_beneficiario", "id", "matricula", "carteirinha", "codigo_beneficiario",
                        "cod_beneficiario", "beneficiario_id", "numero_carteirinha", "cartao", "codigo"],
    "empresa": ["empresa", "estipulante", "subestipulante", "razao_social", "cliente", "contratante"],
    "operadora": ["operadora", "seguradora", "operadora_saude"],
    "plano": ["plano", "produto", "acomodacao"],
    "data_nascimento": ["data_nascimento", "data_de_nascimento", "nascimento", "dt_nascimento", "dt_nasc"],
    "sexo": ["sexo", "genero"],
    "titularidade": ["titularidade", "tipo_beneficiario", "tipo", "titular_dependente", "condicao"],
    "parentesco": ["parentesco", "grau_parentesco", "grau_de_parentesco", "relacao_dependencia"],
    "data_inclusao": ["data_inclusao", "data_de_inclusao", "inclusao", "dt_inclusao", "data_adesao", "inicio_vigencia"],
    "data_exclusao": ["data_exclusao", "data_de_exclusao", "exclusao", "dt_exclusao", "data_cancelamento", "fim_vigencia"],
    # faturamento
    "competencia": ["competencia", "mes_competencia", "mes", "referencia", "mes_referencia", "periodo"],
    "premio": ["premio", "valor_premio", "faturamento", "valor_faturado", "valor_fatura", "mensalidade"],
    "vidas": ["vidas", "qtd_vidas", "quantidade_vidas", "vidas_faturadas"],
    # sinistros
    "data_atendimento": ["data_atendimento", "data_do_atendimento", "dt_atendimento", "data_evento",
                         "data_realizacao", "data_procedimento"],
    "tipo_evento": ["tipo_evento", "tipo_atendimento", "tipo_de_atendimento", "grupo", "natureza",
                    "tipo_guia", "classificacao", "tipo"],
    "valor": ["valor", "valor_pago", "valor_sinistro", "custo", "valor_liquido", "valor_total"],
    "quantidade": ["quantidade", "qtd", "qtde"],
    "dias_internacao": ["dias_internacao", "diarias", "qtd_diarias", "dias"],
    # contratos
    "mes_renovacao": ["mes_renovacao", "mes_reajuste", "aniversario", "mes_aniversario", "renovacao"],
    "limite_sinistralidade": ["limite_sinistralidade", "meta_sinistralidade", "sinistralidade_meta",
                              "break_even", "limite", "meta"],
    "vcmh": ["vcmh", "inflacao_medica", "reajuste_financeiro"],
}


def mapear_colunas(cabecalho: list[str], canonicas: list[str]) -> dict[str, int]:
    """Mapeia nomes canônicos para o índice da coluna no arquivo."""
    normais = [norm(c) for c in cabecalho]
    usados: set[int] = set()
    mapa: dict[str, int] = {}
    for can in canonicas:
        for alias in ALIASES.get(can, [can]):
            if alias in normais:
                idx = normais.index(alias)
                if idx not in usados:
                    mapa[can] = idx
                    usados.add(idx)
                    break
    return mapa


# ---------------------------------------------------------------- classificação

OBRIGATORIAS = {
    "beneficiarios": ["id_beneficiario", "empresa", "data_nascimento", "data_inclusao"],
    "faturamento": ["competencia", "empresa", "premio"],
    "sinistros": ["id_beneficiario", "data_atendimento", "valor"],
    "contratos": ["empresa", "mes_renovacao"],
}
OPCIONAIS = {
    "beneficiarios": ["operadora", "plano", "sexo", "titularidade", "parentesco", "data_exclusao"],
    "faturamento": ["operadora", "vidas"],
    "sinistros": ["empresa", "tipo_evento", "quantidade", "dias_internacao"],
    "contratos": ["operadora", "limite_sinistralidade", "vcmh"],
}


def classificar(tabela) -> tuple[str | None, dict[str, int]]:
    """Descobre, pelas colunas, se a tabela é beneficiários, faturamento,
    sinistros ou contratos de plano de saúde. Retorna (tipo|None, mapa)."""
    melhor, mapa_melhor, score_melhor = None, {}, 0
    for tipo in OBRIGATORIAS:
        mapa = mapear_colunas(tabela.cabecalho, OBRIGATORIAS[tipo] + OPCIONAIS[tipo])
        if all(c in mapa for c in OBRIGATORIAS[tipo]):
            score = len(mapa) + len(OBRIGATORIAS[tipo])
            if score > score_melhor:
                melhor, mapa_melhor, score_melhor = tipo, mapa, score
    return melhor, mapa_melhor


# ---------------------------------------------------------------- conversores

_MESES_PT = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
             "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def para_data(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):  # serial do Excel
        if 20000 < v < 80000:
            return date(1899, 12, 30) + timedelta(days=int(v))
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    if re.fullmatch(r"\d{5}", s):
        return para_data(int(s))
    return None


def para_competencia(v) -> tuple[int, int] | None:
    """Aceita 2025-01, 01/2025, jan/25, jan/2025, 202501, datas."""
    if v is None or v == "":
        return None
    if isinstance(v, (datetime, date)):
        return (v.year, v.month)
    s = norm(v)
    m = re.fullmatch(r"(\d{4})_(\d{1,2})(?:_\d{1,2})?", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(?:\d{1,2}_)?(\d{1,2})_(\d{4})", s)
        if m:
            mo, y = int(m.group(1)), int(m.group(2))
        else:
            m = re.fullmatch(r"([a-z]{3})[a-z]*_(\d{2,4})", s)
            if m and m.group(1) in _MESES_PT:
                mo = _MESES_PT[m.group(1)]
                y = int(m.group(2))
                y = y + 2000 if y < 100 else y
            else:
                m = re.fullmatch(r"(\d{4})(\d{2})", s)
                if not m:
                    d = para_data(v)
                    return (d.year, d.month) if d else None
                y, mo = int(m.group(1)), int(m.group(2))
    if 1 <= mo <= 12 and 1990 <= y <= 2100:
        return (y, mo)
    return None


def para_numero(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace("%", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    negativo = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negativo else n


def para_sexo(v) -> str | None:
    s = norm(v)
    if s in ("m", "masc", "masculino", "h", "homem", "1"):
        return "M"
    if s in ("f", "fem", "feminino", "mulher", "2"):
        return "F"
    return None


def para_titularidade(v) -> str | None:
    s = norm(v)
    if not s:
        return None
    if s.startswith("tit") or s in ("t", "titular"):
        return "T"
    if s.startswith("dep") or s in ("d",) or s.startswith("agreg"):
        return "D"
    return None


TIPOS = ["consulta", "exame", "terapia", "pronto_socorro", "internacao", "outros"]
TIPOS_LABEL = {"consulta": "Consultas", "exame": "Exames", "terapia": "Terapias",
               "pronto_socorro": "Pronto-socorro", "internacao": "Internações", "outros": "Outros"}


def para_tipo(v) -> tuple[str, bool]:
    """Classifica o tipo de evento. Retorna (tipo, reconhecido)."""
    s = norm(v)
    regras = [
        ("internacao", ["internac", "hospital", "intern", "uti", "cirurg", "obstet", "parto"]),
        ("pronto_socorro", ["pronto", "ps", "urgenc", "emergenc", "pa"]),
        ("consulta", ["consult", "eletiv"]),
        ("exame", ["exame", "sadt", "diagnost", "laborat", "imagem"]),
        ("terapia", ["terap", "fisio", "psico", "fono", "quimio", "radio", "hemodial"]),
    ]
    for tipo, chaves in regras:
        for c in chaves:
            if s == c or s.startswith(c) or (len(c) > 3 and c in s):
                return tipo, True
    return "outros", s in ("outros", "outro", "demais")
