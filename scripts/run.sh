#!/usr/bin/env bash
# Sobe tudo (banco + importação + backend + frontend) e mantém os processos.
# Configuração no .env da raiz (copie o .env.example).
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -f .env ] || { echo "ERRO: crie o .env a partir do .env.example"; exit 1; }
if [ -x ./.venv/Scripts/python ]; then PY=./.venv/Scripts/python; else PY=./.venv/bin/python; fi

echo "==> 1/4 Banco local (MariaDB na porta 3320)"
bash scripts/db.sh start

echo "==> 2/4 Importação dos dados reais"
"$PY" importacao/import_data.py --promover

echo "==> 3/5 Importação dos dados reais para o MySQL"
doppler run -- "$PY" import_data.py --promover

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
