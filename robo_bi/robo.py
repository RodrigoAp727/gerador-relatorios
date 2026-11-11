"""Orquestrador: lê tudo, decide os painéis, valida, calcula e gera os HTML."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from . import generico, motor, seguros
from .formatos import ler_pasta
from .leitura import classificar

WEB = Path(__file__).parent / "web"


def _html(modelo: str, dados: dict) -> str:
    css = (WEB / "base.css").read_text(encoding="utf-8")
    js = (WEB / "graficos.js").read_text(encoding="utf-8")
    txt = (WEB / modelo).read_text(encoding="utf-8")
    payload = json.dumps(dados, ensure_ascii=False, separators=(",", ":"), default=str).replace("</", "<\\/")
    return txt.replace("/*__CSS__*/", css).replace("/*__JS__*/", js).replace("__DADOS__", payload)


def _relatorio_txt(titulo: str, regras: list[dict]) -> str:
    linhas = [titulo, "=" * len(titulo), ""]
    for r in sorted(regras, key=lambda r: {"ERRO": 0, "ALERTA": 1, "INFO": 2, "OK": 3}.get(r["status"], 4)):
        linhas.append(f"[{r['status']:6}] {r['grupo']} · {r['regra']} ({r['qtd']}): {r['detalhe']}")
        for a in r.get("amostra") or []:
            linhas.append(f"           - {a}")
    return "\n".join(linhas)


def executar(entrada: Path, saida: Path, params: motor.Parametros, marca: dict, log=print) -> list[Path]:
    t0 = time.time()
    saida.mkdir(parents=True, exist_ok=True)
    log(f"📂 Lendo arquivos em {entrada} ...")
    tabelas, erros = ler_pasta(entrada)
    for t in tabelas:
        log(f"   ✓ {t.nome}: {t.n:,} linhas × {len(t.cabecalho)} colunas".replace(",", "."))
    for e in erros:
        log(f"   ✕ {e}")
    if not tabelas:
        raise SystemExit("Nenhuma tabela legível encontrada.")
    gerados: list[Path] = []

    # ------------------------------------------------ 1. plano de saúde (beneficiário a beneficiário)
    por_tipo = defaultdict(list)
    for t in tabelas:
        tipo, _ = classificar(t)
        if tipo:
            por_tipo[tipo].append(t)
    usadas_saude = set()
    if all(por_tipo.get(k) for k in ("beneficiarios", "faturamento", "sinistros")):
        log("🩺 Detectado: carteira de plano de saúde (beneficiários + faturamento + sinistros)")
        try:
            dados = motor.processar(dict(por_tipo), params)
            dados["meta"]["marca"] = marca
            for k in ("beneficiarios", "faturamento", "sinistros", "contratos"):
                usadas_saude.update(id(t) for t in por_tipo.get(k, []))
            arq = saida / "BI_Saude.html"
            arq.write_text(_html("saude.html", dados), encoding="utf-8")
            (saida / "validacao_saude.txt").write_text(
                _relatorio_txt(f"Robô de validação — Saúde — {dados['meta']['status']}", dados["validacao"]),
                encoding="utf-8")
            log(f"   ✓ Validação: {dados['meta']['status']} · {len(dados['unidades'])} empresa(s) · "
                f"{len(dados['ben'])} vidas · {dados['meta']['meses'][0]} a {dados['meta']['meses'][-1]}")
            gerados.append(arq)
        except motor.ErroCritico as e:
            val = e.args[1] if len(e.args) > 1 else motor.Validador()
            (saida / "validacao_saude.txt").write_text(_relatorio_txt(f"Robô de validação — REPROVADO: {e.args[0]}",
                                                                      val.regras), encoding="utf-8")
            log(f"   ✕ BI de saúde não gerado: {e.args[0]} (ver validacao_saude.txt)")

    # ------------------------------------------------ 2. seguros multirramo
    restantes = [t for t in tabelas if id(t) not in usadas_saude]
    fato, regras_seg, perfil = seguros.montar_fato(restantes)
    extras = []
    titulo = "Painel de Inteligência de Dados"
    if fato is not None:
        log(f"🛡️  Detectado: dados de seguros — dimensões: {', '.join(perfil['dims']) or 'nenhuma'}")
        log(f"   métricas do setor: {', '.join(m['nome'] for m in perfil['metricas'])}")
        tipos = {c: "categoria" for c in perfil["dims"]}
        tipos.update({"Apólice": "id", "Nº sinistro": "id", "Origem": "categoria", "Mês": "data"})
        extras.append(generico.montar_tabela(fato, perfil, tipos))
        titulo = "Painel de Inteligência de Seguros"

    # ------------------------------------------------ 3. BI geral (qualquer área) com todas as bases
    log("📊 Montando o BI automático de todas as bases ...")
    dados_g = generico.montar(tabelas, titulo, erros, extras=extras, regras_extra=regras_seg)
    dados_g["meta"]["marca"] = marca
    for tb in dados_g["tabelas"]:
        log(f"   • {tb['nome']}: data={tb['data'] or '—'} · categorias={len(tb['categorias'])} · "
            f"métricas={len(tb['specs'])}")
    nome = "BI_Seguros.html" if fato is not None else "BI_Geral.html"
    arq = saida / nome
    arq.write_text(_html("bi.html", dados_g), encoding="utf-8")
    regras_todas = dados_g["validacao_geral"] + [r for tb in dados_g["tabelas"] for r in tb["validacao"]]
    (saida / "validacao_geral.txt").write_text(_relatorio_txt("Robô de validação — bases gerais", regras_todas),
                                               encoding="utf-8")
    gerados.append(arq)
    log(f"✅ Pronto em {time.time() - t0:.1f}s")
    for g in gerados:
        log(f"   → {g}  ({g.stat().st_size / 1e6:.1f} MB)")
    return gerados
