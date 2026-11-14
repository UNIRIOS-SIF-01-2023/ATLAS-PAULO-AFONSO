"""Cadastro e login com JWT (padrão Bearer).

Responsabilidades deste módulo:
- transformar senha em hash bcrypt (a senha nunca é gravada nem devolvida);
- persistência e consulta de usuários na tabela USUARIOS (create_user, authenticate);
- emitir e validar tokens JWT (create_token, get_current_user).

A tabela USUARIOS é criada à mão em cada banco (não entra na promoção do stage).
Tudo passa por consultas parametrizadas, no padrão de db.py.
Fora do escopo: recuperação de senha e bloqueio por tentativas.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pymysql
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db
from .config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET

_CAMPOS = "ID, NOME, EMAIL, CRIADO_EM"


class EmailEmUso(ValueError):
    """Cadastro com e-mail que já existe na tabela."""


def hash_senha(senha: str) -> str:
    """Gera o hash bcrypt da senha (salt aleatório embutido)."""
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def confere_senha(senha: str, senha_hash: str) -> bool:
    """Confere a senha em tempo constante contra o hash guardado."""
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("ascii"))
    except ValueError:
        return False


def _publico(row):
    """Linha do banco → dados públicos (sem nenhum vestígio da senha)."""
    return {
        "id": row["ID"],
        "nome": row["NOME"],
        "email": row["EMAIL"],
        "criado_em": row["CRIADO_EM"],
    }


def create_user(nome: str, email: str, senha: str):
    """Cadastra e devolve os dados públicos. Levanta EmailEmUso se duplicar."""
    email = email.strip().lower()
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO USUARIOS (NOME, EMAIL, SENHA) VALUES (%s, %s, %s)",
                (nome.strip(), email, hash_senha(senha)),
            )
            cur.execute(
                f"SELECT {_CAMPOS} FROM USUARIOS WHERE ID = %s", (cur.lastrowid,)
            )
            return _publico(cur.fetchone())
    except pymysql.err.IntegrityError as e:
        if e.args and e.args[0] == 1062:  # Duplicate entry (EMAIL UNIQUE)
            raise EmailEmUso(email) from e
        raise


def authenticate(email: str, senha: str):
    """Dados públicos se e-mail + senha conferem e o usuário está ativo, senão None."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_CAMPOS}, SENHA FROM USUARIOS WHERE EMAIL = %s AND ATIVO",
            (email.strip().lower(),),
        )
        row = cur.fetchone()
    if row is None or not confere_senha(senha, row["SENHA"]):
        return None
    return _publico(row)


def find_user_by_id(user_id: int):
    """Dados públicos do usuário ativo, ou None se não existir mais / inativo."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_CAMPOS} FROM USUARIOS WHERE ID = %s AND ATIVO", (user_id,)
        )
        row = cur.fetchone()
    return _publico(row) if row is not None else None


def create_token(user_id: int) -> str:
    """Emite um JWT HS256 com o id do usuário e expiração configurável."""
    expira = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": str(user_id), "exp": expira},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


bearer = HTTPBearer(auto_error=True)


def get_current_user(
    credenciais: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Dependência das rotas protegidas: validação stateless, só assinatura
    + expiração, sem ida ao banco (é o que deixa os tiles rápidos mesmo com
    o banco remoto). Token ausente, expirado ou adulterado → 401.

    A existência do usuário é checada só no /api/auth/eu (abertura do app).
    """
    try:
        dados = jwt.decode(
            credenciais.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        return {"id": int(dados.get("sub", ""))}
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="sessão inválida ou expirada")
