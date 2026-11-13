"""Tela de envio do Robô BI.

    python servidor_bi.py            → abre http://127.0.0.1:8765 no navegador

Roda só no seu computador (127.0.0.1): os arquivos não vão para a internet.
Cada envio vira uma pasta em relatorios_bi/AAAAMMDD_HHMMSS/ com os painéis e a validação.
O acesso exige login: na primeira vez, crie a conta na própria tela (aba "Criar conta").
Usuários e sessões ficam em dados_bi/acesso.db.
"""
from __future__ import annotations

import json
import mimetypes
import re
import sys
import threading
import webbrowser
import zipfile
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as politica
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from robo_bi.acesso import SESSAO_HORAS, Acesso, ErroAcesso  # noqa: E402
from robo_bi.formatos import EXTENSOES  # noqa: E402
from robo_bi.motor import Parametros  # noqa: E402
from robo_bi.robo import WEB, executar  # noqa: E402

RAIZ = Path(__file__).resolve().parent
SAIDA = RAIZ / "relatorios_bi"
PORTA = 8765
LIMITE_UPLOAD = 2 * 1024 ** 3  # 2 GB por envio
LIMITE_JSON = 16 * 1024
COOKIE = "robo_bi_sessao"
ROTAS_LIVRES = {"/login", "/api/login", "/api/cadastro"}
TRABALHOS: dict[str, dict] = {}
ACESSO: Acesso | None = None  # criado no main() (ou pelos testes)


def _nome_seguro(nome: str) -> str:
    nome = Path(nome.replace("\\", "/")).name
    nome = re.sub(r"[^\w.\- ()À-ÿ]", "_", nome).strip(" .") or "arquivo"
    return nome[:150]


def _arquivos_do_trabalho(pasta: Path) -> list[dict]:
    if not pasta.exists():
        return []
    ordem = {"BI_Saude.html": 0, "BI_Seguros.html": 1, "BI_Geral.html": 2}
    arqs = [p for p in pasta.iterdir() if p.suffix in (".html", ".txt")]
    arqs.sort(key=lambda p: (ordem.get(p.name, 5), p.name))
    return [{"nome": p.name, "url": f"/relatorios/{pasta.name}/{p.name}"} for p in arqs]


