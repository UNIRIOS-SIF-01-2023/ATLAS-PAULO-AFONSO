import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

import algoritmo_raycast as cnefe_rooftop_alg


def test_direcao_ibge_tem_preferencia_e_fallback_svd_e_centro(tmp_path):
    path = tmp_path / "faces.geojson"
    faces = gpd.GeoDataFrame(
        {"CD_SETOR": ["S1"], "CD_QUADRA": ["Q1"], "CD_FACE": ["F1"]},
        geometry=[LineString([(0, 0), (0, 20)])],
        crs=31984,
    )
    faces.to_file(path, driver="GeoJSON")
    index = cnefe_rooftop_alg.carregar_faces_ibge(path, 31984)

    rows = []
    for i, x in enumerate((0.0, 1.0, 2.0, 3.0)):
        rows.append({
            "addr_key": f"ibge-{i}", "endereco": f"Rua A, {i + 1}",
            "setor_cnefe": "S1", "quadra_cnefe": "Q1", "face_cnefe": "F1",
            "quadra_key": "S1_Q1", "face_key": "S1_Q1_F1", "x": x, "y": 0.0,
        })
        rows.append({
            "addr_key": f"svd-{i}", "endereco": f"Rua B, {i + 1}",
            "setor_cnefe": "S2", "quadra_cnefe": "Q2", "face_cnefe": "F1",
            "quadra_key": "S2_Q2", "face_key": "S2_Q2_F1", "x": 10.0 + 10.0 * i, "y": 10.0,
        })
    rows.append({
        "addr_key": "centro-1", "endereco": "Rua C, 1",
        "setor_cnefe": "S3", "quadra_cnefe": "Q3", "face_cnefe": "F1",
        "quadra_key": "S3_Q3", "face_key": "S3_Q3_F1", "x": 30.0, "y": 30.0,
    })

    result = cnefe_rooftop_alg.calcular_normais(pd.DataFrame(rows), faces_ibge=index)

    ibge = result[result["fonte_direcao"] == "faces_ibge"]
    svd = result[result["fonte_direcao"] == "svd"]
    centro = result[result["fonte_direcao"] == "centro_quadra"]
    assert len(ibge) == 4
    assert np.allclose(ibge["tx"], 0.0, atol=1e-6)
    assert np.allclose(np.abs(ibge["ty"]), 1.0, atol=1e-6)
    assert len(svd) == 4
    assert np.allclose(np.abs(svd["tx"]), 1.0, atol=1e-6)
    assert len(centro) == 1
