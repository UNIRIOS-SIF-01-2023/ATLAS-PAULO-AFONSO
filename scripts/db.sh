#!/usr/bin/env bash
# Inicia/para o MariaDB local do Atlas Urbano (Porta 3320).
# Cross-platform: Linux usa /usr/bin/mariadb*; Windows (Git Bash/MSYS)
# usa "C:/Program Files/MariaDB 13.0/...".
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Sem ponto no nome: no Windows uma pasta de dados ".algo" não reinicia.
DATADIR="$ROOT/mariadb-data"
CONF="$ROOT/scripts/mysql-local.cnf"
SCHEMA="$ROOT/scripts/schema.sql"
mkdir -p /tmp/mariadb-tmp

# Senhas vêm do .env da raiz (modelo em .env.example), nunca do código.
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi
DB_PORT="3320"
ROOT_PASSWORD="${MARIADB_ROOT_PASSWORD:-}"
if [ -z "$ROOT_PASSWORD" ] || [ -z "${DB_PASSWORD:-}" ]; then
  echo "ERRO: defina MARIADB_ROOT_PASSWORD e DB_PASSWORD no .env (veja .env.example)" >&2
  exit 1
fi

case "$(uname -s 2>/dev/null || echo Linux)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    IS_WINDOWS=1
    ;;
  *)
    IS_WINDOWS=0
    ;;
esac

if [ "$IS_WINDOWS" = "1" ]; then
  EFF_DATADIR="$(cygpath -w "$DATADIR" 2>/dev/null || echo "$DATADIR" | sed 's|^/c/|C:/|;s|/|\\|g')"
  EFF_CONF="$(cygpath -w "$CONF" 2>/dev/null || echo "$CONF" | sed 's|^/c/|C:/|;s|/|\\|g')"
  # O /tmp do Git Bash não existe para o mariadbd.exe; passa o caminho Windows.
  EFF_TMPDIR="$(cygpath -w /tmp/mariadb-tmp)"
  MARIADBD="C:/Program Files/MariaDB 13.0/bin/mariadbd.exe"
  MARIADB_CLIENT="C:/Program Files/MariaDB 13.0/bin/mariadb.exe"
  MARIADB_ADMIN="C:/Program Files/MariaDB 13.0/bin/mariadb-admin.exe"
  MARIADB_INSTALL_DB="C:/Program Files/MariaDB 13.0/bin/mariadb-install-db.exe"
  SOCKET_FILE="$EFF_DATADIR/mysql.sock"
  PID_FILE="$EFF_DATADIR/mysql.pid"
  ERR_LOG="$EFF_DATADIR/mysql.err"
else
  EFF_DATADIR="$DATADIR"
  EFF_CONF="$CONF"
  EFF_TMPDIR="/tmp/mariadb-tmp"
  MARIADBD="/usr/bin/mariadbd"
  MARIADB_CLIENT="/usr/bin/mariadb"
  MARIADB_ADMIN="/usr/bin/mariadb-admin"
  MARIADB_INSTALL_DB="/usr/bin/mariadb-install-db"
  SOCKET_FILE="$DATADIR/mysql.sock"
  PID_FILE="$DATADIR/mysql.pid"
  ERR_LOG="$DATADIR/mysql.err"
fi

if [ -n "$ROOT_PASSWORD" ]; then
  # shellcheck disable=SC2086
  CLIENT_AUTH="-h 127.0.0.1 -P $DB_PORT -u root -p$ROOT_PASSWORD"
  ADMIN_AUTH="-h 127.0.0.1 -P $DB_PORT -u root -p$ROOT_PASSWORD"
else
  CLIENT_AUTH="-h 127.0.0.1 -P $DB_PORT -u root"
  ADMIN_AUTH="-h 127.0.0.1 -P $DB_PORT -u root"
fi

