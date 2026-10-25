"""Mirá — servidor do assistente (/api/agente e /api/status).

No Vercel, este arquivo é uma função Python e a pasta public/ é servida pela CDN.
Localmente, também entrega o site:
    pip install -r requirements.txt
    copy .env.example .env          (Windows) — e preencha as chaves
    uvicorn api.index:app --reload --port 8000
    abra http://localhost:8000
"""
import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(AQUI))

try:
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")  # antes de importar o agente, que lê as variáveis
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from _agente import decisao, gemini, jev  # noqa: E402

PUBLIC = RAIZ / "public"
NO_VERCEL = bool(os.getenv("VERCEL"))
app = FastAPI(title="Mirá")

# Limite simples por visitante, para proteger as chaves do Jev e do Gemini.
# Vale por instância do servidor; para um limite global, use também o Firewall do Vercel.
POR_MINUTO = int(os.getenv("LIMITE_POR_MINUTO", "6"))
POR_DIA = int(os.getenv("LIMITE_POR_DIA", "60"))
_historico = defaultdict(deque)


def _ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (req.client.host if req.client else "?")


def _checar_limite(ip: str):
    agora = time.time()
    h = _historico[ip]
    while h and agora - h[0] > 86400:
        h.popleft()
    if sum(1 for t in h if agora - t < 60) >= POR_MINUTO:
        raise HTTPException(429, "Muitas perguntas seguidas. Espere um minuto e tente de novo.")
    if len(h) >= POR_DIA:
        raise HTTPException(429, "Limite de perguntas de hoje atingido. Volte amanhã.")
    h.append(agora)


class Pesos(BaseModel):
    calor: float = 0.4
    verde: float = 0.3
    pessoas: float = 0.3


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=2, max_length=600)
    bairro: str | None = Field(default=None, max_length=80)
    hexagono: str | None = Field(default=None, max_length=20)
    pesos: Pesos | None = None


@app.get("/api/status")
def status():
    return {"jev": "simulado" if jev._mock_ativo() else "TypeSafe",
            "redacao": "Gemini" if gemini.ativo() else "template"}


@app.post("/api/agente")
def agente(p: Pergunta, req: Request):
    _checar_limite(_ip(req))
    try:
        return decisao.responder(p.pergunta.strip(), {
            "bairro": p.bairro, "hexagono": p.hexagono,
            "pesos": p.pesos.model_dump() if p.pesos else None})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # Jev fora do ar
        raise HTTPException(502, f"Não foi possível responder agora: {str(e)[:200]}")


if not NO_VERCEL:
    # Desenvolvimento local: entrega só arquivos de public/. Nunca a raiz, que tem o .env.
    @app.get("/{nome:path}", include_in_schema=False)
    def frontend(nome: str):
        alvo = (PUBLIC / (nome or "index.html")).resolve()
        if PUBLIC.resolve() in alvo.parents and alvo.is_file():
            return FileResponse(alvo)
        raise HTTPException(404)
