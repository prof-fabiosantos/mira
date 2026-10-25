"""Etapa 6 — copas de árvores por hexágono (CHMv2 da Meta/WRI, ~1,2 m).

O CHMv2 é um mapa global de altura das copas, gerado por um modelo de visão computacional (DINOv3)
sobre imagens de satélite de alta resolução. Aberto (CC BY 4.0), lido direto do Source Cooperative:
https://source.coop/tge-labs/meta-chm-v2 . Só o recorte de Manaus é lido (requisições por faixa).

Por hexágono:
  copa         fração de pixels com altura ≥ 3 m (árvore; abaixo disso é gramado, arbusto ou chão)
  altura_copa  altura média dos pixels de copa (m)

As imagens de origem em Manaus são de 2010 a 2019 (variam por tile). Por isso a copa NÃO substitui
o NDVI recente no índice: ela refina a exclusão de "vegetação densa" e indica provável perda de copa.

Saídas: dados/intermediario/copas.parquet (h3, copa, altura_copa)
        dados/intermediario/copas_tiles.csv (quadkey e período das imagens de origem)
"""
import argparse
import os
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import requests
from pyproj import Transformer
from rasterio.windows import Window, from_bounds

from comum import INTER, RAIZ, RES, bbox_da_grade, carregar_grade, log, salvar

BASE = "https://data.source.coop/tge-labs/meta-chm-v2/"
CACHE = RAIZ / "cache" / "chmv2"
ALTURA_COPA = 3.0
F = 16          # agrega 16×16 pixels (~19 m) antes de atribuir ao hexágono
BLOCO = 4096    # pixels por bloco de leitura (múltiplo de F)

for k, v in {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
             "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
             "GDAL_HTTP_MAX_RETRY": "4", "GDAL_HTTP_RETRY_DELAY": "2", "AWS_NO_SIGN_REQUEST": "YES"}.items():
    os.environ.setdefault(k, v)


