import json

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

import cnefe_rooftop_alg


def _escrever_edificacoes(path):
    gdf = gpd.GeoDataFrame(
        {"id": ["perto"]},
        geometry=[Polygon([
            (-40.0001, -10.0001), (-39.9999, -10.0001),
            (-39.9999, -9.9999), (-40.0001, -9.9999),
        ])],
        crs=4326,
    )
    gdf.to_file(path, driver="GeoJSON")


def test_relatorio_entrada_mede_campos_nivel_faces_distancia_e_privacidade(tmp_path):
    cnefe = tmp_path / "entrada.csv"
    edificios = tmp_path / "edificios.geojson"
    report = tmp_path / "relatorio_entrada.html"
    cnefe.write_text(
        "COD_UNICO_ENDERECO;COD_SETOR;NUM_QUADRA;NUM_FACE;NUM_ENDERECO;NOM_SEGLOGR;"
        "LATITUDE;LONGITUDE;NV_GEO_COORD;COMPLEMENTO\n"
        "1;S1;Q1;F1;10;RUA A;-10.0;-40.0;1;APARTAMENTO 2\n"
        "2;S1;Q1;F1;12;RUA A;-10.00001;-40.00001;1;NOME DA PESSOA\n"
        "3;S2;Q2;F1;;RUA B;-10.01;-39.99;1;\n"
        "4;S3;Q3;F1;20;RUA C;-10.02;-39.98;2;\n",
        encoding="utf-8",
    )
    _escrever_edificacoes(edificios)

    result = cnefe_rooftop_alg.gerar_relatorio_entrada(cnefe, edificios, report)

    assert result["total_registros"] == 4
    assert result["descartados_nivel"] == 1
    assert result["faltantes"]["número"]["registros"] == 1
    assert result["faces_1_2_enderecos"] == 2
    assert result["faces_sem_reta_valida"] == 2
    assert result["pontos_mais_40m"] == 1
    body = report.read_text(encoding="utf-8")
    assert "APARTAMENTO 2" in body
    assert "NOME DA PESSOA" not in body
    assert "texto omitido por privacidade" in body


def test_relatorio_para_se_geojson_nao_tem_chaves_de_face(tmp_path):
    cnefe = tmp_path / "qg.geojson"
    cnefe.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-40, -10]},
            "properties": {"COD_UNICO_ENDERECO": "1"},
        }],
    }), encoding="utf-8")
    edificios = tmp_path / "edificios.geojson"
    _escrever_edificacoes(edificios)

    with pytest.raises(ValueError, match="Quadro Geográfico sem COD_SETOR, NUM_QUADRA, NUM_FACE"):
        cnefe_rooftop_alg.gerar_relatorio_entrada(cnefe, edificios, tmp_path / "report.html")
