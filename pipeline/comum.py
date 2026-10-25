"""Caminhos, grade H3 e conversão de raster em valores por hexágono."""
import json
import os
from pathlib import Path

import h3
import numpy as np
import pandas as pd
from pyproj import Transformer

# PLANTIO_RAIZ permite rodar os testes numa pasta separada, sem tocar nos dados reais.
RAIZ = Path(os.getenv("PLANTIO_RAIZ", Path(__file__).resolve().parent.parent))
ENTRADA = RAIZ / "dados" / "entrada"
INTER = RAIZ / "dados" / "intermediario"
SAIDA = RAIZ / "public" / "data"
for p in (ENTRADA, INTER, SAIDA):
    p.mkdir(parents=True, exist_ok=True)

RES = 10  # H3 resolução 10: hexágonos de ~15 mil m², cerca de 120 m de diâmetro
AREA_HEX_M2 = h3.average_hexagon_area(RES, unit="m^2")
COD_MANAUS = "1302603"


def log(msg):
    print(msg, flush=True)


def carregar_grade() -> pd.DataFrame:
    p = INTER / "grade.parquet"
    if not p.exists():
        raise SystemExit("Grade não encontrada. Rode antes: python pipeline/01_grade.py")
    return pd.read_parquet(p)


def salvar(df: pd.DataFrame, nome: str):
    p = INTER / nome
    df.to_parquet(p, index=False)
    log(f"  gravado {p.relative_to(RAIZ)} ({len(df)} linhas)")


# ---------- raster → hexágono ----------
_cache_celulas = {}
_cache_dentro = {}


def celulas_dos_pixels(transform, crs, shape):
    """Índice H3 de cada pixel de uma janela raster. Fica em cache por (crs, transform, shape):
    cenas da mesma grade de satélite reaproveitam o cálculo."""
    chave = (str(crs), tuple(round(v, 6) for v in transform[:6]), shape)
    if chave in _cache_celulas:
        return _cache_celulas[chave]
    linhas, colunas = shape
    cols, rows = np.meshgrid(np.arange(colunas) + 0.5, np.arange(linhas) + 0.5)
    xs = transform.c + cols * transform.a + rows * transform.b
    ys = transform.f + cols * transform.d + rows * transform.e
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs.ravel(), ys.ravel())
    cel = np.array([h3.latlng_to_cell(la, lo, RES) for la, lo in zip(lat, lon)], dtype=object)
    _cache_celulas[chave] = cel
    return cel


def agregar_por_hex(colunas: dict, transform, crs, shape, celulas_validas: set) -> pd.DataFrame:
    """colunas: nome → array 2D (NaN = pixel inválido). Devolve um DataFrame com uma linha por pixel
    dentro da grade, pronto para groupby('h3')."""
    cel = celulas_dos_pixels(transform, crs, shape)
    chave = (id(cel), len(celulas_validas))
    if chave not in _cache_dentro:
        _cache_dentro[chave] = np.fromiter((c in celulas_validas for c in cel), dtype=bool, count=cel.size)
    dentro = _cache_dentro[chave]
    df = pd.DataFrame({"h3": cel[dentro]})
    for nome, arr in colunas.items():
        df[nome] = np.asarray(arr, dtype="float32").ravel()[dentro]
    return df


def bbox_da_grade(grade: pd.DataFrame, margem=0.005):
    return [grade.lon.min() - margem, grade.lat.min() - margem, grade.lon.max() + margem, grade.lat.max() + margem]


def poligono_hex(cel):
    """Contorno do hexágono em (lon, lat), para geopandas."""
    from shapely.geometry import Polygon
    return Polygon([(lo, la) for la, lo in h3.cell_to_boundary(cel)])


def gravar_json(nome, obj):
    (SAIDA / nome).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
