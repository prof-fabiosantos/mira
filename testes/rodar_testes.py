"""Roda todos os testes sintéticos numa pasta temporária (não toca em dados/ nem em public/)."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
RAIZ = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="plantio_teste_"))
(tmp / "dados" / "entrada").mkdir(parents=True)
shutil.copy(RAIZ / "dados" / "entrada" / "bairros.geojson", tmp / "dados" / "entrada")
env = {**os.environ, "PLANTIO_RAIZ": str(tmp)}
passos = [["pipeline/01_grade.py"], ["testes/sintetico_satelite.py"], ["testes/sintetico_populacao.py"],
          ["testes/sintetico_osm.py"], ["testes/sintetico_copas.py"], ["pipeline/07_indice.py"],
          ["pipeline/08_web.py"]]
for p in passos:
    print(f"\n>>> {p[0]}", flush=True)
    r = subprocess.run([sys.executable, str(RAIZ / p[0])], env=env, cwd=tmp)
    if r.returncode:
        sys.exit(f"Falhou: {p[0]}")
print(f"\nTodos os testes passaram (arquivos em {tmp}).")
