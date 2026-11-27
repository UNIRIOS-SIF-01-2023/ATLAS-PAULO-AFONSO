from shapely.geometry import shape

from scripts import fetch_osm_buildings
from scripts.fetch_osm_buildings import extrair_endereco_osm


def test_osm_address_node_preserva_apenas_tags_de_endereco():
    feature = extrair_endereco_osm({
        "type": "node", "id": 12, "lon": -38.2, "lat": -9.4,
        "tags": {
            "addr:housenumber": "15", "addr:street": "Rua A",
            "name": "dado que não deve ser exportado",
        },
    })

    assert feature["id"] == "node/12"
    assert shape(feature["geometry"]).geom_type == "Point"
    assert feature["properties"]["addr:housenumber"] == "15"
    assert feature["properties"]["addr:street"] == "Rua A"
    assert "name" not in feature["properties"]


def test_osm_address_way_gera_ponto_dentro_da_geometria():
    feature = extrair_endereco_osm({
        "type": "way", "id": 34,
        "geometry": [
            {"lon": 0, "lat": 0}, {"lon": 10, "lat": 0},
            {"lon": 10, "lat": 10}, {"lon": 0, "lat": 10},
            {"lon": 0, "lat": 0},
        ],
        "tags": {"addr:housenumber": "20", "addr:place": "Centro"},
    })

    assert feature["id"] == "way/34"
    assert shape(feature["geometry"]).geom_type == "Point"
    assert feature["properties"]["addr:place"] == "Centro"


def test_overpass_busca_pegadas_e_enderecos_na_mesma_consulta(monkeypatch):
    query = {}
    monkeypatch.setattr(fetch_osm_buildings, "overpass", lambda value, timeout: query.update(text=value) or {})

    fetch_osm_buildings.fetch_cell(-10, -40, -9, -38)

    assert 'way["building"]' in query["text"]
    assert 'nw["addr:housenumber"]' in query["text"]
