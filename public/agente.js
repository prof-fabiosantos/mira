/* Assistente do Mirá: envia a pergunta e o contexto do mapa para /api/agente e mostra a resposta,
   os pontos citados e a trilha de decisão. A conversa fica só na memória da página. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const SUGESTOES = [
    "Por que este ponto é prioridade?",
    "Onde vale plantar árvores no Japiim?",
    "Compare Cidade Nova e Coroado",
    "Quais bairros mais precisam de árvores?",
    "Redija um ofício para a prefeitura pedindo árvores no Novo Aleixo",
  ];
  const pct = (v) => (typeof v === "number" ? Math.round(v * 100) + "%" : "—");
  const INTENCOES = {
    explicar_local: "explicar um ponto", onde_plantar: "onde plantar num bairro", comparar_bairros: "comparar bairros",
    panorama_cidade: "panorama da cidade", como_funciona: "como o índice funciona", redigir_documento: "redigir um documento",
    fora_do_tema: "fora do tema",
  };

  function el(tag, cls, texto) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (texto != null) e.textContent = texto;
    return e;
  }

  function addMsg(texto, quem, meta) {
    const d = el("div", "msg " + quem, texto);
    if (meta) d.appendChild(el("span", "meta", meta));
    $("msgs").appendChild(d);
    $("msgs").scrollTop = $("msgs").scrollHeight;
    return d;
  }

  function item(cls, titulo, linhas) {
    const li = el("li", cls);
    li.appendChild(el("b", null, titulo));
    linhas.filter(Boolean).forEach((l) => li.appendChild(el("div", null, l)));
    return li;
  }

  function renderTrilha(trilha) {
    const ul = $("trail"); ul.innerHTML = "";
    trilha.forEach((t) => {
      if (t.etapa === "Jev interpreta a pergunta") {
        ul.appendChild(item("ok", t.etapa + (t.simulado ? " (simulado)" : ""), [
          `Intenção: ${INTENCOES[t.intencao] || t.intencao} (confiança ${pct(t.confianca_intencao)})`,
          t.intencao === "redigir_documento" ? `Destinatário: ${t.destinatario} (confiança ${pct(t.confianca_destinatario)})` : null,
        ]));
      } else if (t.etapa === "Código escolhe os dados") {
        ul.appendChild(item("ok", t.etapa, [
          t.bairros_citados.length ? `Bairros citados: ${t.bairros_citados.join(", ")}` : "Nenhum bairro citado na pergunta",
          t.bairro_do_mapa ? `Bairro em foco no mapa: ${t.bairro_do_mapa}` : null,
          `Pesos: ${t.pesos}`,
        ]));
      } else if (t.etapa === "Código monta os fatos") {
        ul.appendChild(item("ok", t.etapa, [`${t.n_fatos} dados liberados para a ${t.tipo === "documento" ? "redação do documento" : "resposta"}`]));
      } else if (t.etapa === "Servidor confere o texto") {
        ul.appendChild(item("fix", t.etapa, [`Texto do Gemini descartado: números sem fonte (${t.numeros_sem_fonte.join(", ")}). Usada a versão de modelo.`]));
      } else if (t.etapa === "Gemini redige; servidor confere") {
        ul.appendChild(item("ok", t.etapa, ["Todos os números do texto conferem com os dados."]));
      } else if (t.etapa === "Gemini") {
        ul.appendChild(item("fix", "Gemini indisponível", [t.erro, "Usada a versão de modelo."]));
      } else if (t.etapa === "Código") {
        ul.appendChild(item("stop", "Regra do servidor", [t.decisao]));
      }
    });
  }

  function imprimir(texto) {
    $("docImpressao").textContent = texto;
    document.body.classList.add("imprimir-doc");
    window.print();
  }
  window.addEventListener("afterprint", () => document.body.classList.remove("imprimir-doc"));

  async function perguntar(texto, extra) {
    const ctx = Object.assign(window.miraApp ? window.miraApp.contexto() : {}, extra || {});
    addMsg(texto, "eu");
    const pend = addMsg("Consultando os dados…", "agente");
    $("askBtn").disabled = true;
    try {
      const r = await fetch("api/agente", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pergunta: texto, ...ctx }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `Erro ${r.status}`);
      pend.remove();
      const fonte = { gemini: "redigido pelo Gemini e conferido", template: "texto de modelo", fixo: "resposta fixa" }[j.fonte_texto];
      const m = addMsg(j.resposta, j.tipo === "documento" ? "agente doc" : "agente", fonte);
      const acoes = el("div", "acoes");
      if (j.tipo === "documento") {
        const c = el("button", "btn", "Copiar");
        c.type = "button";
        c.addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(j.resposta); c.textContent = "Copiado"; } catch (e) { c.textContent = "Selecione e copie"; }
        });
        const p = el("button", "btn", "Imprimir ou salvar PDF");
        p.type = "button";
        p.addEventListener("click", () => imprimir(j.resposta));
        acoes.append(c, p);
      }
      (j.locais || []).forEach((l) => {
        const b = el("button", "btn", l.rotulo);
        b.type = "button";
        b.addEventListener("click", () => window.miraApp && window.miraApp.selecionar(l.hexagono));
        acoes.appendChild(b);
      });
      if (acoes.childNodes.length) m.insertBefore(acoes, m.querySelector(".meta"));
      renderTrilha(j.trilha);
    } catch (e) {
      pend.textContent = "Não consegui responder agora. " + e.message;
    } finally {
      $("askBtn").disabled = false;
    }
  }

  async function init() {
    SUGESTOES.forEach((s) => {
      const b = el("button", "chip", s);
      b.type = "button";
      b.addEventListener("click", () => perguntar(s));
      $("chips").appendChild(b);
    });
    $("askForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const t = $("q").value.trim();
      if (t.length < 2) return;
      $("q").value = "";
      perguntar(t);
    });
    window.miraAgente = {
      perguntar: (t, extra) => { $("assistente").scrollIntoView({ behavior: "smooth" }); perguntar(t, extra); },
    };
    try {
      const r = await fetch("api/status");
      if (!r.ok) throw 0;
      const s = await r.json();
      $("agStatus").textContent = `Interpretação: ${s.jev === "TypeSafe" ? "Jev (TypeSafe)" : "Jev simulado"} · Redação: ${s.redacao === "Gemini" ? "Gemini" : "texto de modelo"}. A conversa não é guardada.`;
    } catch (e) {
      $("agStatus").textContent = "Assistente indisponível neste endereço. Localmente, abra o site pelo servidor (uvicorn api.index:app), não pelo http.server.";
      $("askBtn").disabled = true;
    }
  }
  init();
})();
