"""Testes do assistente sem rede: Jev simulado e Gemini falso. Usa o public/data/web.json real.

    python testes/teste_agente.py
"""
import os
import sys
from pathlib import Path
from unittest import mock

os.environ["JEV_MOCK"] = "1"
os.environ["GEMINI_MOCK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from _agente import dados, decisao, gemini  # noqa: E402

d = dados.D()
topo = d["h3"][dados.calcular(dados.PESOS_PADRAO)["ordem"][0]]
CASOS = [
    ("Por que este ponto é prioridade?", {"hexagono": topo}, "explicar_local", "texto"),
    ("Onde vale plantar árvores no Japiim?", {}, "onde_plantar", "texto"),
    ("Compare Cidade Nova e Coroado", {}, "comparar_bairros", "texto"),
    ("Compare com o Coroado", {"bairro": "Japiim"}, "comparar_bairros", "texto"),
    ("Quais bairros mais precisam de árvores?", {}, "panorama_cidade", "texto"),
    ("Como funcionam os pesos?", {}, "como_funciona", "texto"),
    ("Redija um ofício para o vereador pedindo árvores no Novo Aleixo", {}, "redigir_documento", "documento"),
    ("Qual a cotação do dólar?", {}, "fora_do_tema", "texto"),
]
falhas = 0


def checar(cond, msg):
    global falhas
    print(("ok   " if cond else "FALHA ") + msg)
    falhas += not cond


assert dados.bairros_no_texto("plantar no Tarumã-Açu e no Japiim?") == [d["bairros"].index("Tarumã-Açu"), d["bairros"].index("Japiim")]
for q, ctx, intencao, tipo in CASOS:
    capt = {}

    def falso(instr, msg, max_tokens=500):
        capt["fatos"] = msg
        raise RuntimeError("sem rede no teste")
    with mock.patch.object(gemini, "ativo", lambda: True), mock.patch.object(gemini, "redigir", falso):
        r = decisao.responder(q, ctx)
    checar(r["trilha"][0]["intencao"] == intencao and r["tipo"] == tipo, f"{q} → {intencao}/{tipo}")
    if "fatos" in capt:  # o texto de modelo tem de passar na própria conferência de números
        fatos = eval(capt["fatos"].split("FATOS: ", 1)[1])  # noqa: S307 (texto gerado pelo próprio teste)
        checar(not decisao.numeros_nao_verificados(r["resposta"], fatos), "  texto de modelo passa na conferência")

# pesos diferentes mudam a resposta
a = decisao.responder("Onde plantar no Japiim?", {"pesos": {"calor": 0.4, "verde": 0.3, "pessoas": 0.3}})["resposta"]
b = decisao.responder("Onde plantar no Japiim?", {"pesos": {"calor": 0.1, "verde": 0.1, "pessoas": 0.8}})["resposta"]
checar(a != b, "pesos do site mudam a resposta")

# número inventado pelo LLM é barrado; números corretos passam
with mock.patch.object(gemini, "ativo", lambda: True), \
        mock.patch.object(gemini, "redigir", lambda i, m, max_tokens=500: "Plante 500 ipês no Japiim até 2027."):
    r = decisao.responder("Onde plantar no Japiim?", {})
checar(r["fonte_texto"] == "template" and r["trilha"][-1]["numeros_sem_fonte"] == ["2027", "500"], "número inventado é barrado")
resumo = dados.resumo_bairro(d["bairros"].index("Japiim"), dados.calcular(dados.PESOS_PADRAO))
txt_ok = f"No Japiim, {resumo['hexagonos_prioridade_muito_alta']} hexágonos estão em prioridade muito alta."
with mock.patch.object(gemini, "ativo", lambda: True), mock.patch.object(gemini, "redigir", lambda i, m, max_tokens=500: txt_ok):
    r = decisao.responder("Onde plantar no Japiim?", {})
checar(r["fonte_texto"] == "gemini", "texto com números certos é aprovado")

print("\nTodos os testes do assistente passaram." if not falhas else f"\n{falhas} falha(s).")
sys.exit(1 if falhas else 0)
