"""Exploração da fase 3 — copas de árvores com o mapa de altura de dossel da Meta/WRI (CHMv2).

O CHMv2 é um mapa global de altura das copas a ~1,2 m por pixel, gerado por um modelo de visão
(DINOv3) sobre imagens de satélite de alta resolução. É aberto (CC BY 4.0) e fica no Source
Cooperative: https://source.coop/tge-labs/meta-chm-v2

Este script NÃO altera o Mirá. Ele mede, por hexágono, a cobertura de copas (pixels com altura
≥ 3 m) e compara com a cobertura vegetal do Sentinel-2 (NDVI) usada hoje, para decidir se vale
trocar o fator "falta de verde" por "falta de copa".

Uso (na raiz do projeto, com o mesmo ambiente do pipeline + matplotlib):
    python explorar/copas_chmv2.py --bairro "Lírio do Vale"     # piloto, 1–3 min
    python explorar/copas_chmv2.py                               # cidade inteira, 10–30 min

Saídas em dados/copas/: copas_hexagonos.csv, resumo.txt e figuras PNG.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import requests
from pyproj import Transformer
from rasterio.windows import Window, from_bounds

RAIZ = Path(__file__).resolve().parent.parent
HEX_CSV = RAIZ / "public" / "data" / "hexagonos.csv"
SAIDA = RAIZ / "dados" / "copas"
CACHE = RAIZ / "cache" / "chmv2"
BASE = "https://data.source.coop/tge-labs/meta-chm-v2/"
RES = 10
ALTURA_COPA = 3.0   # m: abaixo disso é gramado, arbusto ou chão
F = 16              # agrega 16×16 pixels (~19 m) antes de atribuir ao hexágono
BLOCO = 4096        # pixels por bloco de leitura (múltiplo de F)
PESOS = (0.4, 0.3, 0.3)

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.tiff")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")


def log(msg):
    print(msg, flush=True)


# ---------- acesso aos dados ----------
def obter(nome, fonte):
    """Baixa (uma vez) um arquivo pequeno do repositório, ou usa uma pasta local (testes)."""
    if fonte:
        return Path(fonte) / nome
    destino = CACHE / nome
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        log(f"  baixando {nome}…")
        with requests.get(BASE + nome, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = destino.with_suffix(".part")
            with open(tmp, "wb") as f:
                for parte in r.iter_content(1 << 20):
                    f.write(parte)
            tmp.replace(destino)
    return destino


def caminho_gdal(url, fonte):
    url = str(url)
    if fonte and not url.startswith(("s3://", "http")):
        p = Path(url)
        return str(p if p.is_absolute() else Path(fonte) / p)
    if url.startswith("s3://us-west-2.opendata.source.coop/"):
        url = BASE.replace("tge-labs/meta-chm-v2/", "") + url.split("opendata.source.coop/", 1)[1]
    if url.startswith("s3://"):
        os.environ.setdefault("AWS_REGION", "us-west-2")
        return "/vsis3/" + url[5:]
    if url.startswith("http"):
        return "/vsicurl/" + url
    return BASE_REL(url)


def BASE_REL(rel):
    return "/vsicurl/" + BASE + rel.lstrip("/")


def tiles_na_area(bbox_ll, fonte):
    """Tiles (quadkey zoom 10) do CHMv2 que tocam a área, pelo índice tiles.parquet."""
    tab = pq.read_table(obter("tiles.parquet", fonte)).flatten().to_pandas()
    tab.columns = [c.replace(".", "_") for c in tab.columns]
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x0, y0 = t.transform(bbox_ll[0], bbox_ll[1])
    x1, y1 = t.transform(bbox_ll[2], bbox_ll[3])
    sel = tab[(tab.bbox_3857_minx < x1) & (tab.bbox_3857_maxx > x0) & (tab.bbox_3857_miny < y1) & (tab.bbox_3857_maxy > y0)]
    return sel, (x0, y0, x1, y1)


def datas_aquisicao(quadkeys, fonte):
    try:
        d = pq.read_table(obter("acq_dates.parquet", fonte)).to_pandas()
    except Exception as e:  # o arquivo é opcional para a análise
        return f"(não foi possível ler acq_dates.parquet: {e})"
    col = next((c for c in d.columns if "quadkey" in c.lower()), None)
    if col is None:
        return f"(acq_dates.parquet sem coluna quadkey; colunas: {list(d.columns)})"
    return d[d[col].astype(str).isin([str(q) for q in quadkeys])].to_string(index=False)


# ---------- raster → hexágono ----------
def celulas(transform, crs, shape):
    linhas, colunas = shape
    cols, rows = np.meshgrid(np.arange(colunas) + 0.5, np.arange(linhas) + 0.5)
    xs = transform.c + cols * transform.a
    ys = transform.f + rows * transform.e
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs.ravel(), ys.ravel())
    return np.array([h3.latlng_to_cell(la, lo, RES) for la, lo in zip(lat, lon)], dtype=object)


def processar_tile(caminho, area_3857, validas):
    """Soma, por hexágono: fração de copa, altura das copas e pixels com valor 0 (chão ou sem dado)."""
    partes = []
    with rasterio.open(caminho) as src:
        win = from_bounds(*area_3857, transform=src.transform)
        win = win.round_offsets().round_lengths().intersection(Window(0, 0, src.width, src.height))
        c0 = int(win.col_off) // F * F
        r0 = int(win.row_off) // F * F
        c1 = int(win.col_off + win.width)
        r1 = int(win.row_off + win.height)
        n_blocos = ((r1 - r0 + BLOCO - 1) // BLOCO) * ((c1 - c0 + BLOCO - 1) // BLOCO)
        k = 0
        for r in range(r0, r1, BLOCO):
            for c in range(c0, c1, BLOCO):
                k += 1
                h = min(BLOCO, r1 - r) // F * F
                w = min(BLOCO, c1 - c) // F * F
                if h == 0 or w == 0:
                    continue
                janela = Window(c, r, w, h)
                a = src.read(1, window=janela).astype(np.float32)
                copa = a >= ALTURA_COPA
                bl = lambda x: x.reshape(h // F, F, w // F, F).sum(axis=(1, 3))
                n_copa = bl(copa)
                soma_alt = bl(np.where(copa, a, 0))
                n_zero = bl(a == 0)
                tr = src.window_transform(janela) * rasterio.Affine.scale(F)
                cel = celulas(tr, src.crs, n_copa.shape)
                dentro = np.fromiter((x in validas for x in cel), bool, cel.size)
                if dentro.any():
                    partes.append(pd.DataFrame({
                        "h3": cel[dentro], "n_px": F * F,
                        "n_copa": n_copa.ravel()[dentro], "soma_alt": soma_alt.ravel()[dentro],
                        "n_zero": n_zero.ravel()[dentro],
                    }))
                if k % 10 == 0 or k == n_blocos:
                    log(f"    bloco {k}/{n_blocos}")
    return partes


def recorte_png(caminhos, bbox_ll, destino, titulo):
    """Imagem da altura das copas na área do bairro, para conferir a olho."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from rasterio.merge import merge
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x0, y0 = t.transform(bbox_ll[0], bbox_ll[1])
    x1, y1 = t.transform(bbox_ll[2], bbox_ll[3])
    fontes = [rasterio.open(c) for c in caminhos]
    try:
        arr, _ = merge(fontes, bounds=(x0, y0, x1, y1))
    finally:
        for s in fontes:
            s.close()
    a = arr[0].astype(float)
    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(np.ma.masked_equal(a, 0), cmap="YlGn", vmin=0, vmax=30)
    ax.set_title(titulo)
    ax.axis("off")
    fig.colorbar(im, ax=ax, shrink=0.7, label="altura da copa (m)")
    fig.savefig(destino, dpi=130, bbox_inches="tight")
    plt.close(fig)


