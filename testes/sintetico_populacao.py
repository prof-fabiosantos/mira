import sys, zipfile, importlib.util, io
from pathlib import Path
import numpy as np, pandas as pd, geopandas as gpd
from shapely.geometry import box
RAIZ = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(RAIZ / "pipeline"))
import os
TMP = Path(os.getenv("PLANTIO_RAIZ", RAIZ)) / "testes_tmp"; TMP.mkdir(exist_ok=True)
import comum
spec = importlib.util.spec_from_file_location("p", RAIZ / "pipeline/04_populacao.py"); P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
g = comum.carregar_grade(); b = comum.bbox_da_grade(g, 0.0)
rng = np.random.default_rng(1)
# setores em quadrados de ~0,01° (≈1,1 km); a última coluna se estende 0,05° para fora (zona rural)
xs = np.arange(b[0], b[2], 0.01); ys = np.arange(b[1], b[3], 0.01); geoms, ids = [], []
for i, x in enumerate(xs):
    for j, y in enumerate(ys):
        x1 = x + (0.06 if i == len(xs) - 1 else 0.01)
        geoms.append(box(x, y, x1, y + 0.01)); ids.append(f"130260305{i:03d}{j:03d}")
set_ = gpd.GeoDataFrame({"CD_SETOR": ids, "CD_MUN": "1302603"}, geometry=geoms, crs="EPSG:4674")
set_.to_file(TMP / "AM_setores_CD2022.gpkg", driver="GPKG")
pop = rng.integers(200, 3000, len(ids))
def zipcsv(nome, df):
    buf = df.to_csv(sep=";", index=False, quoting=1)
    with zipfile.ZipFile(TMP / nome, "w") as z: z.writestr(nome.replace(".zip", ".csv"), buf)
zipcsv("basico.zip", pd.DataFrame({"CD_SETOR": ids, "V0001": pop, "V0002": pop // 3}))
dem = pd.DataFrame({"CD_SETOR": ids})
for c in ["V01006","V01009","V01018","V01019","V01020","V01029","V01030"]: dem[c] = (pop * 0.04).astype(int).astype(str)
dem.loc[0, ["V01018","V01019","V01029","V01030"]] = "x"     # setor com sigilo nos idosos
dem.loc[1, "V01019"] = "x"                                  # sigilo parcial
zipcsv("demografia.zip", dem)
setores = P.dados_setores(TMP / "AM_setores_CD2022.gpkg", TMP / "basico.zip", TMP / "demografia.zip")
print("idosos setor 0 (tudo x):", setores.loc[setores.CD_SETOR == ids[0], "idosos"].values, "| setor 1 (parcial):", setores.loc[setores.CD_SETOR == ids[1], "idosos"].values)
P.main([], setores=setores)
out = pd.read_parquet(comum.INTER / "populacao.parquet"); v = pd.read_parquet(comum.INTER / "vegetacao.parquet")
m = out.merge(v, on="h3")
print("densidade média (hab/km²) em hexágonos de mata densa vs construídos:",
      round(m[m.cobertura_vegetal > 0.9].dens_hab_km2.mean()), "vs", round(m[m.cobertura_vegetal < 0.1].dens_hab_km2.mean()))
print("hexágonos com água > 50%:", (m.fracao_agua > 0.5).sum(), "| pop média neles:", round(m[m.fracao_agua > 0.5]["pop"].mean(), 1))
