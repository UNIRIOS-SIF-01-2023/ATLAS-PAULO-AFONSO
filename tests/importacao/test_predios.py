from conftest import colecao, escrever, feature, quadrado
from importacao import config
from importacao.predios import fonte_principal, montar_predios


def test_fonte_principal_sem_sources_assume_osm():
    assert fonte_principal([]) == ("OpenStreetMap", "ODbL-1.0", None)


def test_fonte_principal_usa_primeiro_dataset():
    s = [{"dataset": "Overture", "license": "CDLA", "confidence": 0.9}, {"dataset": "outro"}]
    assert fonte_principal(s) == ("Overture", "CDLA", 0.9)


def test_montar_predios_ponta_a_ponta(tmp_path, monkeypatch):
    # Overture: prédio A. OSM: A' (sobrepõe A -> descartado) e B (só no OSM).
    overture = escrever(tmp_path / "ov.geojson", colecao(
        feature(quadrado(0, 0, 0.001, 0.001), "ov-A", subtype="residential",
                sources=[{"dataset": "Overture", "license": "CDLA", "confidence": 0.9}])))
    osm = escrever(tmp_path / "osm.geojson", colecao(
        feature(quadrado(0.0005, 0.0005, 0.0015, 0.0015), osm_id="way/2", building="house"),
        feature(quadrado(0.01, 0.01, 0.011, 0.011), osm_id="way/1", building="church")))
    ponto = lambda x, y: feature({"type": "Point", "coordinates": [x, y]})
    qg = escrever(tmp_path / "qg.json", colecao(
        ponto(0.0005, 0.0005),   # dentro de A
        ponto(0.0105, 0.0105),   # dentro de B (OSM)
        ponto(1, 1)))            # sem prédio
    contorno = escrever(tmp_path / "contorno.geojson", colecao(feature(quadrado(-1, -1, 2, 2))))

    monkeypatch.setattr(config, "OVERTURE_FILES", [overture])
    monkeypatch.setattr(config, "OSM_BUILDINGS", osm)
    monkeypatch.setattr(config, "QG_ENDERECOS", qg)
    monkeypatch.setattr(config, "MUNICIPIO_BOUNDARY", contorno)

    predios, qg_features, assoc = montar_predios()

    assert [p["id_origem"] for p in predios] == ["ov-A", "osm-way-1"]
    assert predios[0]["fonte"] == "Overture" and predios[0]["confianca"] == 0.9
    assert "sources" not in predios[0]["props"]
    assert predios[1]["props"]["class"] == "church"
    assert len(qg_features) == 3
    assert assoc == {0: (0, "contido", 0.0), 1: (1, "contido", 0.0)}


def test_montar_predios_sem_overture_usa_so_osm(tmp_path, monkeypatch):
    osm = escrever(tmp_path / "osm.geojson", colecao(
        feature(quadrado(0, 0, 0.001, 0.001), building="house")))  # sem osm_id: usa o índice
    qg = escrever(tmp_path / "qg.json", colecao(
        feature({"type": "Point", "coordinates": [0.0005, 0.0005]})))
    monkeypatch.setattr(config, "OVERTURE_FILES", [str(tmp_path / "falta.geojson")])
    monkeypatch.setattr(config, "OSM_BUILDINGS", osm)
    monkeypatch.setattr(config, "QG_ENDERECOS", qg)
    monkeypatch.setattr(config, "MUNICIPIO_BOUNDARY", str(tmp_path / "falta2.geojson"))

    predios, _, assoc = montar_predios()
    assert [p["id_origem"] for p in predios] == ["osm-0"]
    assert assoc == {0: (0, "contido", 0.0)}
