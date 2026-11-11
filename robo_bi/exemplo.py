"""Gera bases FICTÍCIAS para testar o robô nos 3 modos (saúde, seguradora, outra área).
Inclui de propósito alguns erros para o robô de validação encontrar."""
from __future__ import annotations

import csv
import json
import math
import random
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape


# ---------------------------------------------------------------- xlsx mínimo (sem bibliotecas)
def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def escrever_xlsx(caminho: Path, abas: dict[str, list[list]]):
    def cel(ref, v):
        if v is None:
            return ""
        if isinstance(v, date):
            return f'<c r="{ref}" s="1"><v>{(v - date(1899, 12, 30)).days}</v></c>'
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'

    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as z:
        nomes = list(abas)
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(nomes)))
                   + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(nomes)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(nomes)))
                   + f'<Relationship Id="rId{len(nomes) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="1"><font/></fonts><fills count="1"><fill/></fills><borders count="1"><border/></borders>'
                   '<cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs></styleSheet>')
        for i, n in enumerate(nomes):
            linhas = abas[n]
            xml = ['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
            for r, lin in enumerate(linhas):
                xml.append(f'<row r="{r + 1}">' + "".join(cel(f"{_col(c)}{r + 1}", v) for c, v in enumerate(lin)) + "</row>")
            xml.append("</sheetData></worksheet>")
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", "".join(xml))


def _csv(caminho: Path, cab, linhas, sep=";"):
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=sep)
        w.writerow(cab)
        w.writerows(linhas)


