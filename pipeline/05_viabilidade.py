"""Etapa 5 — espaço e restrições por hexágono (OpenStreetMap).

Por hexágono:
  area_publica_m2  praças, parques, jardins, gramados e áreas de lazer (onde cabe plantio público)
  vias_m           comprimento de ruas e avenidas (calçadas e canteiros são o principal espaço de arborização)
  vias_principais_m  só avenidas e vias principais (primary, secondary, tertiary), onde costuma haver canteiro
  escolas          escolas, creches e universidades (parceiros naturais para plantio e educação ambiental)
  aeroporto        True se o centro do hexágono está em área aeroportuária (árvores não são indicadas)

Saída: dados/intermediario/viabilidade.parquet
"""
import argparse

import geopandas as gpd
import pandas as pd

from comum import bbox_da_grade, carregar_grade, log, poligono_hex, salvar

UTM = "EPSG:31980"
TAGS_AREAS = {
    "leisure": ["park", "playground", "garden", "pitch", "recreation_ground", "common"],
    "landuse": ["grass", "recreation_ground", "village_green"],
}
TAGS_ESCOLAS = {"amenity": ["school", "kindergarten", "college", "university"]}
TAGS_AEROPORTO = {"aeroway": ["aerodrome", "runway", "taxiway", "apron"]}
PRINCIPAIS = {"primary", "secondary", "tertiary", "primary_link", "secondary_link", "tertiary_link"}
TAGS_VIAS = {"highway": ["primary", "secondary", "tertiary", "residential", "living_street", "unclassified",
                         "pedestrian", "primary_link", "secondary_link", "tertiary_link"]}


def baixar_osm(bbox, tags):
    import osmnx as ox
    ox.settings.log_console = False
    ox.settings.requests_timeout = 300
    try:
        return ox.features_from_bbox(tuple(bbox), tags=tags)
    except Exception as e:  # nenhum resultado ou falha do Overpass
        if "No matching features" in str(e):
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        raise


def main(argv=None, osm=baixar_osm):
    ap = argparse.ArgumentParser()
    ap.parse_args(argv)
    grade = carregar_grade()
    bbox = bbox_da_grade(grade)
    hexes = gpd.GeoDataFrame(grade[["h3"]], geometry=[poligono_hex(c) for c in grade.h3], crs="EPSG:4326").to_crs(UTM)
    out = grade[["h3"]].copy()

    log("Consultando o OpenStreetMap (praças, escolas, aeroporto e vias) …")
    areas = osm(bbox, TAGS_AREAS)
    areas = areas[areas.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(UTM)
    if len(areas):
        uniao = gpd.GeoDataFrame(geometry=[areas.union_all()], crs=UTM)
        pd_ = gpd.overlay(hexes, uniao, how="intersection", keep_geom_type=True)
        pd_["a"] = pd_.area
        out = out.merge(pd_.groupby("h3")["a"].sum().rename("area_publica_m2"), on="h3", how="left")
    else:
        out["area_publica_m2"] = 0.0
    log(f"  {len(areas)} praças, parques e áreas verdes públicas")

    escolas = osm(bbox, TAGS_ESCOLAS).to_crs(UTM)
    if len(escolas):
        pts = gpd.GeoDataFrame(geometry=escolas.geometry.representative_point(), crs=UTM)
        j = gpd.sjoin(pts, hexes, predicate="within")
        out = out.merge(j.groupby("h3").size().rename("escolas"), on="h3", how="left")
    else:
        out["escolas"] = 0
    log(f"  {len(escolas)} escolas, creches e universidades")

    aero = osm(bbox, TAGS_AEROPORTO)
    aero = aero[aero.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(UTM)
    if len(aero):
        centros = gpd.GeoDataFrame(hexes[["h3"]], geometry=hexes.geometry.centroid, crs=UTM)
        dentro = gpd.sjoin(centros, aero[["geometry"]], predicate="within")["h3"].unique()
        out["aeroporto"] = out["h3"].isin(dentro)
    else:
        out["aeroporto"] = False
    log(f"  {int(out['aeroporto'].sum())} hexágonos em área aeroportuária")

    vias = osm(bbox, TAGS_VIAS)
    vias = vias[vias.geometry.geom_type.isin(["LineString", "MultiLineString"])].to_crs(UTM)
    if len(vias):
        tipo = vias["highway"].astype(str) if "highway" in vias.columns else pd.Series("", index=vias.index)
        vias = gpd.GeoDataFrame({"principal": tipo.isin(PRINCIPAIS).values}, geometry=vias.geometry.values, crs=UTM)
        pv = gpd.overlay(vias, hexes, how="intersection", keep_geom_type=True)
        pv["m"] = pv.length
        out = out.merge(pv.groupby("h3")["m"].sum().rename("vias_m"), on="h3", how="left")
        out = out.merge(pv[pv.principal].groupby("h3")["m"].sum().rename("vias_principais_m"), on="h3", how="left")
    else:
        out["vias_m"] = 0.0
        out["vias_principais_m"] = 0.0
    log(f"  {len(vias)} trechos de via; {out['vias_m'].sum() / 1000:,.0f} km dentro da grade".replace(",", "."))

    out = out.fillna({"area_publica_m2": 0, "escolas": 0, "vias_m": 0, "vias_principais_m": 0})
    out["escolas"] = out["escolas"].astype(int)
    out[["area_publica_m2", "vias_m", "vias_principais_m"]] = out[["area_publica_m2", "vias_m", "vias_principais_m"]].round(0)
    salvar(out, "viabilidade.parquet")


if __name__ == "__main__":
    main()
