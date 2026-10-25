"""Etapa 1 — grade de hexágonos H3 (res. 10) sobre os 63 bairros oficiais de Manaus.

Entrada: dados/entrada/bairros.geojson (malha de bairros do IBGE, a mesma do Akub),
com a propriedade "nome".
Saída:   dados/intermediario/grade.parquet  (h3, bairro, lat, lon)
"""
import json

import h3
import pandas as pd
from shapely.geometry import shape

from comum import ENTRADA, RES, AREA_HEX_M2, log, salvar


def main(argv=None):
    arq = ENTRADA / "bairros.geojson"
    if not arq.exists():
        raise SystemExit("Copie o bairros.geojson do Akub (public/data) para dados/entrada/.")
    feats = json.loads(arq.read_text(encoding="utf-8"))["features"]
    linhas = []
    for f in feats:
        nome = f["properties"]["nome"]
        geom = shape(f["geometry"])
        partes = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for p in partes:
            for cel in h3.geo_to_cells(p.__geo_interface__, RES):  # centro do hexágono dentro do bairro
                linhas.append((cel, nome))
    grade = pd.DataFrame(linhas, columns=["h3", "bairro"]).drop_duplicates("h3")
    centros = [h3.cell_to_latlng(c) for c in grade.h3]
    grade["lat"] = [c[0] for c in centros]
    grade["lon"] = [c[1] for c in centros]
    log(f"{len(grade)} hexágonos em {grade.bairro.nunique()} bairros "
        f"(~{len(grade) * AREA_HEX_M2 / 1e6:.0f} km²; cada hexágono ~{AREA_HEX_M2:,.0f} m²)")
    salvar(grade, "grade.parquet")


if __name__ == "__main__":
    main()
