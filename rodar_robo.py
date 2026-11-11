"""Robô de BI — uso:

    python rodar_robo.py PASTA_OU_ARQUIVO [--saida relatorios_bi] [--corretora "Minha Corretora"]
    python rodar_robo.py --exemplo           # gera dados fictícios e roda os 3 modos

Lê CSV, TXT, XLSX/XLSM (todas as abas), XLS*, ODS, JSON, XML, HTML, Parquet*.
(* precisam de pacote extra: xlrd / pandas+pyarrow)
"""
import argparse
import sys
import webbrowser
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from robo_bi.motor import Parametros  # noqa: E402
from robo_bi.robo import executar  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Robô de BI: lê arquivos, valida, cruza métricas e gera painéis.")
    ap.add_argument("entrada", nargs="?", help="pasta (lida recursivamente) ou arquivo")
    ap.add_argument("--saida", default="relatorios_bi", help="pasta de saída (padrão: relatorios_bi)")
    ap.add_argument("--corretora", default="Corretora", help="nome exibido no cabeçalho do BI de saúde")
    ap.add_argument("--marca", default="Robô BI", help="nome da marca no logo")
    ap.add_argument("--marca-sub", default="Inteligência de dados", help="subtítulo da marca")
    ap.add_argument("--limite", type=float, default=75, help="limite de sinistralidade padrão em %% (saúde)")
    ap.add_argument("--vcmh", type=float, default=0, help="VCMH padrão em %% para reajuste indicativo (saúde)")
    ap.add_argument("--idade-dependente", type=int, default=24, help="idade limite de dependentes (saúde)")
    ap.add_argument("--exemplo", action="store_true", help="gera dados fictícios em exemplos/ e processa")
    ap.add_argument("--abrir", action="store_true", help="abre os painéis no navegador ao final")
    a = ap.parse_args()

    params = Parametros(corretora=a.corretora, limite_padrao=a.limite / 100, vcmh_padrao=a.vcmh / 100,
                        idade_limite_dependente=a.idade_dependente)
    marca = {"nome": a.marca, "sub": a.marca_sub}
    if a.exemplo:
        from robo_bi.exemplo import gerar_todos
        base = Path("exemplos")
        gerar_todos(base)
        params.corretora = "Corretora Exemplo · números fictícios"
        gerados = []
        for pasta in ("saude", "seguradora", "vendas"):
            print(f"\n================ EXEMPLO: {pasta} ================")
            gerados += executar(base / pasta, Path(a.saida) / pasta, params, marca)
    else:
        if not a.entrada:
            ap.error("informe a pasta/arquivo de entrada ou use --exemplo")
        gerados = executar(Path(a.entrada), Path(a.saida), params, marca)
    if a.abrir:
        for g in gerados:
            webbrowser.open(g.resolve().as_uri())


if __name__ == "__main__":
    main()
