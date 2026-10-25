"""Etapa 3 — vegetação por hexágono (Sentinel-2 L2A, 10 m).

Para cada cena da estação seca (jul–out) com pouca nuvem:
  - NDVI = (B08 − B04) / (B08 + B04), com a correção de offset das cenas de processamento ≥ 04.00;
  - pixels válidos pela banda SCL: vegetação (4), solo/construído (5), água (6) e não classificado (7);
    nuvem, sombra e saturação ficam de fora;
  - por hexágono: NDVI mediano, cobertura vegetal (fração de pixels de terra com NDVI ≥ 0,5)
    e fração de água.
Resultado = mediana entre cenas.

Saída: dados/intermediario/vegetacao.parquet  (h3, ndvi, cobertura_vegetal, fracao_agua, n_cenas)
"""
import argparse

import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from pyproj import Transformer

from comum import INTER, bbox_da_grade, carregar_grade, agregar_por_hex, log, salvar

LIMIAR_VEG = 0.5          # NDVI a partir do qual o pixel conta como vegetação densa
SCL_VALIDOS = {4, 5, 6, 7}
SCL_AGUA = 6
MIN_PIXELS_HEX = 60       # de ~150 pixels de 10 m por hexágono


def buscar_cenas(bbox, anos, nuvem, maximo):
    import planetary_computer, pystac_client
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                    modifier=planetary_computer.sign_inplace)
    itens = []
    for ano in anos:
        itens += list(cat.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime=f"{ano}-07-01/{ano}-10-31",
                                 query={"eo:cloud_cover": {"lt": nuvem}}).items())
    itens.sort(key=lambda i: i.properties.get("eo:cloud_cover", 100))
    return itens[:maximo]


def _janela(src, bbox):
    t = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
    xs, ys = t.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    win = from_bounds(min(xs), min(ys), max(xs), max(ys), transform=src.transform)
    return win.round_offsets().round_lengths().intersection(rasterio.windows.Window(0, 0, src.width, src.height))


def offset_reflectancia(baseline):
    """Cenas com processamento ≥ 04.00 (a partir de jan/2022) somam 1000 aos valores."""
    try:
        return 1000 if float(baseline) >= 4.0 else 0
    except (TypeError, ValueError):
        return 0


def processar_cena(href_b04, href_b08, href_scl, baseline, grade, celulas):
    bbox = bbox_da_grade(grade)
    with rasterio.open(href_b04) as src:
        win = _janela(src, bbox)
        b04 = src.read(1, window=win).astype("float32")
        transform, crs, shape = src.window_transform(win), src.crs, (int(win.height), int(win.width))
    with rasterio.open(href_b08) as src:
        b08 = src.read(1, window=_janela(src, bbox), out_shape=shape).astype("float32")
    with rasterio.open(href_scl) as src:
        scl = src.read(1, window=_janela(src, bbox), out_shape=shape, resampling=Resampling.nearest)
    off = offset_reflectancia(baseline)
    b04, b08 = b04 - off, b08 - off
    valido = np.isin(scl, list(SCL_VALIDOS)) & (b04 > 0) & (b08 > 0)
    agua = (scl == SCL_AGUA) & valido
    terra = valido & ~agua
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (b08 - b04) / (b08 + b04)
    px = agregar_por_hex({
        "valido": valido.astype("float32"),
        "agua": np.where(valido, agua, np.nan),
        "ndvi": np.where(terra, ndvi, np.nan),
        "veg": np.where(terra, ndvi >= LIMIAR_VEG, np.nan),
    }, transform, crs, shape, celulas)
    g = px.groupby("h3").agg(n=("valido", "sum"), agua=("agua", "mean"), ndvi=("ndvi", "median"), veg=("veg", "mean"))
    return g[g.n >= MIN_PIXELS_HEX].drop(columns="n")


def main(argv=None, buscar=buscar_cenas):
    ap = argparse.ArgumentParser()
    ap.add_argument("--anos", type=int, nargs="+", default=[2024, 2025, 2026])
    ap.add_argument("--nuvem", type=float, default=20)
    ap.add_argument("--max-cenas", type=int, default=16)
    a = ap.parse_args(argv)
    grade = carregar_grade()
    celulas = set(grade.h3)
    itens = buscar(bbox_da_grade(grade), a.anos, a.nuvem, a.max_cenas)
    log(f"{len(itens)} cenas Sentinel-2 selecionadas (as de menos nuvem)")
    partes, usadas = [], []
    for it in itens:
        try:
            g = processar_cena(it.assets["B04"].href, it.assets["B08"].href, it.assets["SCL"].href,
                               it.properties.get("s2:processing_baseline"), grade, celulas)
        except Exception as e:
            log(f"  {it.id}: erro ({str(e)[:80]})")
            continue
        if g.empty:
            log(f"  {it.id}: sem hexágonos válidos")
            continue
        g["cena"] = it.id
        partes.append(g.reset_index())
        usadas.append({"id": it.id, "data": it.datetime.date().isoformat(),
                       "nuvem": it.properties.get("eo:cloud_cover"), "hexagonos": len(g)})
        log(f"  {it.id}: {len(g)} hexágonos ({len(g) / len(celulas):.0%} da grade)")
    if not partes:
        raise SystemExit("Nenhuma cena Sentinel-2 utilizável. Aumente --nuvem ou inclua mais anos.")
    todos = pd.concat(partes)
    out = todos.groupby("h3").agg(ndvi=("ndvi", "median"), cobertura_vegetal=("veg", "median"),
                                  fracao_agua=("agua", "median"), n_cenas=("cena", "nunique")).reset_index()
    out[["ndvi", "cobertura_vegetal", "fracao_agua"]] = out[["ndvi", "cobertura_vegetal", "fracao_agua"]].round(3)
    salvar(out, "vegetacao.parquet")
    pd.DataFrame(usadas).to_csv(INTER / "cenas_sentinel2.csv", index=False)
    log(f"{len(usadas)} cenas usadas; {len(out)} hexágonos com vegetação ({len(out) / len(celulas):.0%} da grade)")


if __name__ == "__main__":
    main()
