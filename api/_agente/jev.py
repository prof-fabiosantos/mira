"""Cliente do Jev (TypeSafe AI) e um simulador por palavras-chave para testes sem chave.

Mesmo cliente do Akub; só as regras do simulador são do Mirá.
"""
import os
import re
import unicodedata

import requests

URL = os.getenv("TYPESAFE_URL", "https://api.typesafe.ai/v1/systemone")
MODELO = os.getenv("JEV_MODEL", "jev-latest")


def _mock_ativo() -> bool:
    return os.getenv("JEV_MOCK", "0") == "1" or not os.getenv("TYPESAFE_API_KEY")


def perguntar(state, questions: dict) -> dict:
    """Envia uma chamada ao Jev e devolve {'answers': ..., 'model': ..., 'simulado': bool}."""
    if _mock_ativo():
        return {"answers": _simular(state, questions), "model": "simulador-local", "simulado": True}
    r = requests.post(
        URL,
        headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}", "Content-Type": "application/json"},
        json={"model": MODELO, "state": state, "questions": questions},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Jev respondeu {r.status_code}: {r.text[:300]}")
    j = r.json()
    return {"answers": j["answers"], "model": j.get("model", MODELO), "simulado": False}


# ---------------- simulador (só para desenvolvimento e aula) ----------------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s).lower())
    return "".join(c for c in s if not unicodedata.combining(c))


REGRAS = {
    "intencao": [
        ("redigir_documento", r"oficio|carta|redij|redigir|escrev|documento|requeriment|solicitac|pedido formal|justificativa|abaixo.assinado|e-?mail para"),
        ("comparar_bairros", r"compar|\bversus\b|\bvs\b|mais (prioritari|quente|verde) que|diferenca entre"),
        ("como_funciona", r"como (funciona|e calculad|calcula)|o que (e|significa)|peso|percentil|robust|metodolog|de onde vem|fonte"),
        ("panorama_cidade", r"quais bairros|que bairros|cidade toda|manaus inteira|mais prioritari(o|os) da cidade|ranking|onde (mais )?precisa"),
        ("explicar_local", r"\baqui\b|este (local|lugar|ponto|hexagono)|esse (local|lugar|ponto|hexagono)|minha rua|por que (e|esta|este|esse)|selecionad"),
        ("onde_plantar", r"onde plantar|onde (devo|posso|vale)|melhores? (lugar|ponto|local)|plantar|arvore|mutirao"),
    ],
    "destinatario": [
        ("prefeitura", r"prefeit|semmas|secretari|orgao ambiental|poder publico|gestor"),
        ("legislativo", r"vereador|camara|deputad|assembleia|parlament"),
        ("escola", r"escola|diretor|creche|professor"),
        ("comunidade", r"associac|morador|vizinh|comunidade|lideranc"),
    ],
}


def _simular(state, questions):
    texto = _norm(state if isinstance(state, str) else " ".join(map(str, state.values() if isinstance(state, dict) else state)))
    out = {}
    for nome, q in questions.items():
        if q["type"] == "choice":
            opcoes = list(q["criteria"].keys())
            escolha = None
            for op, padrao in REGRAS.get(nome, []):
                if op in opcoes and re.search(padrao, texto):
                    escolha = op
                    break
            escolha = escolha or ("nao_especificado" if "nao_especificado" in opcoes else
                                  "fora_do_tema" if "fora_do_tema" in opcoes else opcoes[0])
            out[nome] = {"type": "choice", "choice": escolha, "confidence": 0.9,
                         "probabilities": {o: (0.9 if o == escolha else 0.1 / max(1, len(opcoes) - 1)) for o in opcoes}}
        elif q["type"] == "noul":
            out[nome] = {"type": "noul", "noul": 0.1}
        elif q["type"] == "score":
            out[nome] = {"type": "score", "score": 0.0, "confidence": 0.5}
    return out
