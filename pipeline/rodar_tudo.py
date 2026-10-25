"""Roda o pipeline inteiro, em ordem.

    python pipeline/rodar_tudo.py                 # todas as etapas
    python pipeline/rodar_tudo.py --a-partir-de 4 # retoma da etapa 4 (as anteriores já rodaram)
    python pipeline/rodar_tudo.py --so 7          # só o índice (por exemplo, para testar outros pesos)

Argumentos extras depois de -- vão para a etapa 7 (índice), por exemplo:
    python pipeline/rodar_tudo.py --so 7 -- --peso-calor 0.5 --peso-verde 0.3 --peso-pessoas 0.2
"""
import argparse
import importlib.util
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
ETAPAS = [
    (1, "01_grade", "Grade de hexágonos"),
    (2, "02_calor", "Calor de superfície (Landsat)"),
    (3, "03_vegetacao", "Vegetação (Sentinel-2)"),
    (4, "04_populacao", "População (Censo 2022)"),
    (5, "05_viabilidade", "Espaço e restrições (OpenStreetMap)"),
    (6, "06_copas", "Copas de árvores (CHMv2)"),
    (7, "07_indice", "Índice de prioridade"),
    (8, "08_web", "Dados compactos para o site"),
]


def carregar(nome):
    spec = importlib.util.spec_from_file_location(nome, AQUI / f"{nome}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    argv = sys.argv[1:]
    extras = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--a-partir-de", type=int, default=1)
    ap.add_argument("--so", type=int)
    a = ap.parse_args(argv)
    for n, nome, titulo in ETAPAS:
        if (a.so and n != a.so) or (not a.so and n < a.a_partir_de):
            continue
        print(f"\n=== Etapa {n}: {titulo} ===", flush=True)
        t0 = time.time()
        modulo = carregar(nome)
        modulo.main(extras if n == 7 else [])
        print(f"--- etapa {n} concluída em {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
