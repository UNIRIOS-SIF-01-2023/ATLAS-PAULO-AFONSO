import pytest

from importacao import cli, config


@pytest.fixture
def espioes(monkeypatch):
    chamadas = []
    monkeypatch.setattr(cli, "montar_predios", lambda: ("P", "Q", "A"))
    monkeypatch.setattr(cli, "gravar_stage", lambda *a: chamadas.append(("stage", a)))
    monkeypatch.setattr(cli, "promover", lambda: chamadas.append(("promover",)))
    monkeypatch.setattr(config, "DB_NAME", "prod")
    monkeypatch.setattr(config, "DB_NAME_STAGE", "stage")
    return chamadas


def test_sem_flag_so_grava_rascunho(espioes):
    cli.main([])
    assert espioes == [("stage", ("P", "Q", "A"))]


def test_com_flag_promove_depois_do_rascunho(espioes):
    cli.main(["--promover"])
    assert [c[0] for c in espioes] == ["stage", "promover"]


@pytest.mark.parametrize("prod,stage", [("x", "x"), ("", "stage"), ("prod", None)])
def test_recusa_bancos_ausentes_ou_iguais(espioes, monkeypatch, prod, stage):
    monkeypatch.setattr(config, "DB_NAME", prod)
    monkeypatch.setattr(config, "DB_NAME_STAGE", stage)
    with pytest.raises(SystemExit):
        cli.main([])
    assert espioes == []