def obter(nome, fonte):
    if fonte:
        return Path(fonte) / nome
    destino = CACHE / nome
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        log(f"  baixando {nome}…")
        with requests.get(BASE + nome, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = destino.with_suffix(".part")
            with open(tmp, "wb") as f:
                for parte in r.iter_content(1 << 20):
                    f.write(parte)
            tmp.replace(destino)
    return destino


def caminho_gdal(url, fonte):
    url = str(url)
    if fonte and not url.startswith(("s3://", "http")):
        p = Path(url)
        return str(p if p.is_absolute() else Path(fonte) / p)
    if url.startswith("s3://us-west-2.opendata.source.coop/"):
        return "/vsicurl/https://data.source.coop/" + url.split("opendata.source.coop/", 1)[1]
    if url.startswith("s3://"):
        os.environ.setdefault("AWS_REGION", "us-west-2")
        return "/vsis3/" + url[5:]
    if url.startswith("http"):
        return "/vsicurl/" + url
    return "/vsicurl/" + BASE + url.lstrip("/")


def tiles_na_area(bbox, fonte):
    tab = pq.read_table(obter("tiles.parquet", fonte)).flatten().to_pandas()
    tab.columns = [c.replace(".", "_") for c in tab.columns]
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x0, y0 = t.transform(bbox[0], bbox[1])
    x1, y1 = t.transform(bbox[2], bbox[3])
    sel = tab[(tab.bbox_3857_minx < x1) & (tab.bbox_3857_maxx > x0) & (tab.bbox_3857_miny < y1) & (tab.bbox_3857_maxy > y0)]
    return sel, (x0, y0, x1, y1)


def datas(quadkeys, fonte):
    try:
        d = pq.read_table(obter("acq_dates.parquet", fonte)).to_pandas()
        col = next(c for c in d.columns if "quadkey" in c.lower())
        d = d[d[col].astype(str).isin([str(q) for q in quadkeys])].rename(columns={col: "quadkey"})
        return d.rename(columns={c: c.replace("acq_", "") for c in d.columns})
    except Exception as e:
        log(f"  (datas das imagens indisponíveis: {e})")
        return pd.DataFrame({"quadkey": list(quadkeys)})


def celulas(transform, crs, shape):
    linhas, colunas = shape
    cols, rows = np.meshgrid(np.arange(colunas) + 0.5, np.arange(linhas) + 0.5)
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(
        (transform.c + cols * transform.a).ravel(), (transform.f + rows * transform.e).ravel())
    return np.array([h3.latlng_to_cell(la, lo, RES) for la, lo in zip(lat, lon)], dtype=object)


def processar_tile(caminho, area_3857, validas):
    partes = []
    with rasterio.open(caminho) as src:
        win = from_bounds(*area_3857, transform=src.transform)
        win = win.round_offsets().round_lengths().intersection(Window(0, 0, src.width, src.height))
        c0, r0 = int(win.col_off) // F * F, int(win.row_off) // F * F
        c1, r1 = int(win.col_off + win.width), int(win.row_off + win.height)
        n = ((r1 - r0 + BLOCO - 1) // BLOCO) * ((c1 - c0 + BLOCO - 1) // BLOCO)
        k = 0
        for r in range(r0, r1, BLOCO):
            for c in range(c0, c1, BLOCO):
                k += 1
                h, w = min(BLOCO, r1 - r) // F * F, min(BLOCO, c1 - c) // F * F
                if h == 0 or w == 0:
                    continue
                janela = Window(c, r, w, h)
                a = src.read(1, window=janela).astype(np.float32)
                copa = a >= ALTURA_COPA
                bl = lambda x: x.reshape(h // F, F, w // F, F).sum(axis=(1, 3))
                n_copa, soma_alt = bl(copa), bl(np.where(copa, a, 0))
                cel = celulas(src.window_transform(janela) * rasterio.Affine.scale(F), src.crs, n_copa.shape)
                dentro = np.fromiter((x in validas for x in cel), bool, cel.size)
                if dentro.any():
                    partes.append(pd.DataFrame({"h3": cel[dentro], "n_px": F * F,
                                                "n_copa": n_copa.ravel()[dentro], "soma_alt": soma_alt.ravel()[dentro]}))
                if k % 10 == 0 or k == n:
                    log(f"    bloco {k}/{n}")
    return partes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonte", help=argparse.SUPPRESS)  # pasta local com tiles.parquet e COGs (testes)
    a = ap.parse_args(argv)
    fonte = a.fonte or os.getenv("PLANTIO_CHM_FONTE")

    grade = carregar_grade()
    validas = set(grade.h3)
    tiles, area = tiles_na_area(bbox_da_grade(grade, margem=0.002), fonte)
    if tiles.empty:
        raise SystemExit("Nenhum tile do CHMv2 cobre a área.")
    log(f"  {len(tiles)} tile(s) do CHMv2: {', '.join(map(str, tiles.quadkey))}")
    d = datas(tiles.quadkey.tolist(), fonte)
    log("  imagens de origem:\n" + d.to_string(index=False))
    d.to_csv(INTER / "copas_tiles.csv", index=False)

    url_col = next(c for c in tiles.columns if c in ("cog_url", "href", "url"))
    partes = []
    for i, u in enumerate(tiles[url_col], 1):
        log(f"  tile {i}/{len(tiles)}")
        partes += processar_tile(caminho_gdal(u, fonte), area, validas)
    if not partes:
        raise SystemExit("Nenhum pixel do CHMv2 caiu na grade.")
    s = pd.concat(partes).groupby("h3")[["n_px", "n_copa", "soma_alt"]].sum()
    out = pd.DataFrame({"copa": (s.n_copa / s.n_px).astype("float32"),
                        "altura_copa": (s.soma_alt / s.n_copa.where(s.n_copa > 0)).astype("float32")}).reset_index()
    salvar(out, "copas.parquet")
    log(f"  copa média: {100 * out.copa.mean():.1f}% em {len(out)} hexágonos")


if __name__ == "__main__":
    main()
