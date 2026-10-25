"""Etapa 2 — calor de superfície por hexágono (Landsat 8/9, Coleção 2, Nível 2).

Para cada cena da estação seca (jul–out) com pouca nuvem:
  - converte a banda térmica ST_B10 para °C e mascara nuvem, sombra, cirro e água (QA_PIXEL);
  - calcula a mediana dos pixels de cada hexágono;
  - anomalia do hexágono na cena = mediana do hexágono − mediana dos hexágonos da cena.
Resultado = mediana entre cenas. Só entram cenas que cobrem ao menos MIN_COBERTURA da grade
(a órbita 230 cobre só a ponta leste de Manaus e fica de fora naturalmente).

A banda térmica tem resolução nativa de 100 m (entregue reamostrada a 30 m), compatível
com hexágonos de ~120 m.

Saída: dados/intermediario/calor.parquet  (h3, lst_c, lst_anom_c, n_cenas)
"""
import argparse

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds
from pyproj import Transformer

from comum import INTER, bbox_da_grade, carregar_grade, agregar_por_hex, log, salvar

ESCALA, OFFSET = 0.00341802, 149.0
MASCARA = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 7)  # nuvem dilatada, cirro, nuvem, sombra, água
MIN_PIXELS_HEX = 6        # ~6 pixels de 30 m já cobrem a resolução térmica nativa no hexágono
MIN_COBERTURA = 0.8       # fração da grade com dado válido para a cena entrar


def buscar_cenas(bbox, anos, nuvem):
    import planetary_computer, pystac_client
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                    modifier=planetary_computer.sign_inplace)
    itens = []
    for ano in anos:
        itens += list(cat.search(collections=["landsat-c2-l2"], bbox=bbox, datetime=f"{ano}-07-01/{ano}-10-31",
                                 query={"eo:cloud_cover": {"lt": nuvem},
                                        "platform": {"in": ["landsat-8", "landsat-9"]}}).items())
    return sorted(itens, key=lambda i: i.datetime)


def ler_janela(href, bbox_4326):
    with rasterio.open(href) as src:
        t = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        xs, ys = t.transform([bbox_4326[0], bbox_4326[2], bbox_4326[0], bbox_4326[2]],
                             [bbox_4326[1], bbox_4326[1], bbox_4326[3], bbox_4326[3]])
        win = from_bounds(min(xs), min(ys), max(xs), max(ys), transform=src.transform)
        win = win.round_offsets().round_lengths().intersection(
            rasterio.windows.Window(0, 0, src.width, src.height))
        return src.read(1, window=win), src.window_transform(win), src.crs, src.nodata


def processar_cena(href_st, href_qa, grade, celulas):
    bbox = bbox_da_grade(grade)
    st, transform, crs, nodata = ler_janela(href_st, bbox)
    qa, _, _, _ = ler_janela(href_qa, bbox)
    lst = st.astype("float32") * ESCALA + OFFSET - 273.15
    invalido = (st == 0) | ((qa & MASCARA) != 0)
    if nodata is not None:
        invalido |= st == nodata
    lst[invalido] = np.nan
    px = agregar_por_hex({"lst": lst}, transform, crs, st.shape, celulas).dropna()
    g = px.groupby("h3")["lst"].agg(["median", "count"])
    return g[g["count"] >= MIN_PIXELS_HEX]["median"]


def main(argv=None, buscar=buscar_cenas):
    ap = argparse.ArgumentParser()
    ap.add_argument("--anos", type=int, nargs="+", default=[2023, 2024, 2025, 2026])
    ap.add_argument("--nuvem", type=float, default=40)
    a = ap.parse_args(argv)
    grade = carregar_grade()
    celulas = set(grade.h3)
    itens = buscar(bbox_da_grade(grade), a.anos, a.nuvem)
    log(f"{len(itens)} cenas Landsat candidatas")
    anomalias, valores, usadas = [], [], []
    for it in itens:
        try:
            med = processar_cena(it.assets["lwir11"].href, it.assets["qa_pixel"].href, grade, celulas)
        except Exception as e:
            log(f"  {it.id}: erro ({str(e)[:80]})")
            continue
        cobertura = len(med) / len(celulas)
        if cobertura < MIN_COBERTURA:
            log(f"  {it.id}: {cobertura:.0%} da grade válida, descartada")
            continue
        ref = float(med.median())
        anomalias.append((med - ref).rename(it.id))
        valores.append(med.rename(it.id))
        usadas.append({"id": it.id, "data": it.datetime.date().isoformat(), "cobertura": round(cobertura, 3),
                       "mediana_c": round(ref, 2)})
        log(f"  {it.id}: {cobertura:.0%} da grade, mediana {ref:.1f} °C")
    if not usadas:
        raise SystemExit("Nenhuma cena Landsat utilizável. Aumente --nuvem ou inclua mais anos.")
    A, V = pd.concat(anomalias, axis=1), pd.concat(valores, axis=1)
    out = pd.DataFrame({"h3": A.index, "lst_c": V.median(axis=1).round(2).values,
                        "lst_anom_c": A.median(axis=1).round(2).values,
                        "n_cenas": A.notna().sum(axis=1).values})
    salvar(out, "calor.parquet")
    pd.DataFrame(usadas).to_csv(INTER / "cenas_landsat.csv", index=False)
    log(f"{len(usadas)} cenas usadas; {len(out)} hexágonos com calor ({len(out) / len(celulas):.0%} da grade)")


if __name__ == "__main__":
    main()
