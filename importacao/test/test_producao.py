import pytest

from importacao import config
from importacao.producao import colunas_gravaveis, promover


def test_colunas_gravaveis_junta_nomes(conexao_falsa):
    conn = conexao_falsa()
    with conn.cursor() as cur:
        assert colunas_gravaveis(cur, "FONTES") == "ID, NOME"


def test_promover_aborta_com_rascunho_vazio(conexao_falsa):
    conn = conexao_falsa(n=0)
    with pytest.raises(SystemExit):
        promover()
    assert conn.rollbacks == 1 and conn.commits == 0
    assert not any(s.startswith("DELETE") for s in conn.sqls())


def test_promover_apaga_filhos_primeiro_e_copia_pais_primeiro(conexao_falsa):
    conn = conexao_falsa(n=3)
    promover()
    sqls = conn.sqls()
    deletes = [s.split()[-1] for s in sqls if s.startswith("DELETE")]
    inserts = [s.split()[2] for s in sqls
               if s.startswith("INSERT INTO") and "information_schema" not in s]
    assert deletes == list(reversed(config.TABELAS))
    assert inserts == config.TABELAS + ["CARGAS"]
    assert conn.commits == 1 and conn.rollbacks == 0
