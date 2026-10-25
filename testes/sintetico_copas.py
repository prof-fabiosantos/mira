"""Teste da etapa 6 com um mapa de alturas sintético no formato do CHMv2 (sem rede)."""
import importlib.util, os, sys
from pathlib import Path
import numpy as np, pyarrow as pa, pyarrow.parquet as pq, rasterio
from rasterio.transform import from_origin
from pyproj import Transformer
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "pipeline"))
import comum
FONTE = Path(os.getenv("PLANTIO_RAIZ", RAIZ)) / "testes_tmp" / "chmv2"
(FONTE / "chm").mkdir(parents=True, exist_ok=True)

bbox = comum.bbox_da_grade(comum.carregar_grade(), margem=0.01)
t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
x0, y0 = t.transform(bbox[0], bbox[1]); x1, y1 = t.transform(bbox[2], bbox[3])
xm = (x0 + x1) / 2
rng = np.random.default_rng(1)
tiles = []
# dois tiles lado a lado, para testar a junção
for k, (a, b) in enumerate([(x0, xm), (xm, x1)]):
    res = 4.0  # mais grosso que o real (1,2 m), para o teste ser rápido
    w, h = int((b - a) / res), int((y1 - y0) / res)
    base = rng.random((h // 40 + 1, w // 40 + 1))
    arr = np.kron(base, np.ones((40, 40)))[:h, :w]
    alt = np.where(arr > 0.55, (arr - 0.55) * 50, np.where(arr > 0.4, 1.0, 0)).astype("uint8")
    nome = f"chm/t{k}.tif"
    with rasterio.open(FONTE / nome, "w", driver="GTiff", width=w, height=h, count=1, dtype="uint8",
                       crs="EPSG:3857", transform=from_origin(a, y1, res, res), tiled=True, compress="deflate") as d:
        d.write(alt, 1)
    tiles.append((f"21010121{k:02d}", nome, a, y0, b, y1))
bb = pa.StructArray.from_arrays([pa.array([x[2] for x in tiles]), pa.array([x[3] for x in tiles]),
                                 pa.array([x[4] for x in tiles]), pa.array([x[5] for x in tiles])],
                                names=["minx", "miny", "maxx", "maxy"])
pq.write_table(pa.table({"quadkey": [x[0] for x in tiles], "cog_url": [x[1] for x in tiles],
                         "tile_size_bytes": [1, 1], "bbox_3857": bb}), FONTE / "tiles.parquet")
pq.write_table(pa.table({"quadkey": [x[0] for x in tiles], "acq_start": ["2014-11-30", "2017-07-11"],
                         "acq_end": ["2019-08-20", "2019-11-29"], "acq_n": [17, 14]}), FONTE / "acq_dates.parquet")

spec = importlib.util.spec_from_file_location("copas", RAIZ / "pipeline" / "06_copas.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.main(["--fonte", str(FONTE)])
import pandas as pd
c = pd.read_parquet(comum.INTER / "copas.parquet")
g = comum.carregar_grade()
assert len(c) == len(g), f"copas em {len(c)} de {len(g)} hexágonos"
assert c.copa.between(0, 1).all() and 0.05 < c.copa.mean() < 0.9, c.copa.describe()
assert (c.altura_copa.dropna() >= 3).all()
print(f"ok: copa em {len(c)} hexágonos, média {100 * c.copa.mean():.1f}%")
