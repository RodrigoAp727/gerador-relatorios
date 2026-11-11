"""Leitor universal de arquivos tabulares — sem dependências obrigatórias.

Formatos lidos nativamente (só Python):
  .csv .tsv .txt .dat  (detecta separador ; , tab | e encoding)
  .xlsx .xlsm .xltx    (TODAS as abas; datas pelo formato da célula)
  .ods                 (LibreOffice/Google Planilhas exportado, todas as abas)
  .json .jsonl         (lista de objetos, objeto com listas, objetos aninhados)
  .xml                 (registros repetidos)
  .html .htm           (todas as <table>)
Formatos com biblioteca opcional:
  .xls (xlrd) · .parquet/.feather (pandas + pyarrow) · .sav/.dta (pandas)

Cada arquivo vira uma lista de Tabela(nome, cabecalho, linhas). A linha de
cabeçalho é detectada automaticamente (ignora títulos/logos no topo).
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

EXTENSOES = {".csv", ".tsv", ".txt", ".dat", ".xlsx", ".xlsm", ".xltx", ".xls", ".ods", ".json", ".jsonl",
             ".ndjson", ".xml", ".html", ".htm", ".parquet", ".feather", ".sav", ".dta"}


@dataclass
class Tabela:
    arquivo: str
    nome: str          # "arquivo.xlsx › Aba"
    cabecalho: list[str]
    linhas: list[list]

    @property
    def n(self):
        return len(self.linhas)


# =================================================================== utilidades

def _decodificar(bruto: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if bruto[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return bruto.decode(enc)
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return bruto.decode(enc)
        except UnicodeDecodeError:
            continue
    return bruto.decode("latin-1", errors="replace")


def _vazio(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _achar_cabecalho(linhas: list[list]) -> int:
    """Escolhe, entre as 25 primeiras linhas, a que mais parece cabeçalho:
    muitas células de texto não vazias, únicas, e seguida de linhas de dados."""
    melhor, idx = -1.0, 0
    largura = max((sum(not _vazio(v) for v in r) for r in linhas[:200]), default=0)
    for i, r in enumerate(linhas[:25]):
        cel = [v for v in r if not _vazio(v)]
        if len(cel) < max(1, largura * 0.5):
            continue
        textos = [v for v in cel if isinstance(v, str) and not re.fullmatch(r"[-+R$\s\d.,%/:]+", v.strip())]
        unicos = len({str(v).strip().lower() for v in cel})
        score = len(textos) + (unicos == len(cel)) * 2 - i * 0.05
        if score > melhor:
            melhor, idx = score, i
    return idx


def _montar(arquivo: str, nome: str, linhas: list[list]) -> Tabela | None:
    linhas = [list(r) for r in linhas if any(not _vazio(v) for v in r)]
    if len(linhas) < 2:
        return None
    h = _achar_cabecalho(linhas)
    cab_bruto = linhas[h]
    dados = linhas[h + 1:]
    largura = max(len(cab_bruto), max((len(r) for r in dados), default=0))
    cab, vistos = [], {}
    for j in range(largura):
        c = cab_bruto[j] if j < len(cab_bruto) else None
        c = str(c).strip() if not _vazio(c) else f"coluna_{j + 1}"
        if isinstance(cab_bruto[j] if j < len(cab_bruto) else None, (datetime, date)):
            c = cab_bruto[j].strftime("%Y-%m")
        if c in vistos:
            vistos[c] += 1
            c = f"{c}_{vistos[c]}"
        else:
            vistos[c] = 1
        cab.append(c)
    # remove colunas totalmente vazias
    cheias = [j for j in range(largura) if not cab[j].startswith("coluna_")
              or any(j < len(r) and not _vazio(r[j]) for r in dados)]
    cab = [cab[j] for j in cheias]
    dados = [[(r[j] if j < len(r) else None) for j in cheias] for r in dados]
    # remove linhas de total no fim ("Total", "Soma")
    while dados and isinstance(dados[-1][0], str) and dados[-1][0].strip().lower().startswith(("total", "soma")):
        dados.pop()
    if not dados:
        return None
    return Tabela(arquivo, nome, cab, dados)


# =================================================================== CSV / TXT

def _ler_csv(p: Path) -> list[Tabela]:
    texto = _decodificar(p.read_bytes())
    amostra = "\n".join(texto.splitlines()[:50])
    try:
        delim = csv.Sniffer().sniff(amostra, delimiters=";,\t|").delimiter
    except csv.Error:
        cont = {d: amostra.count(d) for d in (";", ",", "\t", "|")}
        delim = max(cont, key=cont.get)
    linhas = list(csv.reader(io.StringIO(texto), delimiter=delim))
    t = _montar(p.name, p.name, linhas)
    return [t] if t else []


# =================================================================== XLSX nativo

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_DATE_IDS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}


def _col_idx(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def _ler_xlsx(p: Path) -> list[Tabela]:
    z = zipfile.ZipFile(p)
    nomes = set(z.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in nomes:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    estilos_data: set[int] = set()
    if "xl/styles.xml" in nomes:
        st = ET.fromstring(z.read("xl/styles.xml"))
        custom = {}
        nf = st.find("m:numFmts", _NS)
        if nf is not None:
            for f in nf.findall("m:numFmt", _NS):
                custom[int(f.get("numFmtId"))] = f.get("formatCode", "")
        xfs = st.find("m:cellXfs", _NS)
        if xfs is not None:
            for i, xf in enumerate(xfs.findall("m:xf", _NS)):
                fid = int(xf.get("numFmtId", 0))
                code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom.get(fid, "")).lower()
                if fid in _DATE_IDS or (code and re.search(r"[dy]", code) and "0" not in code.replace("00", "")):
                    estilos_data.add(i)
                elif code and re.search(r"(^|[^h])m{1,4}[/\-.]y|y{2,4}[/\-.]m", code):
                    estilos_data.add(i)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in nomes:
        for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("pr:Relationship", _NS):
            alvo = r.get("Target").lstrip("/")
            rels[r.get("Id")] = alvo if alvo.startswith("xl/") else "xl/" + alvo
    tabelas = []
    sheets = wb.find("m:sheets", _NS)
    for sh in (sheets.findall("m:sheet", _NS) if sheets is not None else []):
        if sh.get("state") in ("hidden", "veryHidden"):
            continue
        alvo = rels.get(sh.get(f"{{{_NS['r']}}}id"))
        if not alvo or alvo not in nomes:
            continue
        linhas: list[list] = []
        for _, el in ET.iterparse(z.open(alvo)):
            if el.tag != f"{{{_NS['m']}}}row":
                continue
            row: dict[int, object] = {}
            for c in el.findall("m:c", _NS):
                t = c.get("t", "n")
                v = c.find("m:v", _NS)
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
                elif v is None or v.text is None:
                    continue
                elif t == "s":
                    val = shared[int(v.text)]
                elif t in ("str", "e"):
                    val = v.text if t == "str" else None
                elif t == "b":
                    val = v.text == "1"
                else:
                    num = float(v.text)
                    if int(c.get("s", 0)) in estilos_data and 0 < num < 2958466:
                        base = datetime(1899, 12, 30) + timedelta(days=num)
                        val = base.date() if num == int(num) else base
                    else:
                        val = int(num) if num.is_integer() and abs(num) < 1e15 else num
                row[_col_idx(c.get("r", "A"))] = val
            el.clear()
            if row:
                lin = [None] * (max(row) + 1)
                for k, val in row.items():
                    lin[k] = val
                linhas.append(lin)
        t = _montar(p.name, f"{p.name} › {sh.get('name')}", linhas)
        if t:
            tabelas.append(t)
    return tabelas


# =================================================================== ODS nativo

def _ler_ods(p: Path) -> list[Tabela]:
    ns = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
          "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
          "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
    T, O = "{%s}" % ns["table"], "{%s}" % ns["office"]
    root = ET.fromstring(zipfile.ZipFile(p).read("content.xml"))
    tabelas = []
    for tb in root.iter(T + "table"):
        linhas = []
        for tr in tb.iter(T + "table-row"):
            rep_r = min(int(tr.get(T + "number-rows-repeated", 1)), 1000)
            lin = []
            for tc in tr:
                if tc.tag not in (T + "table-cell", T + "covered-table-cell"):
                    continue
                rep = min(int(tc.get(T + "number-columns-repeated", 1)), 200)
                tipo = tc.get(O + "value-type")
                if tipo in ("float", "currency", "percentage"):
                    val = float(tc.get(O + "value"))
                    val = int(val) if val.is_integer() else val
                elif tipo == "date":
                    s = tc.get(O + "date-value")
                    val = datetime.fromisoformat(s) if "T" in s else date.fromisoformat(s)
                elif tipo == "boolean":
                    val = tc.get(O + "boolean-value") == "true"
                else:
                    val = "\n".join("".join(t.itertext()) for t in tc.findall("text:p", ns)) or None
                lin.extend([val] * rep)
            while lin and _vazio(lin[-1]):
                lin.pop()
            if lin:
                linhas.extend([lin] * rep_r)
        t = _montar(p.name, f"{p.name} › {tb.get(T + 'name')}", linhas)
        if t:
            tabelas.append(t)
    return tabelas


# =================================================================== JSON

def _achatar(obj, prefixo="") -> dict:
    out = {}
    for k, v in obj.items():
        chave = f"{prefixo}{k}"
        if isinstance(v, dict):
            out.update(_achatar(v, chave + "_"))
        elif isinstance(v, list):
            out[chave] = ", ".join(str(x) for x in v if not isinstance(x, (dict, list)))
        else:
            out[chave] = v
    return out


def _registros_para_tabela(arquivo, nome, regs: list[dict]) -> Tabela | None:
    regs = [_achatar(r) for r in regs if isinstance(r, dict)]
    if not regs:
        return None
    cab: list[str] = []
    for r in regs:
        for k in r:
            if k not in cab:
                cab.append(k)
    return Tabela(arquivo, nome, cab, [[r.get(c) for c in cab] for r in regs])


def _ler_json(p: Path) -> list[Tabela]:
    texto = _decodificar(p.read_bytes()).strip()
    if p.suffix.lower() in (".jsonl", ".ndjson") or (texto.startswith("{") and "\n{" in texto):
        try:
            dados = [json.loads(l) for l in texto.splitlines() if l.strip()]
        except json.JSONDecodeError:
            dados = json.loads(texto)
    else:
        dados = json.loads(texto)
    tabelas = []

    def visitar(obj, nome):
        if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:50]):
            t = _registros_para_tabela(p.name, nome, obj)
            if t:
                tabelas.append(t)
        elif isinstance(obj, list) and obj and all(isinstance(x, list) for x in obj[:50]):
            t = _montar(p.name, nome, obj)
            if t:
                tabelas.append(t)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                visitar(v, f"{p.name} › {k}")

    visitar(dados, p.name)
    return tabelas


# =================================================================== XML

def _ler_xml(p: Path) -> list[Tabela]:
    root = ET.fromstring(p.read_bytes())

    def nome_tag(t):
        return t.split("}")[-1]

    melhores: dict[str, list] = {}
    for pai in root.iter():
        filhos = list(pai)
        if len(filhos) < 2:
            continue
        cont: dict[str, list] = {}
        for f in filhos:
            cont.setdefault(nome_tag(f.tag), []).append(f)
        for tag, els in cont.items():
            if len(els) >= 2 and len(els) > len(melhores.get(tag, [])):
                melhores[tag] = els
    tabelas = []
    for tag, els in sorted(melhores.items(), key=lambda kv: -len(kv[1]))[:5]:
        regs = []
        for e in els:
            r = {nome_tag(k): v for k, v in e.attrib.items()}
            for c in e:
                if len(c) == 0:
                    r[nome_tag(c.tag)] = (c.text or "").strip()
                for k, v in c.attrib.items():
                    r[f"{nome_tag(c.tag)}_{nome_tag(k)}"] = v
            if not r and e.text and e.text.strip():
                r[tag] = e.text.strip()
            regs.append(r)
        if regs and max(len(r) for r in regs) >= 2:
            t = _registros_para_tabela(p.name, f"{p.name} › {tag}", regs)
            if t:
                tabelas.append(t)
    return tabelas


# =================================================================== HTML

class _HTMLTabelas(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tabelas, self._t, self._r, self._c = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = []
        elif tag == "tr" and self._t is not None:
            self._r = []
        elif tag in ("td", "th") and self._r is not None:
            self._c = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._c is not None:
            self._r.append(" ".join("".join(self._c).split()))
            self._c = None
        elif tag == "tr" and self._r is not None:
            self._t.append(self._r)
            self._r = None
        elif tag == "table" and self._t is not None:
            self.tabelas.append(self._t)
            self._t = None

    def handle_data(self, data):
        if self._c is not None:
            self._c.append(data)


def _ler_html(p: Path) -> list[Tabela]:
    parser = _HTMLTabelas()
    parser.feed(_decodificar(p.read_bytes()))
    out = []
    for i, t in enumerate(parser.tabelas, 1):
        tb = _montar(p.name, f"{p.name} › tabela {i}", t)
        if tb:
            out.append(tb)
    return out


# =================================================================== opcionais

def _ler_xls(p: Path) -> list[Tabela]:
    try:
        import xlrd  # type: ignore
    except ImportError:
        raise RuntimeError("arquivo .xls (Excel 97-2003) precisa do pacote xlrd:  pip install xlrd  "
                           "— ou salve como .xlsx") from None
    wb = xlrd.open_workbook(str(p))
    out = []
    for sh in wb.sheets():
        linhas = []
        for r in range(sh.nrows):
            lin = []
            for c in range(sh.ncols):
                cel = sh.cell(r, c)
                if cel.ctype == xlrd.XL_CELL_DATE:
                    lin.append(xlrd.xldate.xldate_as_datetime(cel.value, wb.datemode))
                else:
                    lin.append(cel.value)
            linhas.append(lin)
        t = _montar(p.name, f"{p.name} › {sh.name}", linhas)
        if t:
            out.append(t)
    return out


def _ler_pandas(p: Path) -> list[Tabela]:
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        raise RuntimeError(f"arquivo {p.suffix} precisa de pandas (e pyarrow):  pip install pandas pyarrow") from None
    leitor = {".parquet": pd.read_parquet, ".feather": pd.read_feather,
              ".sav": pd.read_spss, ".dta": pd.read_stata}[p.suffix.lower()]
    df = leitor(p)
    linhas = [[None if (isinstance(v, float) and v != v) else
               (v.to_pydatetime() if hasattr(v, "to_pydatetime") else v) for v in r]
              for r in df.itertuples(index=False)]
    return [Tabela(p.name, p.name, [str(c) for c in df.columns], linhas)]


# =================================================================== API

def ler_arquivo(caminho: Path) -> list[Tabela]:
    p = Path(caminho)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xlsm", ".xltx"):
        return _ler_xlsx(p)
    if ext == ".xls":
        bruto = p.read_bytes()[:8]
        if bruto.startswith(b"PK"):
            return _ler_xlsx(p)
        if bruto.lstrip().startswith((b"<", b"\xef\xbb\xbf<")):
            return _ler_html(p)  # "xls" exportado por sistemas que na verdade é HTML
        return _ler_xls(p)
    if ext == ".ods":
        return _ler_ods(p)
    if ext in (".json", ".jsonl", ".ndjson"):
        return _ler_json(p)
    if ext == ".xml":
        return _ler_xml(p)
    if ext in (".html", ".htm"):
        return _ler_html(p)
    if ext in (".parquet", ".feather", ".sav", ".dta"):
        return _ler_pandas(p)
    return _ler_csv(p)


def ler_pasta(pasta: Path) -> tuple[list[Tabela], list[str]]:
    """Lê todos os arquivos suportados (recursivo). Retorna (tabelas, erros)."""
    pasta = Path(pasta)
    arquivos = [pasta] if pasta.is_file() else sorted(
        p for p in pasta.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSOES
        and not p.name.startswith(("~$", ".")))
    tabelas, erros = [], []
    for a in arquivos:
        try:
            lidas = ler_arquivo(a)
            if not lidas:
                erros.append(f"{a.name}: nenhuma tabela encontrada")
            tabelas.extend(lidas)
        except Exception as e:  # noqa: BLE001 — reportado, não interrompe os demais
            erros.append(f"{a.name}: {e}")
    return tabelas, erros
