"""Etapa 6 — índice de prioridade de plantio por hexágono.

1. Exclusões (o hexágono não recebe índice):
     água (> 50% do hexágono), área aeroportuária, vegetação já densa (cobertura ≥ 60%: proteger,
     não plantar) e falta de dado de satélite.
2. Três fatores, cada um convertido em percentil (0–1) entre os hexágonos elegíveis:
     calor    anomalia de temperatura de superfície (etapa 2)
     verde    falta de vegetação: NDVI baixo (etapa 3)
     pessoas  densidade de moradores, com peso extra de 50% para idosos (60+) e crianças (0–4) (etapa 4)
3. Índice = 100 × média ponderada dos percentis. Pesos padrão: calor 0,4 · verde 0,3 · pessoas 0,3.
4. Faixas pelo percentil do índice: muito alta (≥ 90), alta (75–90), média (50–75), baixa (< 50).
5. Robustez: o índice é recalculado com outros três conjuntos de pesos; "robusto" = o hexágono fica
   entre os 10% mais prioritários em todos.
6. Tipo de oportunidade (não entra no índice; orienta quem planeja o plantio), o primeiro que se aplica:
     praça ou área pública (≥ 500 m²) · escola ou creche · avenida com canteiro (≥ 100 m de via principal)
     · ruas locais (≥ 100 m; depende de calçada, a confirmar em campo) · sem registro no OpenStreetMap
7. Relação vegetação–calor: regressão da anomalia de superfície sobre a cobertura vegetal, com
   intervalo de confiança por bootstrap de bairros inteiros (hexágonos vizinhos não são independentes).

Saídas em public/data/: hexagonos.csv, bairros.csv, relacao.json, meta.json
"""
import argparse
import datetime as dt

import numpy as np
import pandas as pd

from comum import AREA_HEX_M2, INTER, SAIDA, carregar_grade, gravar_json, log

OPORTUNIDADES = ["praça ou área pública", "escola ou creche", "avenida com canteiro", "ruas locais",
                 "sem registro no OpenStreetMap"]
PESOS_ALTERNATIVOS = {"iguais": (1 / 3, 1 / 3, 1 / 3), "calor": (0.6, 0.2, 0.2),
                      "verde": (0.2, 0.6, 0.2), "pessoas": (0.2, 0.2, 0.6)}


def ler(nome):
    p = INTER / nome
    if not p.exists():
        raise SystemExit(f"Falta {nome}. Rode as etapas anteriores (python pipeline/rodar_tudo.py).")
    return pd.read_parquet(p)