wait_ready() {
  # Aguarda o servidor responder (até ~30s). Tenta com senha e sem
  # senha: um datadir recém-criado pode ainda estar sem senha de root.
  for _ in $(seq 1 60); do
    # shellcheck disable=SC2086
    if "$MARIADB_ADMIN" $ADMIN_AUTH ping >/dev/null 2>&1; then
      return 0
    fi
    if "$MARIADB_ADMIN" -h 127.0.0.1 -P "$DB_PORT" -u root ping >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

case "${1:-start}" in
  start)
    if [ ! -d "$DATADIR/mysql" ]; then
      echo "Inicializando data dir do MariaDB..."
      if [ "$IS_WINDOWS" = "1" ]; then
        "$MARIADB_INSTALL_DB" --datadir="$EFF_DATADIR" --password="$ROOT_PASSWORD" >/dev/null 2>&1 || \
        "$MARIADBD" --initialize-insecure --datadir="$EFF_DATADIR"
      else
        "$MARIADB_INSTALL_DB" --datadir="$EFF_DATADIR" \
          --auth-root-authentication-method=normal >/dev/null 2>&1
      fi
    fi

    # Já rodando? Reaproveita em vez de subir outra instância.
    # shellcheck disable=SC2086
    if "$MARIADB_ADMIN" $ADMIN_AUTH ping >/dev/null 2>&1; then
      echo "MariaDB já rodando na porta $DB_PORT."
    else
      # Remove restos stale de execução anterior (sock/pid sem servidor).
      rm -f "$SOCKET_FILE" "$PID_FILE" 2>/dev/null || true

      echo "Iniciando MariaDB na porta $DB_PORT..."
      # shellcheck disable=SC2086
      nohup "$MARIADBD" --defaults-file="$EFF_CONF" --datadir="$EFF_DATADIR" \
        --port="$DB_PORT" --pid-file="$PID_FILE" \
        --socket="$SOCKET_FILE" \
        --log-error="$ERR_LOG" --tmpdir="$EFF_TMPDIR" --bind-address=127.0.0.1 >/dev/null 2>&1 &

      if ! wait_ready; then
        echo "ERRO: MariaDB não respondeu na porta $DB_PORT. Veja $ERR_LOG" >&2
        exit 1
      fi
    fi

    echo "Configurando banco de dados e permissões..."
    # A senha do usuário atlas vem do .env; aspas simples dobradas para o SQL.
    SENHA_ATLAS="$(printf '%s' "$DB_PASSWORD" | sed "s/'/''/g")"
    # shellcheck disable=SC2086
    "$MARIADB_CLIENT" $CLIENT_AUTH <<SQL
CREATE DATABASE IF NOT EXISTS ATLAS       CHARACTER SET UTF8MB4 COLLATE UTF8MB4_0900_AI_CI;
CREATE DATABASE IF NOT EXISTS ATLAS_STAGE CHARACTER SET UTF8MB4 COLLATE UTF8MB4_0900_AI_CI;
CREATE USER IF NOT EXISTS 'atlas'@'127.0.0.1' IDENTIFIED BY '$SENHA_ATLAS';
CREATE USER IF NOT EXISTS 'atlas'@'localhost' IDENTIFIED BY '$SENHA_ATLAS';
GRANT ALL PRIVILEGES ON atlas_urbano.* TO 'atlas'@'127.0.0.1';
GRANT ALL PRIVILEGES ON atlas_urbano.* TO 'atlas'@'localhost';
GRANT ALL PRIVILEGES ON ATLAS.*        TO 'atlas'@'127.0.0.1';
GRANT ALL PRIVILEGES ON ATLAS.*        TO 'atlas'@'localhost';
GRANT ALL PRIVILEGES ON ATLAS_STAGE.*  TO 'atlas'@'127.0.0.1';
GRANT ALL PRIVILEGES ON ATLAS_STAGE.*  TO 'atlas'@'localhost';
FLUSH PRIVILEGES;
SQL
    for banco in ATLAS ATLAS_STAGE; do
      # shellcheck disable=SC2086
      "$MARIADB_CLIENT" $CLIENT_AUTH "$banco" < "$SCHEMA"
    done
    echo "MariaDB pronto na porta $DB_PORT."
    ;;
  stop)
    echo "Parando MariaDB..."
    # shellcheck disable=SC2086
    "$MARIADB_ADMIN" $ADMIN_AUTH shutdown 2>/dev/null || true
    echo "MariaDB parado."
    ;;
  *)
    echo "Uso: $0 [start|stop]"
    exit 1
    ;;
esac
