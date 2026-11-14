"""Promoção do rascunho para produção, tudo ou nada."""

from . import config
from .banco import transacao


def colunas_gravaveis(cur, tabela):
    """Colunas não geradas (AREA_M2 e ENDERECOS.TIPO_USO são calculadas)."""
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s "
        "AND TABLE_NAME = %s AND GENERATION_EXPRESSION = '' ORDER BY ORDINAL_POSITION",
        (config.DB_NAME_STAGE, tabela))
    return ", ".join(r["COLUMN_NAME"] for r in cur.fetchall())


def _verificar_rascunho(cur, stage):
    """Aborta (SystemExit) se o rascunho estiver vazio ou com carga incompleta."""
    cur.execute(f"SELECT COUNT(*) AS n FROM {stage}.EDIFICACOES")
    n = cur.fetchone()["n"]
    cur.execute(f"SELECT COUNT(*) AS n FROM {stage}.CARGAS WHERE CONCLUIDA_EM IS NOT NULL")
    if not n or not cur.fetchone()["n"]:
        raise SystemExit("Rascunho vazio ou carga incompleta: produção intacta.")
    return n


def promover():
    """Substitui os dados de produção pelos do rascunho, tudo ou nada."""
    stage = f"`{config.DB_NAME_STAGE}`"
    with transacao(config.DB_NAME) as cur:
        n = _verificar_rascunho(cur, stage)
        print(f"Promovendo {n} prédios do rascunho para produção...")
        for t in reversed(config.TABELAS):
            cur.execute(f"DELETE FROM {t}")
        for t in config.TABELAS:
            cols = colunas_gravaveis(cur, t)
            cur.execute(f"INSERT INTO {t} ({cols}) SELECT {cols} FROM {stage}.{t}")
        # Produção guarda o histórico de cargas; o ID é o dela.
        cols = "INICIADA_EM, CONCLUIDA_EM, PARAMETROS, ARQUIVOS, CONTAGENS"
        cur.execute(f"INSERT INTO CARGAS ({cols}) SELECT {cols} FROM {stage}.CARGAS")
    print("  Produção atualizada.")
