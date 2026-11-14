#!/usr/bin/env bash
# Sobe tudo (MySQL + importação + backend + frontend) e mantém os processos.
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# venv do Windows (Scripts/) ou do Linux/macOS (bin/).
PY="./.venv/Scripts/python"
[ -x "$PY" ] || [ -x "$PY.exe" ] || PY="./.venv/bin/python"

echo "==> 1/5 Banco de dados"
bash scripts/db.sh start

echo "==> 2/5 Camada rooftop (cnefe_rooftop.py -> algoritmo_raycast)"
QG="data/raw/qg/qg_810_endereco_Munic2924009.json"
if [ "${1:-}" = "--rooftop" ] || [ ! -f data/processed/pontos_rooftop.geojson ]; then
  if [ -f "$QG" ]; then
    "$PY" cnefe_rooftop.py "$QG" mysql data/processed/pontos_rooftop.gpkg \
      --debug \
      --web-geojson data/processed/pontos_rooftop.geojson \
      --web-poligonos data/processed/pontos_rooftop_poligonos.geojson
  else
    echo "  (QG do CNEFE ausente em $QG, pulando geração da camada rooftop)"
  fi
else
  echo "  data/processed/pontos_rooftop.geojson já existe (use --rooftop para regenerar)"
fi

echo "==> 3/5 Importação dos dados reais para o MySQL"
if [ "${1:-}" = "--reimport" ]; then
  "$PY" import_data.py
else
  "$PY" import_data.py --if-empty
fi

echo "==> 4/5 Backend (FastAPI em :8000)"
if curl -sf http://127.0.0.1:8000/ >/dev/null 2>&1; then
  echo "  backend já rodando"
else
  nohup "$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 \
    > /tmp/atlas-uvicorn.log 2>&1 &
  sleep 3
  echo "  backend OK"
fi

echo "==> 5/5 Frontend (Vite em :5173)"
cd frontend
if [ ! -x node_modules/.bin/vite ]; then
  if ! npm install --no-audit --no-fund >/dev/null 2>&1; then
    echo "  (npm install falhou, seguindo sem ele)"
  fi
fi
if curl -sf http://127.0.0.1:5173/ >/dev/null 2>&1; then
  echo "  frontend já rodando em http://127.0.0.1:5173/"
else
  nohup npm run dev -- --port 5173 --host 127.0.0.1 > /tmp/atlas-vite.log 2>&1 &
  sleep 4
  echo "  frontend OK"
fi

echo
echo "Pronto! Abra http://127.0.0.1:5173/ no navegador."
