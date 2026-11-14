"""Acesso ao banco: uma transação "tudo ou nada" reutilizada por stage e produção."""

from contextlib import contextmanager

from backend.db import connect


@contextmanager
def transacao(db_name):
    """Entrega um cursor; faz commit ao sair, rollback em qualquer erro.

    Captura BaseException (não só Exception) de propósito: promover() aborta
    com SystemExit quando o rascunho está vazio, e isso também precisa
    desfazer o que já foi feito.
    """
    conn = connect(db_name, autocommit=False)
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