# ---------- comparação ----------
def slug(nome):
    import unicodedata
    return unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower().replace(" ", "_")


def spearman(x, y):
    """Correlação de postos sem depender do scipy."""
    return x.rank().corr(y.rank())


def percentil(s):
    return s.rank(pct=True, method="average")


def ols(x, y):
    ok = x.notna() & y.notna()
    x, y = x[ok].to_numpy(), y[ok].to_numpy()
    b, a = np.polyfit(x, y, 1)
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    return b, r2, ok.sum()


def comparar(df, bairro):
    linhas = []
    p = linhas.append
    cob = df.cobertura_vegetal
    p(f"Hexágonos com copa medida: {df.copa.notna().sum()} de {len(df)}")
    p(f"Pixels com valor 0 (chão ou sem dado), média por hexágono: {100 * df.frac_zero.mean():.0f}%")
    p("")
    p("Cobertura média          NDVI ≥ 0,5 (atual)   copa ≥ 3 m (CHMv2)")
    p(f"  todos os hexágonos     {100 * cob.mean():5.1f}%              {100 * df.copa.mean():5.1f}%")
    el = df.excluido.isna()
    p(f"  elegíveis              {100 * cob[el].mean():5.1f}%              {100 * df.copa[el].mean():5.1f}%")
    p("")
    ok = cob.notna() & df.copa.notna()
    p(f"Correlação entre as duas coberturas: Pearson {cob[ok].corr(df.copa[ok]):.2f} · "
      f"Spearman {spearman(cob[ok], df.copa[ok]):.2f}")
    gram = (cob >= 0.5) & (df.copa < 0.2)
    p(f"'Verde que não é árvore' (NDVI diz ≥ 50% de vegetação, CHMv2 < 20% de copa): {gram.sum()} hexágonos")
    densa = df.excluido == "vegetação densa (proteger)"
    if densa.any():
        p(f"Excluídos como 'vegetação densa' com menos de 30% de copa: {(densa & (df.copa < 0.3)).sum()} de {densa.sum()}"
          " (candidatos a gramado/campo, que poderiam receber plantio)")
    p("")
    base = df[(df.fracao_agua.fillna(0) < 0.5) & (df.excluido.fillna("") != "área aeroportuária")]
    if len(base) > 30 and base.lst_anom_c.notna().sum() > 30:
        b1, r1, n1 = ols(base.cobertura_vegetal, base.lst_anom_c)
        b2, r2, n2 = ols(base.copa, base.lst_anom_c)
        p("Qual cobertura explica melhor o calor de superfície? (regressão simples, sem água nem aeroporto)")
        p(f"  NDVI:   {0.1 * b1:+.2f} °C por +10 pontos de cobertura · R² = {r1:.2f}  (n = {n1})")
        p(f"  copas:  {0.1 * b2:+.2f} °C por +10 pontos de copa      · R² = {r2:.2f}  (n = {n2})")
        p("")
    e = df[el & df.copa.notna()].copy()
    if len(e) > 30:
        e["p_verde_copa"] = percentil(-e.copa)
        wc, wv, wp = PESOS
        e["idx_atual"] = 100 * (wc * e.p_calor + wv * e.p_verde + wp * e.p_pessoas)
        e["idx_copa"] = 100 * (wc * e.p_calor + wv * e.p_verde_copa + wp * e.p_pessoas)
        topo_a = percentil(e.idx_atual) >= 0.9
        topo_c = percentil(e.idx_copa) >= 0.9
        p("Se o fator 'falta de verde' passasse a ser 'falta de copa' (pesos padrão, entre os elegíveis medidos):")
        p(f"  correlação de postos entre os dois índices: {spearman(e.idx_atual, e.idx_copa):.2f}")
        p(f"  prioridade muito alta: {topo_a.sum()} hoje → {topo_c.sum()} com copas; "
          f"{(topo_a & topo_c).sum()} continuam ({100 * (topo_a & topo_c).sum() / max(topo_a.sum(), 1):.0f}%), "
          f"{(~topo_a & topo_c).sum()} entram, {(topo_a & ~topo_c).sum()} saem")
        if e.bairro.nunique() > 1:
            g = e.assign(a=topo_a, c=topo_c).groupby("bairro")[["a", "c"]].sum()
            g["dif"] = g.c - g.a
            g = g.sort_values("dif")
            p("  bairros que mais perdem hexágonos de prioridade muito alta: " +
              ", ".join(f"{n} ({int(d):+d})" for n, d in g.dif.head(5).items() if d < 0))
            p("  bairros que mais ganham: " +
              ", ".join(f"{n} ({int(d):+d})" for n, d in g.dif.tail(5)[::-1].items() if d > 0))
        df = df.merge(e[["h3", "p_verde_copa", "idx_copa"]], on="h3", how="left")
    if bairro:
        b = df[df.bairro == bairro]
        p("")
        p(f"{bairro}: cobertura NDVI {100 * b.cobertura_vegetal.mean():.1f}% · copa {100 * b.copa.mean():.1f}% · "
          f"altura média das copas {b.altura_copa.mean():.1f} m")
    return "\n".join(linhas), df


