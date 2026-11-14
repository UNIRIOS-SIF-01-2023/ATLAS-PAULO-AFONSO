"""Carga do banco de rascunho (DB_NAME_STAGE) a partir do zero."""

import json
import os

from . import config
from .banco import transacao
from .registros import construir_registros

GEO = f"ST_GeomFromGeoJSON(%s, 1, {config.SRID})"


def montar_inserts(reg):
    """[(tabela, sql, linhas)] na ordem de carga (pais antes de filhos)."""
    return [
        ("FONTES", "INSERT INTO FONTES (ID, NOME, LICENCA) VALUES (%s, %s, %s)",
         [(i, n, lic) for n, (i, lic) in reg.fontes.items()]),
        ("BAIRROS", "INSERT INTO BAIRROS (ID, NOME) VALUES (%s, %s)",
         [(i, n) for n, i in reg.localidades.items()]),
        ("LOCALIDADES", "INSERT INTO LOCALIDADES (ID, NOME, BAIRRO_ID) VALUES (%s, %s, %s)",
         [(i, n, i) for n, i in reg.localidades.items()]),
        ("EDIFICACOES",
         "INSERT INTO EDIFICACOES (ID, FONTE_ID, BAIRRO_ID, TIPO_USO, ORIGEM_TIPO, "
         "USO_MISTO, N_ENDERECOS, NUM_PAVIMENTOS, CONFIANCA_FONTE, GEOM, PONTO_ROTULO) "
         f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, {GEO}, {GEO})", reg.edificacoes),
        ("EDIFICACAO_ORIGEM",
         "INSERT INTO EDIFICACAO_ORIGEM (EDIFICACAO_ID, ID_ORIGEM, FONTES, ATRIBUTOS) "
         "VALUES (%s, %s, %s, %s)", reg.origens),
        ("ENDERECOS",
         "INSERT INTO ENDERECOS (ID, COD_UNICO, COD_ESPECIE, COD_TIPO_ESPECI, COD_SETOR, "
         "NUM_QUADRA, NUM_FACE, CEP, LOCALIDADE_ID, LOGRADOURO_NUM, COMPLEMENTO, "
         "ESTABELECIMENTO, NV_GEO_COORD, PT) "
         f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {GEO})",
         reg.enderecos),
        ("ENDERECO_EDIFICACAO",
         "INSERT INTO ENDERECO_EDIFICACAO (ENDERECO_ID, EDIFICACAO_ID, METODO, DISTANCIA_M) "
         "VALUES (%s, %s, %s, %s)", reg.vinculos),
    ]


def inserir_em_lotes(cur, sql, rows, tamanho=None):
    """INSERT multi-valores em lotes.

    pymysql só agrupa executemany com %s puros no VALUES; com
    ST_GeomFromGeoJSON(%s) seria 1 ida ao banco por linha.
    """
    tamanho = tamanho or config.TAMANHO_LOTE
    head, tpl = sql.split(" VALUES ", 1)
    for i in range(0, len(rows), tamanho):
        lote = rows[i:i + tamanho]
        cur.execute(f"{head} VALUES " + ",".join([tpl] * len(lote)),
                    [v for r in lote for v in r])


def _limpar_tabelas(cur):
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for t in config.TABELAS + ["CARGAS"]:
        cur.execute(f"TRUNCATE TABLE {t}")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def _abrir_carga(cur):
    """Registra a carga (parâmetros + arquivos usados) e devolve o ID dela."""
    arquivos = [p for p in config.OVERTURE_FILES
                + [config.OSM_BUILDINGS, config.MUNICIPIO_BOUNDARY, config.QG_ENDERECOS]
                if os.path.exists(p)]
    parametros = {"municipio": config.MUNICIPIO, "srid": config.SRID,
                  "point_tolerance_deg": config.POINT_TOLERANCE_DEG}
    cur.execute("INSERT INTO CARGAS (PARAMETROS, ARQUIVOS) VALUES (%s, %s)",
                (json.dumps(parametros), json.dumps(arquivos)))
    return cur.lastrowid


def _agregar_bairro_tipo(cur):
    cur.execute(
        "INSERT INTO AGG_BAIRRO_TIPO (BAIRRO_NOME, TIPO_USO, N_EDIFICACOES, "
        "N_ENDERECOS, AREA_SOMA_M2) "
        "SELECT COALESCE(B.NOME, %s), E.TIPO_USO, COUNT(*), SUM(E.N_ENDERECOS), "
        "COALESCE(SUM(E.AREA_M2), 0) FROM EDIFICACOES E "
        "LEFT JOIN BAIRROS B ON B.ID = E.BAIRRO_ID GROUP BY 1, 2", (config.NAO_INFORMADO,))


def _fechar_carga(cur, carga_id, inserts):
    contagens = {t: len(rows) for t, _, rows in inserts}
    cur.execute("SELECT SUM(TIPO_USO = 'desconhecido') AS n FROM EDIFICACOES")
    contagens["EDIFICACOES_DESCONHECIDO"] = int(cur.fetchone()["n"] or 0)
    cur.execute("UPDATE CARGAS SET CONCLUIDA_EM = NOW(), CONTAGENS = %s WHERE ID = %s",
                (json.dumps(contagens), carga_id))
    return contagens


def gravar_stage(predios, qg_features, assoc):
    """Recarrega todas as tabelas do banco de rascunho a partir do zero."""
    reg = construir_registros(predios, qg_features, assoc)
    inserts = montar_inserts(reg)

    print(f"Gravando no banco de rascunho ({len(reg.edificacoes)} prédios, "
          f"{len(reg.enderecos)} endereços)...")
    with transacao(config.DB_NAME_STAGE) as cur:
        _limpar_tabelas(cur)
        carga_id = _abrir_carga(cur)
        for tabela, sql, rows in inserts:
            inserir_em_lotes(cur, sql, rows)
            print(f"  {tabela}: {len(rows)}")
        _agregar_bairro_tipo(cur)
        contagens = _fechar_carga(cur, carga_id, inserts)
    print(f"  Carga concluída: {contagens}")
