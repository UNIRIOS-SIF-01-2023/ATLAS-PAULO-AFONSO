#!/usr/bin/env bash
# Sobe tudo (importação + backend + frontend) e mantém os processos.
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> 1/4 Credenciais do banco (Doppler)"
command -v doppler >/dev/null || { echo "ERRO: instale o Doppler CLI"; exit 1; }

echo "==> 2/4 Importação dos dados reais para o MySQL"
doppler run -- "./.venv/Scripts/python" importacao/import_data.py --promover

echo "==> 3/4 Backend (FastAPI em :8000)"
if curl -sf http://127.0.0.1:8000/ >/dev/null 2>&1; then
  echo "  backend já rodando"
else
  nohup doppler run -- ./.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 \
    > /tmp/atlas-uvicorn.log 2>&1 &
  sleep 3
  echo "  backend OK"
fi

echo "==> 4/4 Frontend (Vite em :5173)"
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