def percentil(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average")


def indice(df, pesos):
    wc, wv, wp = pesos
    return 100 * (wc * df.p_calor + wv * df.p_verde + wp * df.p_pessoas) / (wc + wv + wp)


def relacao_vegetacao_calor(df, n_boot=500, seed=0):
    d = df[(df.fracao_agua < 0.1) & ~df.aeroporto].dropna(subset=["lst_anom_c", "cobertura_vegetal"])
    x, y = d.cobertura_vegetal.values, d.lst_anom_c.values
    inc, intercepto = np.polyfit(x, y, 1)
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    rng = np.random.default_rng(seed)
    grupos = {b: g for b, g in d.groupby("bairro")}
    nomes = list(grupos)
    boot = []
    for _ in range(n_boot):
        amostra = pd.concat([grupos[nomes[i]] for i in rng.integers(0, len(nomes), len(nomes))])
        boot.append(np.polyfit(amostra.cobertura_vegetal, amostra.lst_anom_c, 1)[0])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {
        "descricao": "Variação da temperatura de superfície associada a +10 pontos percentuais de cobertura "
                     "vegetal no hexágono, em Manaus (associação observada, não efeito causal garantido).",
        "graus_por_10pp": round(inc / 10, 2), "ic95_graus_por_10pp": [round(lo / 10, 2), round(hi / 10, 2)],
        "r2": round(r2, 3), "hexagonos": int(len(d)), "bairros": len(nomes),
        "metodo": "regressão linear; IC 95% por bootstrap de bairros (500 reamostragens)",
        "pontos_grafico": d.sample(min(1500, len(d)), random_state=seed)[["cobertura_vegetal", "lst_anom_c"]]
                          .round(3).values.tolist(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--peso-calor", type=float, default=0.4)
    ap.add_argument("--peso-verde", type=float, default=0.3)
    ap.add_argument("--peso-pessoas", type=float, default=0.3)
    a = ap.parse_args(argv)
    pesos = (a.peso_calor, a.peso_verde, a.peso_pessoas)

    df = (carregar_grade().merge(ler("calor.parquet"), on="h3", how="left")
          .merge(ler("vegetacao.parquet"), on="h3", how="left")
          .merge(ler("populacao.parquet"), on="h3", how="left")
          .merge(ler("viabilidade.parquet"), on="h3", how="left"))
    df["aeroporto"] = df["aeroporto"].fillna(False).astype(bool)

    df["excluido"] = ""
    df.loc[df.lst_anom_c.isna() | df.ndvi.isna(), "excluido"] = "sem dado de satélite"
    df.loc[df.cobertura_vegetal >= 0.6, "excluido"] = "vegetação densa (proteger)"
    df.loc[df.aeroporto, "excluido"] = "área aeroportuária"
    df.loc[df.fracao_agua > 0.5, "excluido"] = "água"
    el = df.excluido == ""

    vuln = df[["idosos", "criancas"]].fillna(0).sum(axis=1)
    df["pessoas_ponderadas"] = df["pop"].fillna(0) + 0.5 * vuln
    df.loc[el, "p_calor"] = percentil(df.loc[el, "lst_anom_c"])
    df.loc[el, "p_verde"] = percentil(-df.loc[el, "ndvi"])
    df.loc[el, "p_pessoas"] = percentil(df.loc[el, "pessoas_ponderadas"])
    df.loc[el, "indice"] = indice(df.loc[el], pesos)

    pct = percentil(df.loc[el, "indice"]) * 100
    df.loc[el, "faixa"] = pd.cut(pct, [-1, 50, 75, 90, 101], labels=["baixa", "média", "alta", "muito alta"]).astype(str)
    df.loc[el, "rank_cidade"] = df.loc[el, "indice"].rank(ascending=False, method="min")
    df.loc[el, "rank_bairro"] = df[el].groupby("bairro")["indice"].rank(ascending=False, method="min")

    topo = pd.DataFrame({n: percentil(indice(df.loc[el], w)) >= 0.9 for n, w in PESOS_ALTERNATIVOS.items()})
    df.loc[el, "robusto"] = topo.all(axis=1) & (pct >= 90)
    df["robusto"] = df["robusto"].fillna(False).astype(bool)
    if "vias_principais_m" not in df.columns:
        df["vias_principais_m"] = 0.0
    df["oportunidade"] = np.select(
        [df.area_publica_m2.fillna(0) >= 500, df.escolas.fillna(0) >= 1,
         df.vias_principais_m.fillna(0) >= 100, df.vias_m.fillna(0) >= 100],
        [OPORTUNIDADES[0], OPORTUNIDADES[1], OPORTUNIDADES[2], OPORTUNIDADES[3]], default=OPORTUNIDADES[4])

    # ---------- saídas ----------
    cols = ["h3", "bairro", "indice", "faixa", "rank_cidade", "rank_bairro", "robusto", "excluido",
            "p_calor", "p_verde", "p_pessoas", "lst_anom_c", "lst_c", "ndvi", "cobertura_vegetal", "fracao_agua",
            "pop", "idosos", "criancas", "dens_hab_km2", "area_publica_m2", "vias_m", "vias_principais_m", "escolas", "oportunidade"]
    saida = df[cols].copy()
    saida[["indice", "p_calor", "p_verde", "p_pessoas"]] = saida[["indice", "p_calor", "p_verde", "p_pessoas"]].round(3)
    saida["faixa"] = saida["faixa"].fillna("")
    for c in ("rank_cidade", "rank_bairro"):
        saida[c] = saida[c].astype("Int64")
    saida.to_csv(SAIDA / "hexagonos.csv", index=False)

    m = df[el]
    terra = df[df.fracao_agua.fillna(0) < 0.5]
    resumo = pd.DataFrame({
        "hexagonos": df.groupby("bairro").size(),
        "area_km2": (df.groupby("bairro").size() * AREA_HEX_M2 / 1e6).round(2),
        "populacao": df.groupby("bairro")["pop"].sum().round(0),
        "cobertura_vegetal_media": terra.groupby("bairro")["cobertura_vegetal"].mean().round(3),
        "anomalia_superficie_media_c": terra.groupby("bairro")["lst_anom_c"].mean().round(2),
        "indice_medio": m.groupby("bairro")["indice"].mean().round(1),
        "hex_muito_alta": m[m.faixa == "muito alta"].groupby("bairro").size(),
        "hex_robustos": m[m.robusto].groupby("bairro").size(),
        "pop_em_muito_alta": m[m.faixa == "muito alta"].groupby("bairro")["pop"].sum().round(0),
    }).fillna({"hex_muito_alta": 0, "hex_robustos": 0, "pop_em_muito_alta": 0})
    mt = m[m.faixa == "muito alta"]
    colunas_op = {"praça ou área pública": "mt_praca", "escola ou creche": "mt_escola",
                  "avenida com canteiro": "mt_avenida", "ruas locais": "mt_ruas_locais",
                  "sem registro no OpenStreetMap": "mt_sem_registro"}
    por_op = mt.groupby(["bairro", "oportunidade"]).size().unstack(fill_value=0).rename(columns=colunas_op)
    for c in colunas_op.values():
        resumo[c] = por_op[c] if c in por_op.columns else 0
    resumo[list(colunas_op.values())] = resumo[list(colunas_op.values())].fillna(0)
    resumo["pct_muito_alta"] = (100 * resumo.hex_muito_alta / resumo.hexagonos).round(1)
    resumo["top5_h3"] = m.sort_values("indice", ascending=False).groupby("bairro")["h3"].apply(lambda s: ";".join(s.head(5)))
    for c in ("hex_muito_alta", "hex_robustos", *colunas_op.values()):
        resumo[c] = resumo[c].astype(int)
    resumo = resumo.sort_values("pop_em_muito_alta", ascending=False)
    resumo.index.name = "bairro"
    resumo.to_csv(SAIDA / "bairros.csv")

    rel = relacao_vegetacao_calor(df)
    gravar_json("relacao.json", rel)

    def ler_cenas(nome):
        p = INTER / nome
        return pd.read_csv(p).to_dict("records") if p.exists() else []
    gravar_json("meta.json", {
        "gerado_em": dt.date.today().isoformat(),
        "grade": "H3 resolução 10 (~15 mil m² por hexágono)",
        "pesos": {"calor": pesos[0], "verde": pesos[1], "pessoas": pesos[2]},
        "pesos_alternativos_robustez": PESOS_ALTERNATIVOS,
        "hexagonos": int(len(df)), "elegiveis": int(el.sum()),
        "exclusoes": df[~el].excluido.value_counts().to_dict(),
        "fontes": {
            "calor": "USGS Landsat 8/9 C2 L2 (ST_B10), Microsoft Planetary Computer",
            "vegetacao": "ESA Copernicus Sentinel-2 L2A, Microsoft Planetary Computer",
            "populacao": "IBGE, Censo Demográfico 2022, agregados por setor censitário",
            "espaco": "© colaboradores do OpenStreetMap",
            "bairros": "IBGE, malha de bairros do Censo 2022",
        },
        "cenas_landsat": ler_cenas("cenas_landsat.csv"), "cenas_sentinel2": ler_cenas("cenas_sentinel2.csv"),
    })

    log(f"{el.sum()} de {len(df)} hexágonos elegíveis; exclusões: {df[~el].excluido.value_counts().to_dict()}")
    log(f"{int((df.faixa == 'muito alta').sum())} hexágonos de prioridade muito alta, "
        f"{int(df.robusto.sum())} robustos a qualquer conjunto de pesos")
    log(f"Relação vegetação–calor: {rel['graus_por_10pp']:+.2f} °C por +10 pp de cobertura "
        f"(IC 95% {rel['ic95_graus_por_10pp'][0]:+.2f} a {rel['ic95_graus_por_10pp'][1]:+.2f}; R² {rel['r2']})")
    log("Bairros com mais moradores em hexágonos de prioridade muito alta:")
    for b, r in resumo.head(8).iterrows():
        pessoas = f"{int(r.pop_em_muito_alta):,}".replace(",", ".")
        log(f"  {b}: {pessoas} pessoas, {r.hex_muito_alta} hexágonos — praça {r.mt_praca}, escola {r.mt_escola}, "
            f"avenida {r.mt_avenida}, ruas locais {r.mt_ruas_locais}, sem registro {r.mt_sem_registro}")
    contagem = mt["oportunidade"].value_counts().to_dict()
    log(f"Oportunidade nos hexágonos de prioridade muito alta: {contagem}")


if __name__ == "__main__":
    main()
