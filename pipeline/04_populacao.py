"""Etapa 4 — população por hexágono (Censo 2022, IBGE, agregados por setor censitário).

Fontes (baixadas e guardadas em dados/entrada/ibge/):
  - malha de setores com atributos do Amazonas (AM_setores_CD2022.gpkg);
  - agregados por setor: Básico (V0001, total de pessoas) e Demografia (faixas etárias por sexo).
Variáveis de Demografia usadas:
  idosos   = V01018 + V01029 (60–69, homens e mulheres) + V01019 + V01030 (70 ou mais)
  crianças = V01009 + V01020 (0–4 anos)
Valores suprimidos por sigilo ("x") contam como ausentes.

Distribuição dasimétrica: a população de cada setor é repartida entre os hexágonos que ele toca,
proporcionalmente à área sobreposta × "habitabilidade" do hexágono, que é menor onde há água ou
vegetação densa (etapa 3). Assim, um setor que inclui um trecho de mata não põe moradores na mata.

Saída: dados/intermediario/populacao.parquet  (h3, pop, idosos, criancas, dens_hab_km2)
"""
import argparse
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from comum import AREA_HEX_M2, COD_MANAUS, ENTRADA, INTER, carregar_grade, log, poligono_hex, salvar

FTP = "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/"
URL_MALHA = FTP + "malha_com_atributos/setores/gpkg/UF/AM/AM_setores_CD2022.gpkg"
PASTA_CSV = FTP + "Agregados_por_Setor_csv/"
IBGE = ENTRADA / "ibge"
UTM = "EPSG:31980"  # SIRGAS 2000 / UTM 20S

IDOSOS = ["V01018", "V01029", "V01019", "V01030"]
CRIANCAS = ["V01009", "V01020"]


def baixar(url, destino: Path):
    if destino.exists() and destino.stat().st_size > 0:
        return destino
    log(f"  baixando {url.rsplit('/', 1)[-1]} …")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(destino, "wb") as f:
            for parte in r.iter_content(1 << 20):
                f.write(parte)
    return destino


def url_mais_recente(padrao):
    """Na pasta do IBGE, o nome de alguns arquivos leva a data de atualização."""
    html = requests.get(PASTA_CSV, timeout=60).text
    nomes = sorted(set(re.findall(rf'href="({padrao}[^"]*\.zip)"', html)))
    if not nomes:
        raise SystemExit(f"Não encontrei {padrao}*.zip em {PASTA_CSV}")
    return PASTA_CSV + nomes[-1]


def detectar_codificacao(z: zipfile.ZipFile, nome: str) -> str:
    """A documentação diz UTF-8, mas há arquivos do IBGE em Latin-1. Testa uma amostra."""
    with z.open(nome) as f:
        amostra = f.read(2_000_000)
    try:
        amostra.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        if e.start > len(amostra) - 4:  # a amostra cortou um caractere no meio
            return "utf-8"
        return "latin-1"


def ler_csv_zip(caminho: Path) -> pd.DataFrame:
    """Lê o CSV nacional em blocos e guarda só os setores de Manaus."""
    with zipfile.ZipFile(caminho) as z:
        nome = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        cod = detectar_codificacao(z, nome)
        partes = []
        with z.open(nome) as f:
            for bloco in pd.read_csv(f, sep=";", dtype=str, encoding=cod, chunksize=100_000):
                bloco.columns = [c.strip().upper() for c in bloco.columns]
                partes.append(bloco[bloco["CD_SETOR"].str.startswith(COD_MANAUS)])
    log(f"  {caminho.name}: codificação {cod}")
    return pd.concat(partes, ignore_index=True)


def numero(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace({"x": np.nan, "X": np.nan, "..": np.nan, "...": np.nan}), errors="coerce")


def coluna(df, *nomes):
    """Aceita V0001 e V00001, maiúsculas ou minúsculas."""
    for n in nomes:
        for c in df.columns:
            if c.upper() == n.upper():
                return c
    return None


