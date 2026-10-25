/* Mirá — onde plantar árvores em Manaus. Lê data/web.json (gerado pela etapa 7 do pipeline). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const fmt = (v, d = 0) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const sinal = (v, d = 1) => (v >= 0 ? "+" : "−") + fmt(Math.abs(v), d);
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  let D, N, B, cent, idxCell, bairroHex;
  let pesos = { calor: 0.4, verde: 0.3, pessoas: 0.3 };
  let indice, faixa, rankCidade, rankBairro, nEleg;
  let camada = "prioridade", soRobustos = false, sel = null, map, layer;

  const COR_FAIXA = () => [css("--f0"), css("--f1"), css("--f2"), css("--f3")];
  const COR_EXCL = () => [null, css("--agua"), css("--aero"), css("--mata"), css("--semdado")];
  const COR_OP = ["#3f8f4f", "#3f6fb0", "#c07a2c", "#9a8f80", "#cfcac0"];
  const NOME_FAIXA = ["Baixa", "Média", "Alta", "Muito alta"];

  // ---------- cálculo do índice com os pesos atuais ----------
  function calcular() {
    const soma = pesos.calor + pesos.verde + pesos.pessoas || 1;
    indice = new Float32Array(N).fill(NaN);
    const eleg = [];
    for (let i = 0; i < N; i++) {
      if (D.excl[i] !== 0 || D.pc[i] == null || D.pv[i] == null || D.pp[i] == null) continue;
      indice[i] = (100 * (pesos.calor * D.pc[i] + pesos.verde * D.pv[i] + pesos.pessoas * D.pp[i])) / (1000 * soma);
      eleg.push(i);
    }
    nEleg = eleg.length;
    eleg.sort((a, b) => indice[b] - indice[a]);
    faixa = new Int8Array(N).fill(-1);
    rankCidade = new Int32Array(N).fill(0);
    eleg.forEach((i, r) => {
      rankCidade[i] = r + 1;
      const pct = 1 - r / nEleg; // fração de hexágonos com índice menor ou igual
      faixa[i] = pct > 0.9 ? 3 : pct > 0.75 ? 2 : pct > 0.5 ? 1 : 0;
    });
    rankBairro = new Int32Array(N).fill(0);
    const cont = new Int32Array(B.length);
    eleg.forEach((i) => { cont[D.bairro[i]]++; rankBairro[i] = cont[D.bairro[i]]; });
  }

  // ---------- cores por camada ----------
  function corDe(i) {
    if (camada === "prioridade") {
      if (D.excl[i]) return COR_EXCL()[D.excl[i]];
      return faixa[i] >= 0 ? COR_FAIXA()[faixa[i]] : css("--semdado");
    }
    if (camada === "calor") {
      if (D.anom[i] == null) return css("--semdado");
      return rampa(RAMPA_CALOR, Math.max(-6, Math.min(6, D.anom[i] / 10)), -6, 6);
    }
    if (camada === "verde") {
      if (D.cob[i] == null) return css("--semdado");
      return rampa(RAMPA_VERDE, D.cob[i], 0, 100);
    }
    const d = D.dens[i];
    return d < 10 ? css("--semdado") : rampa(RAMPA_PESSOAS, Math.log10(d), 1, 4.5);
  }
  const RAMPA_CALOR = [[44, 98, 160], [130, 180, 210], [240, 236, 220], [235, 150, 90], [170, 35, 35]];
  const RAMPA_VERDE = [[245, 240, 220], [190, 220, 160], [110, 175, 100], [45, 120, 60], [20, 70, 35]];
  const RAMPA_PESSOAS = [[238, 236, 245], [200, 190, 225], [150, 125, 195], [100, 70, 160], [60, 20, 110]];
  function rampa(st, v, lo, hi) {
    const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo))) * (st.length - 1);
    const k = Math.min(st.length - 2, Math.floor(t)), f = t - k;
    // quantiza em passos de 8 para agrupar os hexágonos por cor e desenhar menos caminhos
    const c = st[k].map((x, j) => Math.round((x + (st[k + 1][j] - x) * f) / 8) * 8);
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }

  // ---------- camada de hexágonos em canvas ----------
  const HexLayer = L.Layer.extend({
    onAdd(m) {
      this._m = m;
      this._c = L.DomUtil.create("canvas", "leaflet-zoom-hide");
      this._c.style.position = "absolute";
      m.getPane("overlayPane").appendChild(this._c);
      this._cache = new Map();
      m.on("moveend zoomend resize viewreset", this._reset, this);
      this._reset();
    },
    onRemove(m) { L.DomUtil.remove(this._c); m.off("moveend zoomend resize viewreset", this._reset, this); },
    _proj(z) {
      if (this._cache.has(z)) return this._cache.get(z);
      const p = new Float64Array(N * 12), m = this._m;
      for (let i = 0; i < N * 6; i++) {
        const pt = m.project([BORDA[2 * i], BORDA[2 * i + 1]], z);
        p[2 * i] = pt.x; p[2 * i + 1] = pt.y;
      }
      if (this._cache.size > 3) this._cache.delete(this._cache.keys().next().value);
      this._cache.set(z, p);
      return p;
    },
    _reset() {
      const m = this._m, s = m.getSize(), dpr = window.devicePixelRatio || 1;
      this._c.width = s.x * dpr; this._c.height = s.y * dpr;
      this._c.style.width = s.x + "px"; this._c.style.height = s.y + "px";
      L.DomUtil.setPosition(this._c, m.containerPointToLayerPoint([0, 0]));
      this.redraw();
    },
    redraw() {
      if (!this._m || !faixa) return;  // o índice ainda não foi calculado
      const m = this._m, z = m.getZoom(), p = this._proj(z), o = m.getPixelBounds().min, s = m.getSize();
      const ctx = this._c.getContext("2d"), dpr = window.devicePixelRatio || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, s.x, s.y);
      const grupos = new Map();
      for (let i = 0; i < N; i++) {
        const x = p[12 * i] - o.x, y = p[12 * i + 1] - o.y;
        if (x < -60 || y < -60 || x > s.x + 60 || y > s.y + 60) continue;
        const cor = corDe(i);
        if (!cor) continue;
        const apagado = soRobustos && camada === "prioridade" && !D.excl[i] && !D.robusto[i];
        const chave = (apagado ? "a" : "") + cor;
        if (!grupos.has(chave)) grupos.set(chave, []);
        grupos.get(chave).push(i);
      }
      const traco = z >= 14;
      for (const [chave, lista] of grupos) {
        const apagado = chave[0] === "a";
        ctx.globalAlpha = apagado ? 0.18 : 0.88;
        ctx.fillStyle = apagado ? chave.slice(1) : chave;
        ctx.beginPath();
        for (const i of lista) {
          ctx.moveTo(p[12 * i] - o.x, p[12 * i + 1] - o.y);
          for (let k = 1; k < 6; k++) ctx.lineTo(p[12 * i + 2 * k] - o.x, p[12 * i + 2 * k + 1] - o.y);
          ctx.closePath();
        }
        ctx.fill();
        if (traco) { ctx.globalAlpha = 0.5; ctx.strokeStyle = css("--surface"); ctx.lineWidth = 0.6; ctx.stroke(); }
      }
      ctx.globalAlpha = 1;
      if (sel != null) {
        ctx.beginPath();
        ctx.moveTo(p[12 * sel] - o.x, p[12 * sel + 1] - o.y);
        for (let k = 1; k < 6; k++) ctx.lineTo(p[12 * sel + 2 * k] - o.x, p[12 * sel + 2 * k + 1] - o.y);
        ctx.closePath();
        ctx.lineWidth = 3; ctx.strokeStyle = css("--ink"); ctx.stroke();
        ctx.lineWidth = 1.2; ctx.strokeStyle = css("--surface"); ctx.stroke();
      }
    },
  });
  let BORDA;

  // ---------- legenda ----------
  function legenda() {
    const L_ = $("legenda");
    if (camada === "prioridade") {
      const f = COR_FAIXA(), e = COR_EXCL();
      L_.innerHTML = NOME_FAIXA.map((n, k) => `<span><i style="background:${f[k]}"></i>${n}</span>`).join("") +
        `<span><i style="background:${e[3]}"></i>Vegetação densa (proteger)</span><span><i style="background:${e[1]}"></i>Água</span>` +
        `<span><i style="background:${e[2]}"></i>Aeroporto</span>`;
    } else {
      const cfg = { calor: [RAMPA_CALOR, "−6 °C", "+6 °C", "Superfície em relação à mediana da cidade (Landsat, estação seca)"],
        verde: [RAMPA_VERDE, "0%", "100%", "Cobertura vegetal (Sentinel-2)"],
        pessoas: [RAMPA_PESSOAS, "10", "30 mil+", "Moradores por km² (Censo 2022)"] }[camada];
      const g = `linear-gradient(90deg,${cfg[0].map((c) => `rgb(${c})`).join(",")})`;
      L_.innerHTML = `<span class="rampa">${cfg[1]}<span class="barra" style="background:${g}"></span>${cfg[2]}</span><span>${cfg[3]}</span>`;
    }
  }

  // ---------- ficha do hexágono ----------
  function ficha(i) {
    const lat = cent[2 * i], lng = cent[2 * i + 1], b = B[D.bairro[i]];
    const gmaps = `https://www.google.com/maps/search/?api=1&query=${lat.toFixed(6)},${lng.toFixed(6)}`;
    const anom = D.anom[i] != null ? D.anom[i] / 10 : null, cob = D.cob[i], pop = D.pop[i], vuln = D.vuln[i];
    let topo, texto;
    if (D.excl[i]) {
      const motivo = D.exclusoes[D.excl[i]];
      const t = {
        "água": "Este hexágono é principalmente água.",
        "área aeroportuária": "Fica em área aeroportuária, onde árvores não são indicadas.",
        "vegetação densa (proteger)": `Já tem vegetação densa${cob != null ? ` (${cob}% de cobertura)` : ""}. Aqui a prioridade é proteger a vegetação que existe, não plantar.`,
        "sem dado de satélite": "Não há cenas de satélite sem nuvem suficientes para este hexágono.",
      }[motivo] || motivo;
      topo = `<span class="pill" style="color:var(--muted)">Fora do índice: ${motivo}</span>`;
      texto = `<p class="explica">${t}</p>`;
    } else {
      const f = faixa[i], cor = f === 3 ? "var(--t-f3)" : f === 2 ? "var(--t-f2)" : "var(--muted)";
      const quente = Math.round(D.pc[i] / 10), menosVerde = Math.round(D.pv[i] / 10), mais = Math.round(D.pp[i] / 10);
      topo = `<div class="indice">${fmt(indice[i], 0)}<span class="sub" style="font-size:14px;font-weight:400"> de 100</span></div>
        <span class="pill" style="color:${cor}">Prioridade ${NOME_FAIXA[f].toLowerCase()}</span>${D.robusto[i] ? '<span class="pill" style="color:var(--accent)">Robusto</span>' : ""}
        <p class="sub">${fmt(rankCidade[i])}º de ${fmt(nEleg)} na cidade · ${fmt(rankBairro[i])}º em ${b}</p>`;
      texto = `<p class="explica">Mais quente que ${quente}% dos hexágonos da cidade (superfície ${sinal(anom)} °C em relação à mediana), com ${cob}% de cobertura vegetal, menos verde que ${menosVerde}% deles. Moram aqui cerca de ${fmt(pop)} pessoas${vuln ? `, ${fmt(vuln)} delas idosas ou crianças pequenas` : ""}, mais que em ${mais}% dos hexágonos.</p>` +
        [["Calor", D.pc[i], `${sinal(anom)} °C`, "var(--f2)"], ["Falta de verde", D.pv[i], `${cob}% de cobertura`, "var(--mata)"],
         ["Moradores", D.pp[i], `${fmt(D.dens[i])} hab/km²`, "#6a4fa8"]]
          .map(([n, p, v, c]) => `<div class="fator"><div class="rot"><b>${n}</b><span>${v} · percentil ${Math.round(p / 10)}</span></div><div class="barra"><i style="width:${p / 10}%;background:${c}"></i></div></div>`).join("");
    }
    const op = D.oportunidades[D.op[i]];
    $("ficha").innerHTML = `<h2>${b}</h2>${topo}${texto}
      <dl class="kv">
        <div><dt>Oportunidade</dt><dd>${op}</dd></div>
        <div><dt>Praças e áreas públicas</dt><dd>${fmt(D.praca[i])} m²</dd></div>
        <div><dt>Ruas (avenidas)</dt><dd>${fmt(D.vias[i])} m (${fmt(D.viasp[i])} m)</dd></div>
        <div><dt>Escolas e creches</dt><dd>${D.escolas[i]}</dd></div>
      </dl>
      <div class="controles"><a class="btn" href="${gmaps}" target="_blank" rel="noopener">Ver o local no Google Maps</a>
      <button class="btn" type="button" id="abrirRel">Relatório de ${b}</button></div>
      <p class="sub" style="font-family:var(--f-data);font-size:11px">Hexágono ${D.h3[i]} · ${lat.toFixed(5)}, ${lng.toFixed(5)}</p>`;
    $("abrirRel").addEventListener("click", () => relatorio(D.bairro[i], true));
  }

  // ---------- agregados por bairro ----------
  function agregar() {
    const A = B.map((nome, k) => ({ k, nome, hex: 0, pop: 0, hexMt: 0, popMt: 0, rob: 0, cobS: 0, cobN: 0, anomS: 0, anomN: 0, op: [0, 0, 0, 0, 0] }));
    for (let i = 0; i < N; i++) {
      const a = A[D.bairro[i]];
      a.hex++; a.pop += D.pop[i];
      if (D.excl[i] !== 1) {
        if (D.cob[i] != null) { a.cobS += D.cob[i]; a.cobN++; }
        if (D.anom[i] != null) { a.anomS += D.anom[i] / 10; a.anomN++; }
      }
      if (faixa[i] === 3) { a.hexMt++; a.popMt += D.pop[i]; a.op[D.op[i]]++; }
      if (D.robusto[i]) a.rob++;
    }
    A.forEach((a) => { a.pctMt = (100 * a.hexMt) / a.hex; a.cob = a.cobN ? a.cobS / a.cobN : null; a.anom = a.anomN ? a.anomS / a.anomN : null; });
    return A;
  }
  let ordem = { k: "popMt", asc: false }, AG;
  function tabela() {
    AG = agregar();
    const lista = [...AG].sort((x, y) => {
      const a = x[ordem.k], b = y[ordem.k];
      const r = typeof a === "string" ? a.localeCompare(b, "pt") : (a ?? -1e9) - (b ?? -1e9);
      return ordem.asc ? r : -r;
    });
    document.querySelectorAll("#bairros th").forEach((th) => {
      if (th.dataset.k === ordem.k) th.setAttribute("aria-sort", ordem.asc ? "ascending" : "descending"); else th.removeAttribute("aria-sort");
    });
    $("tBairros").innerHTML = lista.map((a) => {
      const tot = a.op.reduce((s, v) => s + v, 0) || 1;
      const barra = a.op.map((v, j) => v ? `<i style="width:${(100 * v) / tot}%;background:${COR_OP[j]}" title="${D.oportunidades[j]}: ${v}"></i>` : "").join("");
      return `<tr data-k="${a.k}" tabindex="0"><td>${a.nome}</td><td class="n">${fmt(a.popMt)}</td><td class="n">${fmt(a.hexMt)}</td>
        <td class="n">${fmt(a.pctMt, 1)}%</td><td class="n">${fmt(a.rob)}</td><td class="n">${a.cob == null ? "—" : fmt(a.cob, 0) + "%"}</td>
        <td class="n">${a.anom == null ? "—" : sinal(a.anom) + " °C"}</td><td>${a.hexMt ? `<span class="mini">${barra}</span>` : "—"}</td></tr>`;
    }).join("");
    $("tBairros").querySelectorAll("tr").forEach((tr) => {
      const abrir = () => relatorio(+tr.dataset.k, true);
      tr.addEventListener("click", abrir);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") abrir(); });
    });
    $("legOp").innerHTML = D.oportunidades.map((n, j) => `<span><i style="background:${COR_OP[j]}"></i>${n}</span>`).join("");
  }

  // ---------- relatório do bairro ----------
  let relAberto = null;
  function relatorio(k, rolar) {
    relAberto = k;
    const a = agregar()[k], nome = B[k];
    const cidade = agregar().reduce((s, x) => ({ cobS: s.cobS + x.cobS, cobN: s.cobN + x.cobN }), { cobS: 0, cobN: 0 });
    const cobCidade = cidade.cobS / cidade.cobN;
    const top = [];
    for (let i = 0; i < N; i++) if (D.bairro[i] === k && faixa[i] >= 0) top.push(i);
    top.sort((x, y) => indice[y] - indice[x]);
    const FRASE_OP = ["com praça ou área pública", "com escola ou creche", "em avenida com canteiro", "só com ruas locais", "sem registro no OpenStreetMap"];
    const opTxt = a.hexMt ? FRASE_OP.map((n, j) => a.op[j] ? `${fmt(a.op[j])} ${n}` : "").filter(Boolean).join(", ") : "";
    const hoje = new Date().toLocaleDateString("pt-BR");
    $("relConteudo").innerHTML = `
      <div class="cab"><div><h2>Relatório de prioridade de plantio: ${nome}</h2><p class="sub" style="margin:0">Mirá · onde plantar árvores em Manaus</p>
        <p class="sub">Manaus · gerado em ${hoje} · pesos: calor ${Math.round(pesos.calor * 100)}%, falta de verde ${Math.round(pesos.verde * 100)}%, moradores ${Math.round(pesos.pessoas * 100)}% · dados de ${D.gerado_em.split("-").reverse().join("/")}</p></div>
        <div class="controles noprint"><button class="btn" type="button" id="relMapa">Ver no mapa</button><button class="btn prim" type="button" id="relImprimir">Imprimir ou salvar PDF</button></div></div>
      <div class="nums">
        <div><b>${fmt(a.pop)}</b><span>moradores no bairro</span></div>
        <div><b>${fmt(a.hexMt)}</b><span>hexágonos de prioridade muito alta (${fmt(a.pctMt, 1)}% do bairro)</span></div>
        <div><b>${fmt(a.popMt)}</b><span>moradores nesses hexágonos</span></div>
        <div><b>${fmt(a.rob)}</b><span>hexágonos robustos a qualquer peso</span></div>
      </div>
      <p>${a.hexMt
        ? `Em ${nome}, ${fmt(a.hexMt)} hexágonos de cerca de 120 m estão entre os 10% mais prioritários de Manaus para o plantio de árvores, e neles moram cerca de ${fmt(a.popMt)} pessoas.`
        : `${nome} não tem hexágonos entre os 10% mais prioritários da cidade com os pesos atuais.`}
        A cobertura vegetal média do bairro é de ${a.cob == null ? "—" : fmt(a.cob, 0) + "%"} (cidade: ${fmt(cobCidade, 0)}%), e a superfície fica, em média, ${a.anom == null ? "—" : sinal(a.anom) + " °C"} em relação à mediana da cidade na estação seca.
        ${opTxt ? `Entre os hexágonos de prioridade muito alta: ${opTxt}.` : ""}</p>
      <h3>Os ${Math.min(10, top.length)} pontos mais prioritários do bairro</h3>
      <div class="tabela"><table><thead><tr><th class="n">#</th><th class="n">Índice</th><th>Faixa</th><th>Robusto</th><th class="n">Superfície</th><th class="n">Cobertura</th><th class="n">Moradores</th><th>Oportunidade</th><th>Local</th></tr></thead><tbody>
      ${top.slice(0, 10).map((i, r) => `<tr><td class="n">${r + 1}</td><td class="n">${fmt(indice[i], 0)}</td><td>${NOME_FAIXA[faixa[i]]}</td><td>${D.robusto[i] ? "sim" : "—"}</td>
        <td class="n">${sinal(D.anom[i] / 10)} °C</td><td class="n">${D.cob[i]}%</td><td class="n">${fmt(D.pop[i])}</td><td>${D.oportunidades[D.op[i]]}</td>
        <td><a href="https://www.google.com/maps/search/?api=1&query=${cent[2 * i].toFixed(6)},${cent[2 * i + 1].toFixed(6)}" target="_blank" rel="noopener">${cent[2 * i].toFixed(5)}, ${cent[2 * i + 1].toFixed(5)}</a></td></tr>`).join("")}
      </tbody></table></div>
      <p class="sub" style="margin-top:12px">Índice de 0 a 100 que combina calor de superfície (Landsat), falta de vegetação (Sentinel-2) e moradores (Censo 2022, com peso extra para idosos e crianças de 0 a 4 anos). Indica onde priorizar; espécies e viabilidade de cada local (calçada, fiação, tubulação) dependem de avaliação técnica em campo. Metodologia e dados abertos no site.</p>`;
    $("relatorio").hidden = false;
    $("relMapa").addEventListener("click", () => { irPara(k); $("mapa-sec").scrollIntoView({ behavior: "smooth" }); });
    $("relImprimir").addEventListener("click", () => { document.body.classList.add("imprimir"); window.print(); });
    if (rolar) $("relatorio").scrollIntoView({ behavior: "smooth" });
  }
  window.addEventListener("afterprint", () => document.body.classList.remove("imprimir"));

  function irPara(k) {
    const h = bairroHex[k];
    let a = 90, b = -90, c = 180, d = -180;
    for (const i of h) { a = Math.min(a, cent[2 * i]); b = Math.max(b, cent[2 * i]); c = Math.min(c, cent[2 * i + 1]); d = Math.max(d, cent[2 * i + 1]); }
    map.fitBounds([[a, c], [b, d]], { padding: [20, 20] });
  }

  // ---------- números do topo e gráficos ----------
  function numeros() {
    let mt = 0, popMt = 0;
    for (let i = 0; i < N; i++) if (faixa[i] === 3) { mt++; popMt += D.pop[i]; }
    const r = D.relacao;
    $("numeros").innerHTML = `
      <div><b>${fmt(nEleg)}</b><span>hexágonos onde plantar faz sentido, de ${fmt(N)} analisados</span></div>
      <div><b>${fmt(mt)}</b><span>de prioridade muito alta (10% do total)</span></div>
      <div><b>${fmt(popMt)}</b><span>moradores nessas áreas</span></div>
      <div><b>${fmt(Math.abs(r.graus_por_10pp), 2)} °C</b><span>a menos de superfície para cada +10 pontos de cobertura vegetal</span></div>`;
  }
  const NS = "http://www.w3.org/2000/svg";
  const el = (t, a, p) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  function graficoRelacao() {
    const r = D.relacao, svg = $("relGrafico"); svg.innerHTML = "";
    $("relTexto").textContent = `Em ${fmt(r.hexagonos)} hexágonos de terra, cada +10 pontos percentuais de cobertura vegetal se associam a ${fmt(Math.abs(r.graus_por_10pp), 2)} °C a menos de temperatura de superfície (intervalo de 95%: ${fmt(Math.abs(r.ic95_graus_por_10pp[1]), 2)} a ${fmt(Math.abs(r.ic95_graus_por_10pp[0]), 2)} °C; R² ${fmt(r.r2, 2)}). É uma associação observada na estação seca, perto das 10h, e não uma promessa de quanto cada árvore resfria o ar.`;
    const W = 520, H = 330, l = 46, rr = 12, t = 12, b = 40;
    const X = (v) => l + v * (W - l - rr), Y = (v) => t + (1 - (Math.max(-8, Math.min(8, v)) + 8) / 16) * (H - t - b);
    [-8, -4, 0, 4, 8].forEach((v) => { el("line", { x1: l, x2: W - rr, y1: Y(v), y2: Y(v), stroke: "var(--line)" }, svg); el("text", { x: l - 6, y: Y(v) + 4, "text-anchor": "end", class: "eixo" }, svg).textContent = (v > 0 ? "+" : "") + v + "°"; });
    [0, 0.25, 0.5, 0.75, 1].forEach((v) => el("text", { x: X(v), y: H - 22, "text-anchor": "middle", class: "eixo" }, svg).textContent = Math.round(v * 100) + "%");
    el("text", { x: (l + W) / 2, y: H - 4, "text-anchor": "middle", class: "eixo" }, svg).textContent = "cobertura vegetal do hexágono";
    const g = el("g", { fill: css("--f2"), "fill-opacity": "0.35" }, svg);
    let sx = 0, sy = 0, sxx = 0, sxy = 0;
    r.pontos.forEach(([x, y]) => { el("circle", { cx: X(x), cy: Y(y), r: 2.2 }, g); sx += x; sy += y; sxx += x * x; sxy += x * y; });
    const n = r.pontos.length, inc = r.graus_por_10pp * 10, b0 = (sy - inc * sx) / n;
    el("line", { x1: X(0), y1: Y(b0), x2: X(1), y2: Y(b0 + inc), stroke: "var(--ink)", "stroke-width": 2.5 }, svg);
  }
  function graficoOportunidades() {
    const svg = $("opGrafico"); svg.innerHTML = "";
    const c = [0, 0, 0, 0, 0];
    for (let i = 0; i < N; i++) if (faixa[i] === 3) c[D.op[i]]++;
    const tot = c.reduce((a, b) => a + b, 0) || 1, max = Math.max(...c, 1);
    // rótulos curtos à esquerda e espaço reservado à direita para o número, sem cortes
    const ROT = ["praça ou área pública", "escola ou creche", "avenida com canteiro", "ruas locais", "sem registro no OSM"];
    const W = 520, l = 172, dir = 92, h = 34, larg = W - l - dir;
    c.forEach((v, j) => {
      const y = 10 + j * (h + 8), w = Math.max(2, (larg * v) / max);
      el("text", { x: l - 10, y: y + h / 2 + 4, "text-anchor": "end", class: "eixo", style: "font-size:11.5px" }, svg).textContent = ROT[j];
      el("rect", { x: l, y, width: w, height: h, rx: 4, fill: COR_OP[j] }, svg);
      el("text", { x: l + w + 8, y: y + h / 2 + 4, class: "eixo", style: "font-size:11.5px;fill:var(--ink)" }, svg).textContent = `${fmt(v)} (${fmt((100 * v) / tot, 0)}%)`;
    });
  }
  function metodologia() {
    const m = D.meta;
    $("metLista").innerHTML = [
      `<strong>Grade:</strong> ${m.grade}, dentro dos 63 bairros da malha do IBGE (Censo 2022): ${fmt(m.hexagonos)} hexágonos, ${fmt(m.elegiveis)} elegíveis.`,
      `<strong>Calor:</strong> temperatura de superfície do Landsat 8/9 (banda térmica, resolução nativa de 100 m), mediana de ${m.n_cenas_landsat} cenas sem nuvem da estação seca (julho a outubro), em relação à mediana da cidade em cada cena. Não é a temperatura do ar.`,
      `<strong>Vegetação:</strong> NDVI do Sentinel-2 (10 m), mediana de ${m.n_cenas_sentinel2} cenas da estação seca; cobertura vegetal = fração de pixels com NDVI ≥ 0,5. Não separa árvores de gramado.`,
      `<strong>Moradores:</strong> Censo 2022 por setor censitário, distribuído entre os hexágonos pela área e pela habitabilidade (menos peso para água e mata). Idosos (60+) e crianças (0 a 4 anos) contam 50% a mais.`,
      `<strong>Índice:</strong> cada fator vira um percentil entre os hexágonos elegíveis; o índice é a média ponderada (padrão 40% calor, 30% falta de verde, 30% moradores). Faixas: muito alta = 10% mais prioritários; alta = 75 a 90; média = 50 a 75.`,
      `<strong>Robustez:</strong> “robusto” = entre os 10% mais prioritários com cinco conjuntos de pesos diferentes (padrão, iguais e ênfase em cada fator).`,
      `<strong>Fora do índice:</strong> água (mais de 50% do hexágono), área aeroportuária, vegetação já densa (cobertura de 60% ou mais: proteger, não plantar) e falta de dado de satélite.`,
      `<strong>Oportunidade:</strong> do OpenStreetMap, que é incompleto e não informa a largura das calçadas. Não entra no índice.`,
      `<strong>Limites:</strong> a distribuição da população dentro de cada setor é estimada; a malha de bairros é a do Censo 2022 (ainda sem a Colônia Japonesa, criada em 2025).`,
    ].map((t) => `<li>${t}</li>`).join("");
  }

  function atualizar() {
    calcular(); numeros(); legenda(); tabela(); graficoOportunidades();
    if (layer) layer.redraw();
    if (sel != null) ficha(sel);
    if (relAberto != null) relatorio(relAberto, false);
  }

  // ---------- controles ----------
  function controles() {
    document.querySelectorAll("[data-camada]").forEach((b) => b.addEventListener("click", () => {
      camada = b.dataset.camada;
      document.querySelectorAll("[data-camada]").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      legenda(); layer.redraw();
    }));
    $("soRobustos").addEventListener("change", (e) => { soRobustos = e.target.checked; layer.redraw(); });
    const s = $("selBairro");
    B.forEach((n, k) => { const o = document.createElement("option"); o.value = k; o.textContent = n; s.appendChild(o); });
    s.addEventListener("change", () => { if (s.value !== "") irPara(+s.value); });
    const ws = [["wCalor", "oCalor", "calor"], ["wVerde", "oVerde", "verde"], ["wPessoas", "oPessoas", "pessoas"]];
    let espera;
    const ler = () => {
      const v = ws.map(([w]) => +$(w).value), soma = v.reduce((a, b) => a + b, 0) || 1;
      ws.forEach(([, o, k], j) => { pesos[k] = v[j] / soma; $(o).textContent = Math.round((100 * v[j]) / soma) + "%"; });
      clearTimeout(espera); espera = setTimeout(atualizar, 120);
    };
    ws.forEach(([w]) => $(w).addEventListener("input", ler));
    $("resetPesos").addEventListener("click", () => { $("wCalor").value = 40; $("wVerde").value = 30; $("wPessoas").value = 30; ler(); });
    document.querySelectorAll("#bairros th").forEach((th) => th.addEventListener("click", () => {
      const k = th.dataset.k; ordem = { k, asc: ordem.k === k ? !ordem.asc : k === "nome" }; tabela();
    }));
  }

  async function init() {
    D = await (await fetch("data/web.json")).json();
    N = D.h3.length; B = D.bairros;
    pesos = { ...D.pesos };
    BORDA = new Float64Array(N * 12); cent = new Float64Array(N * 2); idxCell = new Map(); bairroHex = B.map(() => []);
    for (let i = 0; i < N; i++) {
      const bd = h3.cellToBoundary(D.h3[i]);
      for (let k = 0; k < 6; k++) { BORDA[12 * i + 2 * k] = bd[k % bd.length][0]; BORDA[12 * i + 2 * k + 1] = bd[k % bd.length][1]; }
      const c = h3.cellToLatLng(D.h3[i]); cent[2 * i] = c[0]; cent[2 * i + 1] = c[1];
      idxCell.set(D.h3[i], i); bairroHex[D.bairro[i]].push(i);
    }
    $("mapa").innerHTML = "";
    map = L.map("mapa", { preferCanvas: true, zoomSnap: 0.5 }).setView([-3.07, -60.0], 11.5);
    // Mapa de fundo do OpenStreetMap (sem chave de API), em tons de cinza pelo CSS.
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">colaboradores do OpenStreetMap</a>',
    }).addTo(map);
    layer = new HexLayer().addTo(map);
    map.on("click", (e) => {
      const i = idxCell.get(h3.latLngToCell(e.latlng.lat, e.latlng.lng, 10));
      if (i == null) return;
      sel = i; ficha(i); layer.redraw();
    });
    controles();
    ["wCalor", "wVerde", "wPessoas"].forEach((w, j) => { $(w).value = Math.round(100 * [pesos.calor, pesos.verde, pesos.pessoas][j]); });
    atualizar();
    graficoRelacao(); metodologia();
    // começa no hexágono mais prioritário da cidade
    let topo = 0; for (let i = 0; i < N; i++) if (rankCidade[i] === 1) topo = i;
    sel = topo; ficha(topo); layer.redraw();
  }
  init().catch((e) => { console.error(e.stack); $("mapa").innerHTML = `<div class="carregando">Não foi possível carregar os dados (${e.message}). Rode a etapa 7 do pipeline e abra o site por um servidor (python -m http.server).</div>`; });
})();