def _processar(job_id: str, entrada: Path, saida: Path, params: Parametros):
    job = TRABALHOS[job_id]

    def log(msg):
        job["log"].append(str(msg))

    try:
        # descompacta .zip enviados
        for z in list(entrada.glob("*.zip")):
            with zipfile.ZipFile(z) as zf:
                destino = entrada / z.stem
                for info in zf.infolist():
                    alvo = (destino / info.filename).resolve()
                    if info.is_dir() or not str(alvo).startswith(str(destino.resolve())):
                        continue
                    if Path(info.filename).suffix.lower() in EXTENSOES:
                        alvo.parent.mkdir(parents=True, exist_ok=True)
                        alvo.write_bytes(zf.read(info))
            log(f"📦 {z.name} descompactado")
            z.unlink()
        executar(entrada, saida, params, {"nome": "Robô BI", "sub": "Inteligência de dados"}, log=log)
        job["status"] = "ok"
    except SystemExit as e:
        log(f"✕ {e}")
        job["status"] = "erro"
    except Exception as e:  # noqa: BLE001
        log(f"✕ Erro inesperado: {type(e).__name__}: {e}")
        job["status"] = "erro"
    (saida / "envio.json").write_text(json.dumps({
        "quando": job["quando"], "entradas": job["entradas"], "status": job["status"], "log": job["log"]},
        ensure_ascii=False, indent=1), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "RoboBI/1.0"

    def log_message(self, fmt, *args):  # silencia o log padrão
        pass

    def _json(self, obj, code=200):
        dados = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    # ---------- sessão ----------
    def _token(self) -> str | None:
        for item in (self.headers.get("Cookie") or "").split(";"):
            nome, _, valor = item.strip().partition("=")
            if nome == COOKIE and valor:
                return valor
        return None

    def _usuario(self) -> dict | None:
        return ACESSO.usuario_da_sessao(self._token())

    def _cookie(self, token: str, idade: int) -> str:
        return f"{COOKIE}={token}; Max-Age={idade}; Path=/; HttpOnly; SameSite=Strict"

    def _redirecionar(self, destino: str):
        self.send_response(303)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _pagina(self, arquivo: str, troca: dict[str, str] | None = None):
        css = (WEB / "base.css").read_text(encoding="utf-8")
        texto = (WEB / arquivo).read_text(encoding="utf-8").replace("/*__CSS__*/", css)
        for chave, valor in (troca or {}).items():
            texto = texto.replace(chave, valor)
        html = texto.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def _ler_json(self) -> dict:
        tamanho = int(self.headers.get("Content-Length", 0) or 0)
        if tamanho <= 0 or tamanho > LIMITE_JSON:
            raise ErroAcesso("Envio inválido.")
        try:
            dados = json.loads(self.rfile.read(tamanho).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ErroAcesso("Envio inválido.") from None
        if not isinstance(dados, dict):
            raise ErroAcesso("Envio inválido.")
        return {k: v if isinstance(v, str) else "" for k, v in dados.items()}

    def _login_ou_cadastro(self, caminho: str):
        try:
            d = self._ler_json()
            ip = self.client_address[0]
            if caminho == "/api/cadastro":
                if d.get("senha") != d.get("confirma"):
                    raise ErroAcesso("As senhas não coincidem.")
                ACESSO.cadastrar(d.get("nome", ""), d.get("email", ""), d.get("senha", ""), d.get("empresa", ""), ip=ip)
            token, usuario = ACESSO.entrar(d.get("email", ""), d.get("senha", ""), ip=ip)
        except ErroAcesso as e:
            return self._json({"erro": str(e)}, e.codigo)
        dados = json.dumps({"usuario": usuario}, ensure_ascii=False).encode("utf-8")
        self.send_response(201 if caminho == "/api/cadastro" else 200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", self._cookie(token, SESSAO_HORAS * 3600))
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        caminho = self.path.split("?")[0]
        usuario = self._usuario()
        if caminho == "/login":
            return self._redirecionar("/") if usuario else self._pagina("login.html")
        if not usuario:
            if caminho.startswith("/api/"):
                return self._json({"erro": "Sessão expirada. Entre novamente."}, 401)
            return self._redirecionar("/login")
        if caminho in ("/", "/index.html"):
            from html import escape
            self._pagina("upload.html", {"__USUARIO__": escape(usuario["nome"])})
        elif caminho == "/api/eu":
            self._json(usuario)
        elif caminho.startswith("/api/status/"):
            job = TRABALHOS.get(caminho.rsplit("/", 1)[-1])
            if not job:
                return self._json({"erro": "processamento não encontrado"}, 404)
            self._json({"status": job["status"], "log": job["log"],
                        "arquivos": _arquivos_do_trabalho(job["saida"]) if job["status"] != "rodando" else []})
        elif caminho == "/api/historico":
            itens = []
            for p in sorted((x for x in SAIDA.glob("*/envio.json")), reverse=True)[:30]:
                try:
                    info = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                itens.append({"quando": info.get("quando", p.parent.name), "entradas": info.get("entradas", []),
                              "arquivos": _arquivos_do_trabalho(p.parent)})
            self._json(itens)
        elif caminho.startswith("/relatorios/"):
            from urllib.parse import unquote
            alvo = (SAIDA / unquote(caminho[len("/relatorios/"):])).resolve()
            if not str(alvo).startswith(str(SAIDA.resolve())) or not alvo.is_file():
                self.send_error(404)
                return
            dados = alvo.read_bytes()
            tipo = mimetypes.guess_type(alvo.name)[0] or "application/octet-stream"
            if tipo.startswith("text/"):
                tipo += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)
        else:
            self.send_error(404)

    def do_POST(self):
        caminho = self.path.split("?")[0]
        if caminho in ROTAS_LIVRES:
            return self._login_ou_cadastro(caminho)
        if caminho == "/api/sair":
            ACESSO.sair(self._token())
            self.send_response(204)
            self.send_header("Set-Cookie", self._cookie("", 0))
            self.end_headers()
            return
        if not self._usuario():
            return self._json({"erro": "Sessão expirada. Entre novamente."}, 401)
        if caminho != "/api/processar":
            return self.send_error(404)
        tamanho = int(self.headers.get("Content-Length", 0))
        if tamanho <= 0 or tamanho > LIMITE_UPLOAD:
            return self._json({"erro": "Envio vazio ou acima de 2 GB"}, 400)
        tipo = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in tipo:
            return self._json({"erro": "Formato de envio inválido"}, 400)
        corpo = self.rfile.read(tamanho)
        msg = BytesParser(policy=politica).parsebytes(
            b"Content-Type: " + tipo.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + corpo)
        job_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        saida = SAIDA / job_id
        entrada = saida / "_entrada"
        entrada.mkdir(parents=True, exist_ok=True)
        campos, entradas = {}, []
        for parte in msg.iter_parts():
            nome_campo = parte.get_param("name", header="content-disposition")
            arquivo = parte.get_filename()
            if arquivo:
                nome = _nome_seguro(arquivo)
                destino = entrada / nome
                k = 1
                while destino.exists():
                    destino = entrada / f"{Path(nome).stem}_{k}{Path(nome).suffix}"
                    k += 1
                destino.write_bytes(parte.get_payload(decode=True) or b"")
                entradas.append(destino.name)
            elif nome_campo:
                campos[nome_campo] = (parte.get_payload(decode=True) or b"").decode("utf-8", "replace").strip()
        if not entradas:
            return self._json({"erro": "Nenhum arquivo recebido"}, 400)

        def num(c, padrao):
            try:
                return float(campos.get(c, "").replace(",", ".")) if campos.get(c) else padrao
            except ValueError:
                return padrao

        params = Parametros(corretora=campos.get("nome") or "Relatório", limite_padrao=num("limite", 75) / 100,
                            vcmh_padrao=num("vcmh", 0) / 100, idade_limite_dependente=int(num("idade_dep", 24)))
        TRABALHOS[job_id] = {"status": "rodando", "log": [f"📥 {len(entradas)} arquivo(s) recebido(s)"],
                             "saida": saida, "entradas": entradas,
                             "quando": datetime.now().strftime("%d/%m/%Y %H:%M")}
        threading.Thread(target=_processar, args=(job_id, entrada, saida, params), daemon=True).start()
        self._json({"id": job_id})


def main():
    global ACESSO
    SAIDA.mkdir(exist_ok=True)
    ACESSO = Acesso(RAIZ / "dados_bi" / "acesso.db")
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    url = f"http://127.0.0.1:{PORTA}"
    print(f"Robô BI rodando em {url}  (feche esta janela para encerrar)")
    if "--sem-navegador" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