def dados_setores(malha=None, basico=None, demografia=None):
    IBGE.mkdir(parents=True, exist_ok=True)
    malha = malha or baixar(URL_MALHA, IBGE / "AM_setores_CD2022.gpkg")
    basico = basico or baixar(url_mais_recente("Agregados_por_setores_basico_BR"), IBGE / "basico.zip")
    demografia = demografia or baixar(url_mais_recente("Agregados_por_setores_demografia_BR"), IBGE / "demografia.zip")

    setores = gpd.read_file(malha)
    cs = coluna(setores, "CD_SETOR")
    setores = setores[setores[cs].astype(str).str.startswith(COD_MANAUS)][[cs, "geometry"]].rename(columns={cs: "CD_SETOR"})
    setores["CD_SETOR"] = setores["CD_SETOR"].astype(str)
    # Um setor com partes separadas (por exemplo, dos dois lados de um igarapé) pode vir em várias linhas.
    repetidos = int(setores["CD_SETOR"].duplicated().sum())
    if repetidos:
        log(f"  {repetidos} linhas extras de setores com mais de uma parte; juntando as partes")
        setores = setores.dissolve(by="CD_SETOR", as_index=False)
    b, d = ler_csv_zip(basico), ler_csv_zip(demografia)
    cpop = coluna(b, "V0001", "V00001")
    tab = pd.DataFrame({"CD_SETOR": b["CD_SETOR"], "pop": numero(b[cpop])})
    dd = pd.DataFrame({"CD_SETOR": d["CD_SETOR"]})
    for nome, cols in (("idosos", IDOSOS), ("criancas", CRIANCAS)):
        achadas = [coluna(d, c) for c in cols]
        if None in achadas:
            raise SystemExit(f"Colunas {cols} não encontradas no arquivo de Demografia.")
        dd[nome] = sum(numero(d[c]).fillna(0) for c in achadas)
        dd.loc[pd.concat([numero(d[c]) for c in achadas], axis=1).isna().all(axis=1), nome] = np.nan
    tab = tab.drop_duplicates("CD_SETOR").merge(dd.drop_duplicates("CD_SETOR"), on="CD_SETOR", how="left")
    setores = setores.merge(tab, on="CD_SETOR", how="left")
    log(f"  {len(setores)} setores de Manaus; {int(setores['pop'].sum()):,} pessoas no Censo 2022".replace(",", "."))
    return setores


def main(argv=None, setores=None):
    ap = argparse.ArgumentParser()
    ap.parse_args(argv)
    grade = carregar_grade()
    setores = setores if setores is not None else dados_setores()

    hab = pd.Series(1.0, index=grade.h3)
    pveg = INTER / "vegetacao.parquet"
    if pveg.exists():
        v = pd.read_parquet(pveg).set_index("h3")
        h = (1 - v.fracao_agua.fillna(0)) * (1 - 0.8 * v.cobertura_vegetal.fillna(0))
        hab.loc[h.index.intersection(hab.index)] = h.clip(0.02, 1)
    else:
        log("  aviso: vegetação ainda não calculada; população distribuída só por área")

    hexes = gpd.GeoDataFrame(grade[["h3"]], geometry=[poligono_hex(c) for c in grade.h3], crs="EPSG:4326").to_crs(UTM)
    setores = setores.to_crs(UTM)
    setores["area_setor"] = setores.area
    pedacos = gpd.overlay(setores, hexes, how="intersection", keep_geom_type=True)
    pedacos["area"] = pedacos.area
    pedacos["w"] = pedacos["area"] * pedacos["h3"].map(hab)
    # parte do setor fora da grade (zona rural) recebe a habitabilidade média da parte de dentro
    por_setor = pedacos.groupby("CD_SETOR").agg(w=("w", "sum"), area=("area", "sum"))
    fora = (setores.set_index("CD_SETOR")["area_setor"] - por_setor["area"]).clip(lower=0)
    denom = por_setor["w"] + fora.reindex(por_setor.index).fillna(0) * (por_setor["w"] / por_setor["area"])
    pedacos["fracao"] = pedacos["w"] / pedacos["CD_SETOR"].map(denom)
    for col in ("pop", "idosos", "criancas"):
        pedacos[col + "_h"] = pedacos["fracao"] * pedacos[col]
    out = pedacos.groupby("h3")[["pop_h", "idosos_h", "criancas_h"]].sum(min_count=1).reset_index()
    out.columns = ["h3", "pop", "idosos", "criancas"]
    out = grade[["h3"]].merge(out, on="h3", how="left").fillna({"pop": 0})
    out["dens_hab_km2"] = out["pop"] / (AREA_HEX_M2 / 1e6)
    out[["pop", "idosos", "criancas", "dens_hab_km2"]] = out[["pop", "idosos", "criancas", "dens_hab_km2"]].round(1)
    total_setores = setores["pop"].sum()
    log(f"  população na grade: {out['pop'].sum():,.0f} de {total_setores:,.0f} nos setores "
        f"({out['pop'].sum() / total_setores:.1%}; o restante mora fora da área urbana dos bairros)".replace(",", "."))
    salvar(out, "populacao.parquet")


if __name__ == "__main__":
    main()
