import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

import cnefe_rooftop_alg


def test_evidencias_sao_anotadas_sem_mudar_atribuicao_e_lote_discordante_revisa(tmp_path):
    building = Polygon([(0, 0), (6, 0), (6, 6), (0, 6)])
    blds = gpd.GeoDataFrame(
        {"id": ["b1"], "class": ["residential"]},
        geometry=[building], crs=31984,
    )
    a = pd.DataFrame([
        {"bld": 0, "num": 5, "sn": False, "unidades": 10, "especie": "2", "face_key": "f1"},
        {"bld": 0, "num": 7, "sn": False, "unidades": 10, "especie": "2", "face_key": "f1"},
        {"bld": 0, "num": 9, "sn": False, "unidades": 10, "especie": "2", "face_key": "f1"},
    ])
    original = a["bld"].copy()
    lotes = tmp_path / "lotes.geojson"
    gpd.GeoDataFrame(
        {"numero": ["5"]}, geometry=[building], crs=31984,
    ).to_file(lotes, driver="GeoJSON")
    osm = tmp_path / "osm.geojson"
    gpd.GeoDataFrame(
        {"addr:housenumber": ["5"]}, geometry=[Point(2, 2)], crs=31984,
    ).to_file(osm, driver="GeoJSON")

    result = cnefe_rooftop_alg.aplicar_evidencias(a, blds, lotes, osm)

    assert result["bld"].equals(original)
    assert result["lote_prefeitura"].tolist() == ["concorda", "discorda", "discorda"]
    assert result["osm_numero"].tolist() == ["concorda", "discorda", "discorda"]
    assert result["paridade"].tolist() == ["concorda"] * 3
    assert result["porte"].tolist() == ["discorda"] * 3
    assert result["coerencia_especie"].tolist() == ["sem_dado"] * 3
    assert result["n_concordam"].tolist() == [3, 1, 1]
    assert result["n_discordam"].tolist() == [1, 3, 3]
    assert result["revisar_face"].tolist() == [False, True, True]
    assert "lote_prefeitura=discorda" in result.loc[1, "evidencias"]
