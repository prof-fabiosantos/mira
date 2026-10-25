"""Dados do Mirá para o agente: lê public/data/web.json e reproduz o cálculo do site.

O índice é recalculado com os mesmos pesos que a pessoa está vendo no site, para que a resposta
do agente bata com o mapa. Nenhum número vem do LLM: tudo sai daqui.
"""
import json
import unicodedata
from functools import lru_cache
from pathlib import Path

import h3

RAIZ = Path(__file__).resolve().parents[2]
WEB = RAIZ / "public" / "data" / "web.json"
NOME_FAIXA = ["baixa", "média", "alta", "muito alta"]
PESOS_PADRAO = (0.4, 0.3, 0.3)


@lru_cache(maxsize=1)
def D():
    d = json.loads(WEB.read_text(encoding="utf-8"))
    d["_idx"] = {c: i for i, c in enumerate(d["h3"])}
    d["_por_bairro"] = [[] for _ in d["bairros"]]
    for i, b in enumerate(d["bairro"]):
        d["_por_bairro"][b].append(i)
    d["_norm_bairros"] = sorted(((norm(n), k) for k, n in enumerate(d["bairros"])), key=lambda x: -len(x[0]))
    return d


def norm(s):
    s = unicodedata.normalize("NFKD", str(s).lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def milhar(n):
    return f"{n:,}".replace(",", ".")


def pesos_validos(p):
    try:
        v = tuple(max(0.0, min(1.0, float(p.get(k, d)))) for k, d in zip(("calor", "verde", "pessoas"), PESOS_PADRAO))
    except (TypeError, ValueError, AttributeError):
        v = PESOS_PADRAO
    s = sum(v)
    return PESOS_PADRAO if s <= 0 else tuple(round(x / s, 3) for x in v)


@lru_cache(maxsize=32)
def calcular(pesos):
    """Mesmo cálculo de app.js: índice, faixa e posições, para os pesos dados."""
    d = D()
    wc, wv, wp = pesos
    soma = (wc + wv + wp) or 1
    n = len(d["h3"])
    indice = [None] * n
    eleg = []
    for i in range(n):
        if d["excl"][i] != 0 or d["pc"][i] is None or d["pv"][i] is None or d["pp"][i] is None:
            continue
        indice[i] = 100 * (wc * d["pc"][i] + wv * d["pv"][i] + wp * d["pp"][i]) / (1000 * soma)
        eleg.append(i)
    eleg.sort(key=lambda i: -indice[i])
    faixa, rank, rank_b = [-1] * n, [0] * n, [0] * n
    cont = [0] * len(d["bairros"])
    for r, i in enumerate(eleg):
        rank[i] = r + 1
        pct = 1 - r / len(eleg)
        faixa[i] = 3 if pct > 0.9 else 2 if pct > 0.75 else 1 if pct > 0.5 else 0
        cont[d["bairro"][i]] += 1
        rank_b[i] = cont[d["bairro"][i]]
    return {"indice": indice, "faixa": faixa, "rank": rank, "rank_bairro": rank_b, "n_eleg": len(eleg), "ordem": eleg}


def bairros_no_texto(texto):
    """Bairros citados na pergunta, na ordem em que aparecem (casamento sem acento, o nome mais longo primeiro)."""
    d, t = D(), " " + norm(texto) + " "
    achados, ocupado = [], []
    for nome, k in d["_norm_bairros"]:
        pos = t.find(" " + nome + " ")
        while pos >= 0:
            fim = pos + len(nome) + 1
            if not any(a < fim and pos < b for a, b in ocupado):
                ocupado.append((pos, fim))
                achados.append((pos, k))
                break
            pos = t.find(" " + nome + " ", pos + 1)
    return [k for _, k in sorted(achados)]


def indice_bairro(nome):
    d = D()
    alvo = norm(nome or "")
    return next((k for n, k in d["_norm_bairros"] if n == alvo), None)


def latlng(i):
    la, lo = h3.cell_to_latlng(D()["h3"][i])
    return round(la, 5), round(lo, 5)


def link_mapa(i):
    la, lo = latlng(i)
    return f"https://www.google.com/maps/search/?api=1&query={la},{lo}"


def _get(campo, i):
    v = D().get(campo)
    return None if v is None else v[i]


def ficha(i, calc):
    d = D()
    excl = d["exclusoes"][d["excl"][i]] or None
    f = {
        "hexagono": d["h3"][i],
        "bairro": d["bairros"][d["bairro"][i]],
        "coordenadas": "%s, %s" % latlng(i),
        "fora_do_indice_motivo": excl,
        "superficie_vs_mediana_c": None if d["anom"][i] is None else d["anom"][i] / 10,
        "cobertura_vegetal_pct": d["cob"][i],
        "moradores": d["pop"][i],
        "idosos_e_criancas_pequenas": d["vuln"][i],
        "densidade_hab_km2": d["dens"][i],
        "oportunidade": d["oportunidades"][d["op"][i]],
        "pracas_m2": d["praca"][i], "ruas_m": d["vias"][i], "avenidas_m": d["viasp"][i], "escolas": d["escolas"][i],
    }
    if _get("copa", i) is not None:
        f["copa_arvores_pct_2010_2019"] = d["copa"][i]
        f["altura_media_copas_m"] = _get("altc", i)
        f["provavel_perda_de_copa"] = bool(_get("perda", i))
    if not excl and calc["indice"][i] is not None:
        f.update({
            "indice_0_100": round(calc["indice"][i]),
            "faixa_prioridade": NOME_FAIXA[calc["faixa"][i]],
            "posicao_na_cidade": f"{milhar(calc['rank'][i])}º de {milhar(calc['n_eleg'])}",
            "posicao_no_bairro": f"{calc['rank_bairro'][i]}º",
            "robusto_a_qualquer_peso": bool(d["robusto"][i]),
            "mais_quente_que_pct_dos_hexagonos": round(d["pc"][i] / 10),
            "menos_verde_que_pct_dos_hexagonos": round(d["pv"][i] / 10),
            "mais_moradores_que_pct_dos_hexagonos": round(d["pp"][i] / 10),
        })
    return f


def resumo_bairro(k, calc, n_top=5):
    d = D()
    hs = d["_por_bairro"][k]
    pop = sum(d["pop"][i] for i in hs)
    mt = [i for i in hs if calc["faixa"][i] == 3]
    terra = [i for i in hs if d["excl"][i] != 1]
    cob = [d["cob"][i] for i in terra if d["cob"][i] is not None]
    anom = [d["anom"][i] / 10 for i in terra if d["anom"][i] is not None]
    op = {}
    for i in mt:
        nome = d["oportunidades"][d["op"][i]]
        op[nome] = op.get(nome, 0) + 1
    top = sorted((i for i in hs if calc["indice"][i] is not None), key=lambda i: -calc["indice"][i])[:n_top]
    r = {
        "bairro": d["bairros"][k],
        "moradores": round(pop),
        "hexagonos": len(hs),
        "hexagonos_prioridade_muito_alta": len(mt),
        "pct_do_bairro_em_muito_alta": round(100 * len(mt) / len(hs), 1) if hs else 0,
        "moradores_em_muito_alta": round(sum(d["pop"][i] for i in mt)),
        "hexagonos_robustos": sum(1 for i in hs if d["robusto"][i]),
        "cobertura_vegetal_media_pct": round(sum(cob) / len(cob)) if cob else None,
        "superficie_media_vs_cidade_c": round(sum(anom) / len(anom), 1) if anom else None,
        "oportunidades_nos_muito_alta": op,
        "pontos_mais_prioritarios": [{
            "posicao_no_bairro": n + 1, "indice": round(calc["indice"][i]), "faixa": NOME_FAIXA[calc["faixa"][i]],
            "oportunidade": d["oportunidades"][d["op"][i]], "moradores": d["pop"][i],
            "superficie_vs_mediana_c": None if d["anom"][i] is None else d["anom"][i] / 10,
            "cobertura_vegetal_pct": d["cob"][i], "robusto": bool(d["robusto"][i]),
            "coordenadas": "%s, %s" % latlng(i), "hexagono": d["h3"][i],
        } for n, i in enumerate(top)],
    }
    if d.get("perda"):
        r["hexagonos_com_provavel_perda_de_copa"] = sum(1 for i in hs if d["perda"][i])
    return r


def media_cidade():
    d = D()
    cob = [d["cob"][i] for i in range(len(d["h3"])) if d["excl"][i] != 1 and d["cob"][i] is not None]
    return round(sum(cob) / len(cob))


def ranking_bairros(calc, n=5):
    d = D()
    lin = []
    for k, hs in enumerate(d["_por_bairro"]):
        mt = [i for i in hs if calc["faixa"][i] == 3]
        lin.append({"bairro": d["bairros"][k], "moradores_em_muito_alta": round(sum(d["pop"][i] for i in mt)),
                    "hexagonos_muito_alta": len(mt)})
    lin.sort(key=lambda x: -x["moradores_em_muito_alta"])
    return lin[:n]