def mapa_hex(df, colunas, titulos, destino, suptitulo):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    polis = [[(lo, la) for la, lo in h3.cell_to_boundary(c)] for c in df.h3]
    fig, axes = plt.subplots(1, len(colunas), figsize=(5.2 * len(colunas), 5))
    for ax, col, tit in zip(np.atleast_1d(axes), colunas, titulos):
        v = df[col].to_numpy(dtype=float)
        dif = col.startswith("dif")
        pc = PolyCollection(polis, array=np.ma.masked_invalid(v), cmap="RdBu" if dif else "YlGn",
                            edgecolor="white", linewidth=0.3)
        pc.set_clim(-0.5, 0.5) if dif else pc.set_clim(0, 1)
        ax.add_collection(pc)
        ax.autoscale_view()
        ax.set_aspect(1 / np.cos(np.radians(df.lat.mean())))
        ax.set_title(tit, fontsize=11)
        ax.axis("off")
        fig.colorbar(pc, ax=ax, shrink=0.75)
    fig.suptitle(suptitulo)
    fig.savefig(destino, dpi=130, bbox_inches="tight")
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bairro", help="processa só este bairro (piloto rápido)")
    ap.add_argument("--fonte", help=argparse.SUPPRESS)  # pasta local com tiles.parquet e COGs (testes)
    a = ap.parse_args(argv)

    t0 = time.time()
    SAIDA.mkdir(parents=True, exist_ok=True)
    hexs = pd.read_csv(HEX_CSV, dtype={"h3": str})
    if a.bairro:
        if a.bairro not in set(hexs.bairro):
            sys.exit(f"Bairro '{a.bairro}' não encontrado. Exemplos: {', '.join(sorted(hexs.bairro.unique())[:8])}…")
        area = hexs[hexs.bairro == a.bairro].copy()
    else:
        area = hexs.copy()
    ll = np.array([h3.cell_to_latlng(c) for c in area.h3])
    area["lat"], area["lon"] = ll[:, 0], ll[:, 1]
    m = 0.002
    bbox = (area.lon.min() - m, area.lat.min() - m, area.lon.max() + m, area.lat.max() + m)

    log(f"1/4 Localizando os tiles do CHMv2 para {a.bairro or 'Manaus inteira'} ({len(area)} hexágonos)")
    tiles, area_3857 = tiles_na_area(bbox, a.fonte)
    if tiles.empty:
        sys.exit("Nenhum tile do CHMv2 cobre a área.")
    url_col = next(c for c in tiles.columns if c in ("cog_url", "href", "url"))
    caminhos = [caminho_gdal(u, a.fonte) for u in tiles[url_col]]
    log(f"  {len(tiles)} tile(s): {', '.join(map(str, tiles.quadkey))}")
    datas = datas_aquisicao(tiles.quadkey.tolist(), a.fonte)
    log("  datas das imagens de origem:\n" + datas)

    log("2/4 Lendo as alturas (~1,2 m) e agregando por hexágono")
    validas = set(area.h3)
    partes = []
    for i, cam in enumerate(caminhos, 1):
        log(f"  tile {i}/{len(caminhos)}")
        partes += processar_tile(cam, area_3857, validas)
    if not partes:
        sys.exit("Nenhum pixel caiu na área.")
    s = pd.concat(partes).groupby("h3")[["n_px", "n_copa", "soma_alt", "n_zero"]].sum()
    copas = pd.DataFrame({
        "copa": s.n_copa / s.n_px,
        "altura_copa": (s.soma_alt / s.n_copa.where(s.n_copa > 0)),
        "frac_zero": s.n_zero / s.n_px,
    }).reset_index()
    df = area.merge(copas, on="h3", how="left")

    log("3/4 Comparando com a cobertura vegetal do Sentinel-2")
    resumo, df = comparar(df, a.bairro or "Lírio do Vale")
    sufixo = "_" + slug(a.bairro) if a.bairro else ""
    cab = (f"Mirá · exploração de copas com o CHMv2 (Meta/WRI) · {a.bairro or 'Manaus'}\n"
           f"Copa = pixel com altura ≥ {ALTURA_COPA:.0f} m. Dados: CHMv2, CC BY 4.0 (Tolan et al. 2024; Brandt et al. 2026).\n"
           f"Tiles: {', '.join(map(str, tiles.quadkey))}\nDatas das imagens de origem:\n{datas}\n\n")
    (SAIDA / f"resumo{sufixo}.txt").write_text(cab + resumo + "\n", encoding="utf-8")
    cols = ["h3", "bairro", "excluido", "faixa", "indice", "cobertura_vegetal", "copa", "altura_copa", "frac_zero",
            "lst_anom_c", "p_verde"] + [c for c in ("p_verde_copa", "idx_copa") if c in df.columns]
    df[cols].round(4).to_csv(SAIDA / f"copas_hexagonos{sufixo}.csv", index=False)

    log("4/4 Figuras")
    alvo = a.bairro or "Lírio do Vale"
    b = df[df.bairro == alvo].copy()
    b["dif"] = b.copa - b.cobertura_vegetal
    mapa_hex(b, ["cobertura_vegetal", "copa", "dif"],
             ["Cobertura vegetal (NDVI ≥ 0,5, hoje)", "Copa de árvore (CHMv2, ≥ 3 m)", "Diferença (copa − NDVI)"],
             SAIDA / f"comparacao_hex{sufixo or '_' + slug(alvo)}.png", alvo)
    try:
        ll_b = np.array([h3.cell_to_latlng(c) for c in b.h3])
        bb = (ll_b[:, 1].min() - m, ll_b[:, 0].min() - m, ll_b[:, 1].max() + m, ll_b[:, 0].max() + m)
        recorte_png(caminhos, bb, SAIDA / f"alturas_{slug(alvo)}.png",
                    f"Altura das copas · {alvo} (CHMv2, ~1,2 m)")
    except Exception as e:
        log(f"  (recorte em imagem não gerado: {e})")
    log("\n" + resumo)
    log(f"\nPronto em {time.time() - t0:.0f} s. Arquivos em {SAIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
