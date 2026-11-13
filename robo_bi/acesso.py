"""Login e cadastro do Robô BI.

Usuários e sessões ficam num SQLite local (dados_bi/acesso.db), só na biblioteca padrão:
- senha guardada com scrypt + sal aleatório (nunca em texto puro);
- sessão = token aleatório no cookie HttpOnly; no banco fica só o hash dele;
- e-mail inexistente compara contra um hash falso, para o tempo de resposta não
  revelar quais e-mails têm conta;
- limite de tentativas por IP e por IP+e-mail (sem travar a conta, para ninguém
  bloquear o acesso de outra pessoa só errando a senha).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

SENHA_MINIMA = 8
SESSAO_HORAS = 12
JANELA_TENTATIVAS = 15 * 60
LIMITE_POR_IP = 20
LIMITE_POR_IP_EMAIL = 5
LIMITE_CADASTRO_POR_IP = 10
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class ErroAcesso(Exception):
    """Erro com mensagem pronta para mostrar na tela."""

    def __init__(self, mensagem: str, codigo: int = 400):
        super().__init__(mensagem)
        self.codigo = codigo


def gerar_hash(senha: str) -> str:
    sal = secrets.token_bytes(16)
    chave = hashlib.scrypt(senha.encode("utf-8"), salt=sal, **_SCRYPT)
    return f"scrypt${sal.hex()}${chave.hex()}"


def conferir_senha(senha: str, guardado: str) -> bool:
    try:
        _, sal, chave = guardado.split("$")
        calculada = hashlib.scrypt(senha.encode("utf-8"), salt=bytes.fromhex(sal), **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculada.hex(), chave)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Acesso:
    def __init__(self, banco: Path):
        banco.parent.mkdir(parents=True, exist_ok=True)
        self._banco = banco
        self._trava = threading.Lock()
        self._tentativas: dict[str, list[float]] = {}
        self._hash_falso = gerar_hash(secrets.token_hex(16))
        with self._conexao() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    empresa TEXT NOT NULL DEFAULT '',
                    senha_hash TEXT NOT NULL,
                    criado_em REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessoes (
                    token_hash TEXT PRIMARY KEY,
                    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                    expira_em REAL NOT NULL
                );
            """)

    def _conexao(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._banco, timeout=10)
        c.execute("PRAGMA foreign_keys = ON")
        return c

    # ---------- limite de tentativas ----------
    def _estourou(self, chave: str, limite: int) -> bool:
        agora = time.time()
        with self._trava:
            lista = [t for t in self._tentativas.get(chave, []) if agora - t < JANELA_TENTATIVAS]
            self._tentativas[chave] = lista
            return len(lista) >= limite

    def _registrar(self, chave: str):
        with self._trava:
            self._tentativas.setdefault(chave, []).append(time.time())

    # ---------- cadastro ----------
    def cadastrar(self, nome: str, email: str, senha: str, empresa: str = "", ip: str = "") -> dict:
        nome, empresa = (nome or "").strip(), (empresa or "").strip()
        email = (email or "").strip().lower()
        senha = senha or ""
        if self._estourou(f"cad|{ip}", LIMITE_CADASTRO_POR_IP):
            raise ErroAcesso("Muitas tentativas. Aguarde alguns minutos e tente de novo.", 429)
        self._registrar(f"cad|{ip}")
        if len(nome) < 2 or len(nome) > 150:
            raise ErroAcesso("Informe seu nome.")
        if not _EMAIL.match(email) or len(email) > 254:
            raise ErroAcesso("Informe um e-mail válido.")
        if len(senha) < SENHA_MINIMA:
            raise ErroAcesso(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.")
        if len(senha) > 200 or len(empresa) > 150:
            raise ErroAcesso("Dados acima do tamanho permitido.")
        try:
            with self._conexao() as c:
                cur = c.execute(
                    "INSERT INTO usuarios (nome, email, empresa, senha_hash, criado_em) VALUES (?, ?, ?, ?, ?)",
                    (nome, email, empresa, gerar_hash(senha), time.time()))
                uid = cur.lastrowid
        except sqlite3.IntegrityError:
            raise ErroAcesso("Este e-mail já está cadastrado. Use outro ou entre com ele.", 409) from None
        return {"id": uid, "nome": nome, "email": email, "empresa": empresa}

    # ---------- login / sessão ----------
    def entrar(self, email: str, senha: str, ip: str = "") -> tuple[str, dict]:
        email = (email or "").strip().lower()
        senha = senha or ""
        if self._estourou(f"ip|{ip}", LIMITE_POR_IP) or self._estourou(f"ipe|{ip}|{email}", LIMITE_POR_IP_EMAIL):
            raise ErroAcesso("Muitas tentativas. Aguarde alguns minutos e tente de novo.", 429)
        with self._conexao() as c:
            linha = c.execute("SELECT id, nome, email, empresa, senha_hash FROM usuarios WHERE email = ?",
                              (email,)).fetchone()
        ok = conferir_senha(senha, linha[4] if linha else self._hash_falso)
        if not linha or not ok:
            self._registrar(f"ip|{ip}")
            self._registrar(f"ipe|{ip}|{email}")
            raise ErroAcesso("E-mail ou senha inválidos.", 401)
        token = secrets.token_urlsafe(32)
        with self._conexao() as c:
            c.execute("DELETE FROM sessoes WHERE expira_em < ?", (time.time(),))
            c.execute("INSERT INTO sessoes (token_hash, usuario_id, expira_em) VALUES (?, ?, ?)",
                      (_hash_token(token), linha[0], time.time() + SESSAO_HORAS * 3600))
        return token, {"id": linha[0], "nome": linha[1], "email": linha[2], "empresa": linha[3]}

    def usuario_da_sessao(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._conexao() as c:
            linha = c.execute(
                "SELECT u.id, u.nome, u.email, u.empresa FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id "
                "WHERE s.token_hash = ? AND s.expira_em > ?", (_hash_token(token), time.time())).fetchone()
        if not linha:
            return None
        return {"id": linha[0], "nome": linha[1], "email": linha[2], "empresa": linha[3]}

    def sair(self, token: str | None):
        if token:
            with self._conexao() as c:
                c.execute("DELETE FROM sessoes WHERE token_hash = ?", (_hash_token(token),))
