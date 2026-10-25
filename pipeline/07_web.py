"""Etapa 7 — dados compactos para o site (public/data/web.json).

Formato colunar: cada campo é uma lista com um valor por hexágono, na mesma ordem. Números são
inteiros escalados (por exemplo, percentil × 1000) para o arquivo ficar pequeno. O site recalcula o
índice a partir dos percentis quando o usuário muda os pesos.
"""
import json

import numpy as np
import pandas as pd

from comum import SAIDA, log

FAIXAS = ["baixa", "média", "alta", "muito alta"]
EXCLUSOES = ["", "água", "área aeroportuária", "vegetação densa (proteger)", "sem dado de satélite"]
OPORT = ["praça ou área pública", "escola ou creche", "avenida com canteiro", "ruas locais",
         "sem registro no OpenStreetMap"]


def inteiro(s, escala=1, nulo=None):
    v = (s * escala).round()
    return [None if pd.isna(x) else int(x) for x in v] if nulo is None else [nulo if pd.isna(x) else int(x) for x in v]


def main(argv=None):
    h = pd.read_csv(SAIDA / "hexagonos.csv", dtype={"h3": str})
    b = pd.read_csv(SAIDA / "bairros.csv")
    meta = json.loads((SAIDA / "meta.json").read_text(encoding="utf-8"))
    rel = json.loads((SAIDA / "relacao.json").read_text(encoding="utf-8"))
    nomes = sorted(h.bairro.unique())
    idx = {n: i for i, n in enumerate(nomes)}
    vuln = h[["idosos", "criancas"]].fillna(0).sum(axis=1)
    web = {
        "versao": 1,
        "gerado_em": meta["gerado_em"],
        "pesos": meta["pesos"],
        "bairros": nomes,
        "faixas": FAIXAS, "exclusoes": EXCLUSOES, "oportunidades": OPORT,
        "h3": h.h3.tolist(),
        "bairro": [idx[x] for x in h.bairro],
        "excl": [EXCLUSOES.index(x) if isinstance(x, str) and x in EXCLUSOES else 0 for x in h.excluido],
        "robusto": [1 if x else 0 for x in h.robusto],
        "pc": inteiro(h.p_calor, 1000), "pv": inteiro(h.p_verde, 1000), "pp": inteiro(h.p_pessoas, 1000),
        "anom": inteiro(h.lst_anom_c, 10),            # °C × 10
        "cob": inteiro(h.cobertura_vegetal, 100),     # %
        "pop": inteiro(h["pop"], 1, 0),
        "vuln": inteiro(vuln, 1, 0),                  # idosos + crianças de 0 a 4
        "dens": inteiro(h.dens_hab_km2, 1, 0),
        "op": [OPORT.index(x) if x in OPORT else 4 for x in h.oportunidade],
        "praca": inteiro(h.area_publica_m2, 1, 0), "vias": inteiro(h.vias_m, 1, 0),
        "viasp": inteiro(h.vias_principais_m, 1, 0), "escolas": inteiro(h.escolas, 1, 0),
    }
    resumo = []
    for _, r in b.iterrows():
        resumo.append({k: (None if pd.isna(v) else (v.item() if isinstance(v, np.generic) else v)) for k, v in r.items()})
    web["resumo_bairros"] = resumo
    web["relacao"] = {k: rel[k] for k in rel if k != "pontos_grafico"}
    web["relacao"]["pontos"] = rel["pontos_grafico"][:1200]
    web["meta"] = {k: meta[k] for k in ("grade", "hexagonos", "elegiveis", "exclusoes", "fontes",
                                         "pesos_alternativos_robustez")}
    web["meta"]["n_cenas_landsat"] = len(meta.get("cenas_landsat", []))
    web["meta"]["n_cenas_sentinel2"] = len(meta.get("cenas_sentinel2", []))
    destino = SAIDA / "web.json"
    destino.write_text(json.dumps(web, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"  gravado {destino.name}: {destino.stat().st_size / 1e6:.1f} MB para {len(h)} hexágonos")


if __name__ == "__main__":
    main()
