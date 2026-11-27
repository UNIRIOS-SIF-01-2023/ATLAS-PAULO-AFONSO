from conftest import colecao, escrever, feature, quadrado
from importacao import config, fontes

CONTORNO = quadrado(0, 0, 1, 1)


def _contorno(tmp_path, monkeypatch, doc=None):
    caminho = escrever(tmp_path / "contorno.geojson",
                       doc or colecao(feature(CONTORNO)))
    monkeypatch.setattr(config, "MUNICIPIO_BOUNDARY", caminho)


def test_boundary_shape_aceita_feature_unica(tmp_path, monkeypatch):
    _contorno(tmp_path, monkeypatch, feature(CONTORNO))
    assert fontes.boundary_shape().area == 1.0


def test_boundary_shape_aceita_feature_collection(tmp_path, monkeypatch):
    _contorno(tmp_path, monkeypatch)
    assert fontes.boundary_shape().area == 1.0


def test_recorte_mantem_dentro_e_borda_e_descarta_fora(tmp_path, monkeypatch):
    _contorno(tmp_path, monkeypatch)
    dentro = feature(quadrado(0.2, 0.2, 0.3, 0.3), "dentro")
    borda = feature(quadrado(0.9, 0.9, 1.5, 1.5), "borda")
    fora = feature(quadrado(5, 5, 6, 6), "fora")
    r = fontes.recortar_no_municipio([dentro, borda, fora], "Teste")
    assert [f["id"] for f in r] == ["dentro", "borda"]


def test_sem_contorno_nao_recorta_e_avisa(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "MUNICIPIO_BOUNDARY", str(tmp_path / "nao_existe.geojson"))
    feats = [feature(quadrado(5, 5, 6, 6), "fora")]
    assert fontes.recortar_no_municipio(feats, "Overture") == feats
    assert "contorno do município ausente" in capsys.readouterr().out


def test_load_buildings_dedupe_por_id_e_ignora_arquivo_ausente(tmp_path, monkeypatch, capsys):
    _contorno(tmp_path, monkeypatch)
    a = escrever(tmp_path / "a.geojson", colecao(
        feature(quadrado(0.1, 0.1, 0.2, 0.2), "x", origem="a"),
        feature(quadrado(0.3, 0.3, 0.4, 0.4), "y")))
    b = escrever(tmp_path / "b.geojson", colecao(
        feature(quadrado(0.1, 0.1, 0.2, 0.2), "x", origem="b")))
    monkeypatch.setattr(config, "OVERTURE_FILES", [a, str(tmp_path / "falta.geojson"), b])
    r = fontes.load_buildings()
    assert sorted(f["id"] for f in r) == ["x", "y"]
    assert next(f for f in r if f["id"] == "x")["properties"]["origem"] == "a"  # 1º vence
    assert "não encontrado, pulando" in capsys.readouterr().out


def test_load_osm_buildings_sem_arquivo_devolve_lista_vazia(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OSM_BUILDINGS", str(tmp_path / "nao_existe.geojson"))
    assert fontes.load_osm_buildings() == []


def test_load_qg_points(tmp_path, monkeypatch):
    caminho = escrever(tmp_path / "qg.json", colecao(
        feature({"type": "Point", "coordinates": [0, 0]}, COD_UNICO_ENDERECO="1")))
    monkeypatch.setattr(config, "QG_ENDERECOS", caminho)
    assert len(fontes.load_qg_points()) == 1
