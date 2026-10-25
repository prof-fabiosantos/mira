"""Cliente mínimo da API do Gemini (generateContent) — só redige o texto final.

Quando o modelo está sobrecarregado (429/500/503) ou não responde a tempo,
passa para o modelo reserva (GEMINI_MODEL_RESERVA). Respostas 503 ganham uma
nova tentativa no mesmo modelo; timeouts vão direto para o reserva.
"""
import os
import time

import requests

API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
TEMPORARIOS = {429, 500, 503}
TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "15"))


def ativo() -> bool:
    return bool(os.getenv("GEMINI_API_KEY")) and os.getenv("GEMINI_MOCK", "0") != "1"


def _corpo_para(modelo, corpo):
    """Na família 2.5 o raciocínio vem ligado e consome o limite de saída; aqui não é preciso."""
    if modelo.startswith("gemini-2.5-flash"):
        corpo = {**corpo, "generationConfig": {**corpo["generationConfig"], "thinkingConfig": {"thinkingBudget": 0}}}
    return corpo


def _chamar(modelo, corpo, chave):
    url = API.format(modelo=modelo)
    corpo = _corpo_para(modelo, corpo)
    r = requests.post(url, headers={"x-goog-api-key": chave}, json=corpo, timeout=TIMEOUT)
    if r.status_code in (401, 403):
        # Algumas credenciais do Google são tokens de acesso, aceitos como Bearer.
        r = requests.post(url, headers={"Authorization": f"Bearer {chave}"}, json=corpo, timeout=TIMEOUT)
    return r


def redigir(instrucoes: str, mensagem: str, max_tokens: int = 500) -> str:
    chave = os.environ["GEMINI_API_KEY"]
    modelos = [os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
               os.getenv("GEMINI_MODEL_RESERVA", "gemini-3.6-flash")]
    corpo = {
        "systemInstruction": {"parts": [{"text": instrucoes}]},
        "contents": [{"role": "user", "parts": [{"text": mensagem}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
    }
    erros = []
    for modelo in dict.fromkeys(m for m in modelos if m):
        for tentativa in range(2):
            try:
                r = _chamar(modelo, corpo, chave)
            except (requests.Timeout, requests.ConnectionError) as e:
                erros.append(f"{modelo}: sem resposta em {TIMEOUT:.0f} s" if isinstance(e, requests.Timeout)
                             else f"{modelo}: falha de conexão")
                break  # não insiste no mesmo modelo; vai para o reserva
            if r.status_code < 400:
                partes = r.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in partes).strip()
            erros.append(f"{modelo}: {r.status_code}")
            if r.status_code not in TEMPORARIOS:
                raise RuntimeError(f"Gemini respondeu {r.status_code}: {r.text[:200]}")
            if tentativa == 0:
                time.sleep(1.5)
    raise RuntimeError("Gemini indisponível (" + ", ".join(erros) + ")")
