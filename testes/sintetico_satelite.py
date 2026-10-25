"""Teste das etapas 2 e 3 com cenas sintéticas (sem rede)."""
import sys, types, datetime as dt, importlib.util, time
from pathlib import Path
import numpy as np, rasterio
from rasterio.transform import from_origin
from pyproj import Transformer
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "pipeline"))
import os
TMP = Path(os.getenv("PLANTIO_RAIZ", RAIZ)) / "testes_tmp"; TMP.mkdir(exist_ok=True)
import comum
def carregar(nome):
    spec = importlib.util.spec_from_file_location(nome, RAIZ / "pipeline" / f"{nome}.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

bbox = comum.bbox_da_grade(comum.carregar_grade(), margem=0.02)
rng = np.random.default_rng(0)

def campo(crs, res):
    t = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xs, ys = t.transform([bbox[0], bbox[2]], [bbox[1], bbox[3]])
    x0, y1 = min(xs), max(ys); w = int((max(xs) - x0) / res); h = int((y1 - min(ys)) / res)
    tr = from_origin(x0, y1, res, res)
    cols, rows = np.meshgrid(np.arange(w) + .5, np.arange(h) + .5)
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(tr.c + cols * res, tr.f - rows * res)
    # "urbanização": alta perto de (-3.10, -60.01), baixa a norte/oeste
    urb = np.exp(-(((lat + 3.10) / 0.06) ** 2 + ((lon + 60.01) / 0.07) ** 2))
    rio = lat < -3.135 + 0.02 * (lon + 60.0)
    return tr, (h, w), urb, rio

def gravar(path, arr, tr, crs, dtype):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype=dtype, crs=crs, transform=tr) as f:
        f.write(arr.astype(dtype), 1)

class A:
    def __init__(s, h): s.href = str(h)
class Item:
    def __init__(s, id, data, assets, props): s.id, s.datetime, s.assets, s.properties = id, data, assets, props

def nuvem(shape, frac):
    m = np.zeros(shape, bool)
    for _ in range(int(frac * 40)):
        r, c = rng.integers(0, shape[0]), rng.integers(0, shape[1]); rr = rng.integers(20, 120)
        m[max(0, r - rr):r + rr, max(0, c - rr):c + rr] = True
    return m

# ---- Landsat (EPSG:32620, 30 m)
def landsat(bbox, anos, nuvem_max):
    tr, shp, urb, rio = campo("EPSG:32620", 30); itens = []
    for i, (frac, d) in enumerate([(0.1, "2024-08-10"), (0.3, "2024-09-11"), (0.05, "2025-08-13"), (1.0, "2025-09-30")]):
        lst = 31 + 9 * urb + rng.normal(0, 0.6, shp) + i * 0.8
        dn = np.round((lst + 273.15 - 149.0) / 0.00341802)
        qa = np.zeros(shp, dtype="uint16"); qa[rio] = 1 << 7; qa[nuvem(shp, frac)] |= 1 << 3
        if frac >= 1.0: qa[:] = 1 << 3            # cena toda nublada: deve ser descartada
        gravar(TMP / f"ls{i}_st.tif", dn, tr, "EPSG:32620", "uint16"); gravar(TMP / f"ls{i}_qa.tif", qa, tr, "EPSG:32620", "uint16")
        itens.append(Item(f"LC09_SINT_{i}", dt.datetime.fromisoformat(d), {"lwir11": A(TMP / f"ls{i}_st.tif"), "qa_pixel": A(TMP / f"ls{i}_qa.tif")}, {}))
    return itens

# ---- Sentinel-2 (EPSG:32720, 10 m; uma cena antiga sem offset e outras com offset)
def sentinel(bbox, anos, nuvem_max, maximo):
    tr, shp, urb, rio = campo("EPSG:32720", 10); tr20 = from_origin(tr.c, tr.f, 20, 20); itens = []
    for i, (frac, base, d) in enumerate([(0.1, "05.10", "2024-08-02"), (0.2, "05.11", "2025-08-20"), (0.15, "03.01", "2021-08-05")]):
        ndvi_alvo = np.clip(0.85 - 0.75 * urb + rng.normal(0, 0.05, shp), -0.2, 0.95)
        nir = np.full(shp, 3000.0); red = nir * (1 - ndvi_alvo) / (1 + ndvi_alvo)
        off = 1000 if float(base) >= 4 else 0
        scl = np.where(ndvi_alvo > 0.5, 4, 5).astype("uint8"); scl[rio] = 6; red[rio] = 300; nir[rio] = 150
        scl[nuvem(shp, frac)] = 9
        gravar(TMP / f"s2{i}_b04.tif", red + off, tr, "EPSG:32720", "uint16"); gravar(TMP / f"s2{i}_b08.tif", nir + off, tr, "EPSG:32720", "uint16")
        gravar(TMP / f"s2{i}_scl.tif", scl[::2, ::2], tr20, "EPSG:32720", "uint8")
        itens.append(Item(f"S2_SINT_{i}", dt.datetime.fromisoformat(d), {"B04": A(TMP / f"s2{i}_b04.tif"), "B08": A(TMP / f"s2{i}_b08.tif"), "SCL": A(TMP / f"s2{i}_scl.tif")}, {"s2:processing_baseline": base, "eo:cloud_cover": frac * 100}))
    return itens

t0 = time.time(); carregar("02_calor").main([], buscar=landsat); print(f"calor em {time.time() - t0:.0f} s")
t0 = time.time(); carregar("03_vegetacao").main([], buscar=sentinel); print(f"vegetação em {time.time() - t0:.0f} s")
import pandas as pd
c = pd.read_parquet(comum.INTER / "calor.parquet"); v = pd.read_parquet(comum.INTER / "vegetacao.parquet"); g = comum.carregar_grade()
m = g.merge(c, on="h3").merge(v, on="h3")
print(m.groupby("bairro")[["lst_anom_c", "cobertura_vegetal", "fracao_agua"]].mean().sort_values("lst_anom_c").iloc[[0, 1, -2, -1]].round(2))
print("correlação calor × cobertura:", round(m.lst_anom_c.corr(m.cobertura_vegetal), 2))