def _br(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _meses(ini=(2025, 1), n=22):
    y, m = ini
    out = []
    for _ in range(n):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _fim(y, m):
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


# ---------------------------------------------------------------- SAÚDE
def gerar_saude(pasta: Path, rnd: random.Random):
    pasta.mkdir(parents=True, exist_ok=True)
    empresas = [  # nome, operadora, vidas iniciais, alvo sinistralidade, renovação, limite
        ("Metalúrgica Vale Verde", "Operadora Alfa", 1500, 0.73, 5, 75),
        ("Transportes Rota Sul", "Operadora Beta", 800, 0.86, 1, 72),
        ("Varejo Bom Preço", "Operadora Alfa", 1050, 0.69, 3, 75),
        ("Construtora Pilar", "Operadora Gama", 650, 0.81, 12, 70),
        ("Tech Nuvem Sistemas", "Operadora Beta", 430, 0.58, 2, 75),
        ("Colégio Horizonte", "Operadora Gama", 300, 0.77, 8, 75),
    ]
    meses = _meses()
    ini, fim = date(2025, 1, 1), _fim(*meses[-1])
    benef, seq = [], 100000

    def nova_vida(emp, op, inc, tit=True, idade=None, parentesco="Titular"):
        nonlocal seq
        seq += 1
        idade = idade if idade is not None else max(0, int(rnd.triangular(19, 66, 34)))
        nasc = inc - timedelta(days=int(idade * 365.25 + rnd.randint(0, 364)))
        return {"id": f"B{seq}", "empresa": emp, "operadora": op, "plano": rnd.choice(["Enfermaria", "Apartamento"]),
                "nasc": nasc, "sexo": rnd.choice("MF"), "tit": "Titular" if tit else "Dependente",
                "parentesco": parentesco, "inc": inc, "exc": None}

    for emp, op, n, *_ in empresas:
        tit_n = int(n * 0.55)
        for _ in range(tit_n):
            inc0 = ini - timedelta(days=rnd.randint(30, 2500))
            t = nova_vida(emp, op, inc0)
            benef.append(t)
            r = rnd.random()
            if r < 0.45:
                benef.append(nova_vida(emp, op, inc0, False, max(18, int(rnd.gauss(t_idade(t, inc0), 4))), "Cônjuge"))
            if r < 0.6:
                for _ in range(rnd.choice([1, 1, 2])):
                    benef.append(nova_vida(emp, op, inc0, False, rnd.randint(0, 22), "Filho(a)"))
        # filhos acima da idade limite (para a aba elegibilidade)
        for _ in range(rnd.randint(1, 4)):
            benef.append(nova_vida(emp, op, ini - timedelta(days=900), False, rnd.randint(25, 29), "Filho(a)"))
        # movimentação mensal
        for (y, m) in meses:
            for _ in range(int(n * 0.012 * rnd.uniform(0.5, 1.5))):
                benef.append(nova_vida(emp, op, date(y, m, rnd.randint(1, 28))))
    ativos = [b for b in benef]
    for b in ativos:
        if rnd.random() < 0.012 * len(meses) * 0.8 and b["tit"] == "Titular":
            folga = (fim - max(b["inc"], ini)).days
            d = max(b["inc"], ini) + timedelta(days=rnd.randint(20, max(21, folga)))
            if d < fim:
                b["exc"] = d

    # sinistros
    sin, idx_emp = [], {e[0]: e for e in empresas}
    fator_emp = {e[0]: (e[3] / 0.75) for e in empresas}
    for b in benef:
        for (y, m) in meses:
            fm, im = _fim(y, m), date(y, m, 1)
            if b["inc"] > fm or (b["exc"] and b["exc"] <= im):
                continue
            idade = t_idade(b, fm)
            fa = 0.7 + (idade / 45) ** 2 * 0.9 if idade >= 18 else 0.9
            fa *= fator_emp[b["empresa"]] ** 0.5
            tend = 1 + 0.008 * meses.index((y, m))
            def ev(tipo, p, med, sd):
                if rnd.random() < min(0.95, p * fa * tend):
                    v = math.exp(rnd.gauss(math.log(med), sd))
                    dia = date(y, m, rnd.randint(1, 28))
                    if dia < b["inc"] or (b["exc"] and dia > b["exc"]):
                        return
                    dias = max(1, int(rnd.expovariate(1 / 4))) if tipo == "Internação" else ""
                    sin.append([b["id"], b["empresa"], dia.strftime("%d/%m/%Y"), tipo, _br(v), dias])
            ev("Consulta", 0.32, 180, 0.25)
            ev("Exame", 0.30, 160, 0.9)
            ev("Exame", 0.12, 260, 0.8)
            ev("Terapia", 0.05, 180, 0.4)
            ev("Pronto-socorro", 0.05, 650, 0.6)
            ev("Internação", 0.0055, 16000 * fator_emp[b["empresa"]], 0.95)
    # atendimentos após exclusão (glosáveis) e estornos
    excluidos = [b for b in benef if b["exc"] and b["exc"] < fim - timedelta(days=40)]
    for b in rnd.sample(excluidos, min(14, len(excluidos))):
        d = b["exc"] + timedelta(days=rnd.randint(3, 35))
        sin.append([b["id"], b["empresa"], d.strftime("%d/%m/%Y"), rnd.choice(["Consulta", "Exame", "Pronto-socorro"]),
                    _br(rnd.uniform(150, 2500)), ""])
    for _ in range(6):
        s = rnd.choice(sin)
        sin.append([s[0], s[1], s[2], s[3], "-" + s[4], ""])
    sin.append(["B999999", "Varejo Bom Preço", "10/06/2026", "Consulta", "210,00", ""])  # sem cadastro
    sin.append([benef[5]["id"], benef[5]["empresa"], "31/02/2026", "Exame", "90,00", ""])  # data inválida

    # prêmio: calibrado para a sinistralidade-alvo de cada empresa
    custo = {}
    for s in sin:
        try:
            d, mm, yy = s[2].split("/")
            k = (s[1], int(yy), int(mm))
            custo[k] = custo.get(k, 0) + float(s[4].replace(".", "").replace(",", "."))
        except ValueError:
            pass
    fat = []
    for emp, op, n, alvo, renov, lim in empresas:
        vidas_m = []
        for (y, m) in meses:
            fm = _fim(y, m)
            vidas_m.append(sum(1 for b in benef if b["empresa"] == emp and b["inc"] <= fm and not (b["exc"] and b["exc"] <= fm)))
        total_custo = sum(custo.get((emp, y, m), 0) for (y, m) in meses)
        pm_base = total_custo / sum(vidas_m) / alvo
        for i, (y, m) in enumerate(meses):
            reaj = 1.0 if (y == 2025 or m < renov) else 1.12
            reaj = reaj if y == 2026 else (0.95 if m < renov else 1.0)
            fat.append([f"{m:02d}/{y}", emp, op, vidas_m[i] + (rnd.choice([0, 0, 0, 1, -1])), round(vidas_m[i] * pm_base * reaj, 2)])

    # problemas de cadastro plantados
    linhas_b = [[b["id"], b["empresa"], b["operadora"], b["plano"], b["nasc"].strftime("%d/%m/%Y"), b["sexo"], b["tit"],
                 b["parentesco"], b["inc"].strftime("%d/%m/%Y"), b["exc"].strftime("%d/%m/%Y") if b["exc"] else ""]
                for b in benef]
    linhas_b.append(linhas_b[10][:])  # ID duplicado
    linhas_b[20][5] = ""              # sexo vazio
    linhas_b.append(["B888888", "Construtora Pilar", "Operadora Gama", "Enfermaria", "15/13/1980", "M", "Titular",
                     "Titular", "01/02/2025", ""])  # nascimento inválido
    _csv(pasta / "cadastro_beneficiarios.csv",
         ["Matrícula", "Estipulante", "Operadora", "Plano", "Data de Nascimento", "Sexo", "Titularidade",
          "Grau de Parentesco", "Data de Inclusão", "Data de Exclusão"], linhas_b)
    _csv(pasta / "utilizacao_sinistros.csv",
         ["Carteirinha", "Empresa", "Data do Atendimento", "Tipo de Atendimento", "Valor Pago", "Diárias"], sin)
    escrever_xlsx(pasta / "faturamento_premios.xlsx",
                  {"Faturas": [["Relatório de faturamento — Corretora Exemplo"], [],
                               ["Competência", "Empresa", "Operadora", "Vidas Faturadas", "Prêmio"]] + fat})
    (pasta / "contratos.json").write_text(json.dumps(
        [{"empresa": e[0], "operadora": e[1], "mes_renovacao": e[4], "limite_sinistralidade": e[5], "vcmh": 12.5}
         for e in empresas], ensure_ascii=False, indent=1), encoding="utf-8")


def t_idade(b, em):
    n = b["nasc"]
    return em.year - n.year - ((em.month, em.day) < (n.month, n.day))


# ---------------------------------------------------------------- SEGURADORA MULTIRRAMO
def gerar_seguradora(pasta: Path, rnd: random.Random):
    pasta.mkdir(parents=True, exist_ok=True)
    ramos = {  # prêmio médio, freq anual, severidade média, alvo sinistralidade, IS média, comissão
        "Automóvel": (3200, 0.16, 11000, 0.66, 85000, 0.18),
        "Residencial": (650, 0.05, 4200, 0.33, 450000, 0.25),
        "Vida em Grupo": (1800, 0.012, 60000, 0.42, 150000, 0.20),
        "Vida Individual": (1100, 0.006, 90000, 0.30, 250000, 0.30),
        "Empresarial": (5200, 0.07, 30000, 0.48, 2500000, 0.20),
        "Grandes Riscos": (380000, 0.10, 1600000, 0.62, 400000000, 0.10),
        "Rural": (9500, 0.14, 45000, 0.74, 900000, 0.15),
        "Transportes": (14000, 0.09, 70000, 0.52, 3000000, 0.15),
        "Responsabilidade Civil": (7000, 0.03, 120000, 0.45, 2000000, 0.20),
        "Garantia": (12000, 0.01, 450000, 0.28, 5000000, 0.20),
    }
    pesos = {"Automóvel": 40, "Residencial": 25, "Vida em Grupo": 10, "Vida Individual": 8, "Empresarial": 8,
             "Grandes Riscos": 0.6, "Rural": 4, "Transportes": 2.5, "Responsabilidade Civil": 2, "Garantia": 1.5}
    ufs = {"SP": ("Sudeste", 34), "RJ": ("Sudeste", 11), "MG": ("Sudeste", 10), "PR": ("Sul", 7), "RS": ("Sul", 7),
           "SC": ("Sul", 5), "BA": ("Nordeste", 5), "PE": ("Nordeste", 4), "CE": ("Nordeste", 3), "GO": ("Centro-Oeste", 4),
           "DF": ("Centro-Oeste", 3), "MT": ("Centro-Oeste", 3), "PA": ("Norte", 2), "AM": ("Norte", 2)}
    canais = {"Corretor": 55, "Banco parceiro": 20, "Digital": 12, "Varejo/Afinidade": 8, "Concessionária": 5}
    corretores = [f"Corretora {n}" for n in ["Atlas", "Boa Vista", "Cruzeiro", "Delta", "Estrela", "Farol", "Guia",
                                             "Horizonte", "Ipê", "Jatobá", "Kappa", "Litoral"]]
    causas = {"Automóvel": ["Colisão", "Roubo/Furto", "Vidros", "Terceiros", "Perda total"],
              "Residencial": ["Danos elétricos", "Incêndio", "Roubo", "Vendaval", "Danos por água"],
              "Vida em Grupo": ["Morte natural", "Morte acidental", "Invalidez"],
              "Vida Individual": ["Morte natural", "Morte acidental", "Doenças graves"],
              "Empresarial": ["Incêndio", "Danos elétricos", "Roubo", "Lucros cessantes"],
              "Grandes Riscos": ["Incêndio", "Quebra de máquinas", "Riscos de engenharia", "Lucros cessantes"],
              "Rural": ["Seca", "Granizo", "Geada", "Excesso de chuva"],
              "Transportes": ["Avaria", "Roubo de carga", "Tombamento"],
              "Responsabilidade Civil": ["RC Geral", "RC Profissional", "D&O"],
              "Garantia": ["Inadimplemento contratual", "Garantia judicial"]}
    meses = _meses((2024, 11), 24)
    prod, sins, ap_seq, sin_seq = [], [], 7_000_000, 50_000
    esc = lambda d: rnd.choices(list(d), weights=[v if not isinstance(v, tuple) else v[1] for v in d.values()])[0]
    for i, (y, m) in enumerate(meses):
        sazon = 1 + 0.12 * math.sin((m - 3) / 12 * 2 * math.pi)
        for _ in range(int(1400 * sazon * (1 + 0.01 * i))):
            ramo = esc(pesos)
            pm, freq, sev, alvo, is_, com = ramos[ramo]
            uf = esc(ufs)
            canal = esc(canais) if ramo not in ("Grandes Riscos", "Garantia") else "Corretor"
            corr = rnd.choice(corretores) if canal == "Corretor" else ""
            ap_seq += 1
            premio = round(math.exp(rnd.gauss(math.log(pm), 0.45)), 2)
            emis = date(y, m, rnd.randint(1, 28))
            prod.append([emis, emis, emis + timedelta(days=365), f"AP{ap_seq}", ramo, uf, ufs[uf][0], canal, corr, premio,
                         round(premio * com * rnd.uniform(0.9, 1.1), 2), round(is_ * rnd.uniform(0.5, 1.5), 2)])
            # sinistros da apólice (calibrados para o alvo de sinistralidade)
            p_sin = freq * (alvo / (sev * freq / pm)) * (1.25 if (uf == "RS" and ramo == "Rural") else 1)
            p_sin *= 1.3 if (ramo == "Automóvel" and uf in ("RJ", "PE")) else 1
            while rnd.random() < p_sin:
                oc = emis + timedelta(days=rnd.randint(1, 360))
                if oc > _fim(*meses[-1]):
                    break
                sin_seq += 1
                aviso = oc + timedelta(days=int(rnd.expovariate(1 / (9 if ramo != "Vida em Grupo" else 35))))
                valor = round(math.exp(rnd.gauss(math.log(sev), 0.9)), 2)
                status = rnd.choices(["Pago", "Em regulação", "Negado", "Pago parcialmente"], [70, 18, 6, 6])[0]
                sins.append([f"S{sin_seq}", f"AP{ap_seq}", ramo, uf, canal, rnd.choice(causas[ramo]),
                             oc.strftime("%Y-%m-%d"), aviso.strftime("%Y-%m-%d"), valor if status != "Negado" else 0, status])
                p_sin *= 0.3
    # erros plantados
    sins.append(["S999998", "AP0", "Automóvel", "SP", "Digital", "Colisão", "2026-05-10", "2026-05-01", 5300, "Pago"])
    sins.append(["S999999", "AP1", "residencial ", "sp", "Digital", "Roubo", "2026-03-02", "2026-03-05", "abc", "Pago"])
    escrever_xlsx(pasta / "producao_emissoes.xlsx", {
        "Emissões": [["Data Emissão", "Início Vigência", "Fim Vigência", "Nº Apólice", "Ramo", "UF", "Região", "Canal", "Corretor", "Prêmio Emitido",
                      "Comissão", "Importância Segurada"]] + prod,
        "Leia-me": [["Base fictícia gerada pelo robô para teste"], ["Origem", "Sistema de emissão"]]})
    _csv(pasta / "sinistros_avisados.csv",
         ["numero_sinistro", "apolice", "ramo", "uf", "canal", "causa", "data_ocorrencia", "data_aviso",
          "valor_indenizado", "status_sinistro"], sins, sep=",")


# ---------------------------------------------------------------- OUTRA ÁREA (vendas)
def gerar_vendas(pasta: Path, rnd: random.Random):
    pasta.mkdir(parents=True, exist_ok=True)
    prods = {"Notebook": (4200, "Informática"), "Monitor": (1100, "Informática"), "Mouse": (90, "Acessórios"),
             "Teclado": (180, "Acessórios"), "Cadeira gamer": (1500, "Móveis"), "Mesa": (900, "Móveis"),
             "Headset": (350, "Acessórios"), "Impressora": (950, "Informática")}
    lojas = ["Loja Centro", "Loja Shopping Norte", "Loja Bairro Sul", "E-commerce"]
    vend = ["Ana", "Bruno", "Carla", "Diego", "Elisa", "Fábio"]
    regs = []
    for i in range(9000):
        d = date(2025, 1, 1) + timedelta(days=rnd.randint(0, 670))
        p = rnd.choice(list(prods))
        q = rnd.choice([1, 1, 1, 2, 3])
        preco = prods[p][0] * rnd.uniform(0.9, 1.1)
        regs.append({"pedido": 100000 + i, "data": d.isoformat(), "loja": rnd.choice(lojas), "vendedor": rnd.choice(vend),
                     "produto": p, "categoria": prods[p][1], "quantidade": q, "valor_total": round(preco * q, 2),
                     "desconto": round(preco * q * rnd.choice([0, 0, 0.05, 0.1]), 2),
                     "pagamento": rnd.choice(["Pix", "Cartão", "Boleto"])})
    (pasta / "vendas.json").write_text(json.dumps({"empresa": "Loja Exemplo", "vendas": regs}, ensure_ascii=False),
                                       encoding="utf-8")


def gerar_todos(base: Path, semente: int = 42):
    rnd = random.Random(semente)
    print("🧪 Gerando bases fictícias em", base)
    gerar_saude(base / "saude", rnd)
    gerar_seguradora(base / "seguradora", rnd)
    gerar_vendas(base / "vendas", rnd)
