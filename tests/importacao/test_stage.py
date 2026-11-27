from importacao import config
from importacao.registros import Registros
from importacao.stage import GEO, gravar_stage, inserir_em_lotes, montar_inserts
from test_registros import endereco, predio


class CursorMinimo:
    def __init__(self):
        self.chamadas = []

    def execute(self, sql, params=None):
        self.chamadas.append((sql, params))


def test_inserir_em_lotes_divide_em_lotes_e_acha_os_parametros():
    cur = CursorMinimo()
    rows = [(i, f"n{i}") for i in range(1200)]
    inserir_em_lotes(cur, "INSERT INTO T (A, B) VALUES (%s, %s)", rows, tamanho=500)
    assert [c[0].count("(%s, %s)") for c in cur.chamadas] == [500, 500, 200]
    assert len(cur.chamadas[0][1]) == 1000
    assert cur.chamadas[2][1][-2:] == [1199, "n1199"]


def test_inserir_em_lotes_sem_linhas_nao_executa_nada():
    cur = CursorMinimo()
    inserir_em_lotes(cur, "INSERT INTO T (A) VALUES (%s)", [])
    assert cur.chamadas == []


def test_inserir_em_lotes_preserva_expressao_geografica():
    cur = CursorMinimo()
    inserir_em_lotes(cur, f"INSERT INTO T (A, G) VALUES (%s, {GEO})", [(1, "{}"), (2, "{}")])
    assert cur.chamadas[0][0].count("ST_GeomFromGeoJSON") == 2


def test_montar_inserts_ordem_e_geometrias():
    reg = Registros({}, {}, [], [], [], [])
    inserts = montar_inserts(reg)
    assert [t for t, _, _ in inserts] == config.TABELAS[:-1]  # AGG é calculada no banco
    sql = {t: s for t, s, _ in inserts}
    assert sql["EDIFICACOES"].count("ST_GeomFromGeoJSON") == 2
    assert sql["ENDERECOS"].count("ST_GeomFromGeoJSON") == 1


def _dados():
    qg = [endereco(1, "1", "Centro")]
    return [predio({})], qg, {0: (0, "contido", 0.0)}


def test_gravar_stage_commita_e_limpa_antes_de_inserir(conexao_falsa):
    conn = conexao_falsa()
    gravar_stage(*_dados())
    sqls = conn.sqls()
    assert conn.commits == 1 and conn.rollbacks == 0 and conn.fechada == 1
    truncados = [s for s in sqls if s.startswith("TRUNCATE")]
    assert len(truncados) == len(config.TABELAS) + 1  # + CARGAS
    assert max(sqls.index(s) for s in truncados) < min(
        i for i, s in enumerate(sqls) if s.startswith("INSERT INTO FONTES"))
    assert any(s.startswith("INSERT INTO AGG_BAIRRO_TIPO") for s in sqls)
    assert sqls[-1].startswith("UPDATE CARGAS")


def test_gravar_stage_desfaz_tudo_se_algo_falhar(conexao_falsa):
    conn = conexao_falsa(falhar_em="INSERT INTO ENDERECOS")
    try:
        gravar_stage(*_dados())
    except RuntimeError:
        pass
    else:
        raise AssertionError("deveria propagar o erro")
    assert conn.commits == 0 and conn.rollbacks == 1 and conn.fechada == 1
