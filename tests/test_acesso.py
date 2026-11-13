"""Testes do login/cadastro do Robô BI.

Rodar:  python -m pytest tests/test_acesso.py -q
"""
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import servidor_bi  # noqa: E402
from robo_bi.acesso import Acesso, ErroAcesso, conferir_senha, gerar_hash  # noqa: E402


def _acesso():
    return Acesso(Path(tempfile.mkdtemp(prefix="robo_bi_acesso_")) / "acesso.db")


def test_hash_de_senha():
    h = gerar_hash("segredo123")
    assert "segredo123" not in h
    assert conferir_senha("segredo123", h)
    assert not conferir_senha("segredo124", h)
    assert gerar_hash("segredo123") != h  # sal diferente a cada vez
    assert not conferir_senha("x", "lixo")


def test_cadastro_login_sessao_e_sair():
    a = _acesso()
    u = a.cadastrar("Ana", "  Ana@Exemplo.com ", "senhaforte1", "Empresa X")
    assert u["email"] == "ana@exemplo.com"
    token, usuario = a.entrar("ANA@exemplo.com", "senhaforte1")
    assert usuario["nome"] == "Ana"
    assert a.usuario_da_sessao(token)["email"] == "ana@exemplo.com"
    assert a.usuario_da_sessao("token-inventado") is None
    a.sair(token)
    assert a.usuario_da_sessao(token) is None


def test_validacoes_do_cadastro():
    a = _acesso()
    with pytest.raises(ErroAcesso, match="8 caracteres"):
        a.cadastrar("Ana", "ana@x.com", "curta")
    with pytest.raises(ErroAcesso, match="e-mail válido"):
        a.cadastrar("Ana", "sem-arroba", "senhaforte1")
    with pytest.raises(ErroAcesso, match="nome"):
        a.cadastrar("", "ana@x.com", "senhaforte1")
    a.cadastrar("Ana", "ana@x.com", "senhaforte1")
    with pytest.raises(ErroAcesso, match="já está cadastrado") as e:
        a.cadastrar("Outra", "ANA@x.com", "senhaforte2")
    assert e.value.codigo == 409


def test_login_errado_mesma_mensagem_e_limite():
    a = _acesso()
    a.cadastrar("Ana", "ana@x.com", "senhaforte1")
    msgs = set()
    for email in ("ana@x.com", "naoexiste@x.com"):
        with pytest.raises(ErroAcesso) as e:
            a.entrar(email, "errada123", ip="1.1.1.1")
        msgs.add(str(e.value))
    assert len(msgs) == 1  # não revela se o e-mail existe
    for _ in range(5):
        with pytest.raises(ErroAcesso) as e:
            a.entrar("ana@x.com", "errada123", ip="2.2.2.2")
        assert e.value.codigo == 401
    with pytest.raises(ErroAcesso) as e:  # 6ª tentativa: bloqueada mesmo com a senha certa
        a.entrar("ana@x.com", "senhaforte1", ip="2.2.2.2")
    assert e.value.codigo == 429
    # outro IP continua entrando normalmente (a conta não é travada)
    assert a.entrar("ana@x.com", "senhaforte1", ip="3.3.3.3")[0]


@pytest.fixture()
def servidor():
    servidor_bi.ACESSO = _acesso()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), servidor_bi.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _req(url, metodo="GET", dados=None, cookie=None):
    corpo = json.dumps(dados).encode() if dados is not None else None
    r = urllib.request.Request(url, data=corpo, method=metodo)
    if corpo:
        r.add_header("Content-Type", "application/json")
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        resp = urllib.request.build_opener(_SemRedirecionar).open(r)
    except urllib.error.HTTPError as e:
        resp = e
    return resp.status, dict(resp.headers), resp.read().decode("utf-8")


def test_servidor_exige_login(servidor):
    st, cab, _ = _req(servidor + "/")
    assert st == 303 and cab["Location"] == "/login"
    assert _req(servidor + "/api/historico")[0] == 401
    assert _req(servidor + "/relatorios/x/BI_Geral.html")[0] in (303, 401)
    assert _req(servidor + "/api/processar", "POST", {"x": 1})[0] == 401
    st, _, html = _req(servidor + "/login")
    assert st == 200 and "Criar conta" in html


def test_servidor_cadastro_entra_e_sai(servidor):
    novo = {"nome": "Ana <b>", "email": "ana@x.com", "senha": "senhaforte1", "confirma": "senhaforte1"}
    st, _, corpo = _req(servidor + "/api/cadastro", "POST", {**novo, "confirma": "outra123"})
    assert st == 400 and "não coincidem" in corpo
    st, cab, _ = _req(servidor + "/api/cadastro", "POST", novo)
    assert st == 201
    assert "HttpOnly" in cab["Set-Cookie"] and "SameSite=Strict" in cab["Set-Cookie"]
    cookie = cab["Set-Cookie"].split(";")[0]
    st, _, html = _req(servidor + "/", cookie=cookie)
    assert st == 200 and "Ana &lt;b&gt;" in html and "Ana <b>" not in html
    assert _req(servidor + "/api/historico", cookie=cookie)[0] == 200
    assert _req(servidor + "/login", cookie=cookie)[0] == 303  # já logado → vai pro sistema

    assert _req(servidor + "/api/cadastro", "POST", novo)[0] == 409
    assert _req(servidor + "/api/login", "POST", {"email": "ana@x.com", "senha": "errada123"})[0] == 401
    st, cab, _ = _req(servidor + "/api/login", "POST", {"email": "ana@x.com", "senha": "senhaforte1"})
    assert st == 200
    cookie2 = cab["Set-Cookie"].split(";")[0]

    assert _req(servidor + "/api/sair", "POST", cookie=cookie2)[0] == 204
    assert _req(servidor + "/api/historico", cookie=cookie2)[0] == 401
    assert _req(servidor + "/api/historico", cookie=cookie)[0] == 200  # a outra sessão segue válida
