import sys, importlib.util
from pathlib import Path
import numpy as np, geopandas as gpd
from shapely.geometry import box, LineString, Point
RAIZ = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(RAIZ / "pipeline"))
import comum
spec = importlib.util.spec_from_file_location("v", RAIZ / "pipeline/05_viabilidade.py"); V = importlib.util.module_from_spec(spec); spec.loader.exec_module(V)
g = comum.carregar_grade(); b = comum.bbox_da_grade(g, 0)
def osm(bbox, tags):
    k = list(tags)[0]
    if k == "leisure":   # 2 praças sobrepostas + uma
        geoms = [box(-60.021, -3.106, -60.019, -3.104), box(-60.020, -3.105, -60.018, -3.103), box(-60.03, -3.09, -60.029, -3.089)]
    elif k == "amenity": geoms = [Point(-60.0205, -3.1045), box(-60.0301, -3.0901, -60.0299, -3.0899)]
    elif k == "aeroway": geoms = [box(-60.06, -3.045, -60.04, -3.025)]   # "aeroporto" sintético
    else:                # grade de ruas a cada ~200 m numa área
        geoms = [LineString([(x, -3.12), (x, -3.08)]) for x in np.arange(-60.03, -60.0, 0.0018)] + \
                [LineString([(-60.03, y), (-60.0, y)]) for y in np.arange(-3.12, -3.08, 0.0018)]
    return gpd.GeoDataFrame({"tipo": [k] * len(geoms)}, geometry=geoms, crs="EPSG:4326")
V.main([], osm=osm)
import pandas as pd
o = pd.read_parquet(comum.INTER / "viabilidade.parquet")
print(o.describe().loc[["mean", "max"]].round(1))
print("área pública total (m²):", int(o.area_publica_m2.sum()), "| esperado ~", int((0.002*0.002*2 - 0.001*0.001 + 0.001*0.001) * 111320**2))
print("escolas:", o.escolas.sum(), "| aeroporto:", o.aeroporto.sum())
