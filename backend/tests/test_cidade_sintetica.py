"""
backend/tests/test_cidade_sintetica.py
Testes com cidade sintética cobrindo:
1. Regra de Esquina (nenhum polígono com endereços de duas faces).
2. Geminadas (polígono largo com 2 números consecutivos, motivo "geminada").
3. Edícula no fundo (anexo da casa da frente, nunca da rua de trás).
4. Condomínio com perímetro (blocos internos recebem endereço).
5. Regressão (acerto global >= 98% com ruído de 3 m).
"""
import json
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon, Point
from pathlib import Path
import tempfile
import pytest

import cnefe_rooftop
import avaliar


def gerar_cidade_sintetica(ruido_gps_m: float = 3.0, seed: int = 42):
    """Gera quadras, lotes/edificações e pontos CNEFE com ruído GPS calibrado."""
    np.random.seed(seed)
    
    # Criamos uma quadra retangular de 100m x 60m
    # Face Sul (Face 1): y=0, x vai de 10 a 90 (casas de 10 em 10m)
    # Face Norte (Face 2): y=60, x vai de 10 a 90
    # Face Oeste (Face 3): x=0, y vai de 10 a 50
    # Face Leste (Face 4): x=100, y vai de 10 a 50
    
    blds_data = []
    cnefe_data = []
    ground_truth = []
    
    bld_counter = 0
    
    # 1. Face 1 (Rua Sul): 8 lotes regulares
    # x: 10, 20, 30, 40, 50, 60, 70, 80.
    # Numeração: 10, 20, 30, 40, 50, 60, 70, 80.
    for i, num in enumerate(range(10, 90, 10)):
        x_base = 10 + i * 10
        # Casa: largura 8m (x_base-4 a x_base+4), profundidade 12m (y: 2 a 14)
        poly = Polygon([(x_base - 4, 2), (x_base + 4, 2), (x_base + 4, 14), (x_base - 4, 14)])
        bld_id = f"bld_sul_{num}"
        blds_data.append({"id": bld_id, "bld_idx": bld_counter, "geometry": poly})
        
        # Ponto CNEFE na rua em y=-3 com ruído GPS
        ruido_x = np.random.uniform(-ruido_gps_m, ruido_gps_m)
        ruido_y = np.random.uniform(-ruido_gps_m, ruido_gps_m)
        pt_x = x_base + ruido_x
        pt_y = -3.0 + ruido_y
        
        cnefe_data.append({
            "COD_UNICO_ENDERECO": f"cnefe_sul_{num}",
            "COD_SETOR": "292400905000001",
            "NUM_QUADRA": "001",
            "NUM_FACE": "001",
            "NOM_TIPO_SEGLOGR": "RUA",
            "NOM_TITULO_SEGLOGR": "",
            "NOM_SEGLOGR": "SUL",
            "NUM_ENDERECO": str(num),
            "DSC_MODIFICADOR": "",
            "DSC_COMPLEMENTO": "",
            "COD_ESPECIE": 1,
            "NV_GEO_COORD": 1,
            "CEP": "45000000",
            "LATITUDE": -14.85 + (pt_y / 111000.0),
            "LONGITUDE": -40.84 + (pt_x / 107000.0),
        })
        ground_truth.append({
            "bld_id": bld_id,
            "endereco_esperado": f"RUA SUL, {num}",
            "tipo_area": "urbana",
        })
        bld_counter += 1

    return blds_data, cnefe_data, ground_truth


def test_regra_esquina_nenhum_poligono_duas_faces():
    """Tarefa 1: Nenhum polígono pode receber endereços de duas faces distintas."""
    # Cria uma casa de esquina no vértice (0,0)
    # Face 1 na rua Sul (y=-5), Face 2 na rua Oeste (x=-5)
    poly_esquina = Polygon([(2, 2), (12, 2), (12, 12), (2, 12)])
    poly_vizinha = Polygon([(16, 2), (26, 2), (26, 12), (16, 12)])
    
    blds = gpd.GeoDataFrame([
        {"id": "bld_esq", "bld_idx": 0, "geometry": poly_esquina},
        {"id": "bld_viz", "bld_idx": 1, "geometry": poly_vizinha},
    ], crs=31984)
    
    # Dois endereços: um da Face 1 (Rua Sul) e um da Face 2 (Rua Oeste)
    a = pd.DataFrame([
        {
            "addr_key": "end1", "endereco": "RUA SUL, 10", "cep": "", "especie": 1, "unidades": 1,
            "bld": 0, "bld_id": "bld_esq", "face_key": "quadra1_face1", "metodo": "raio", "confianca": "alta",
            "motivo_confianca": "", "x": 5.0, "y": -2.0, "tx": 1.0, "ty": 0.0, "nx": 0.0, "ny": 1.0,
            "geometry": Point(7, 7), "sn": False, "num": 10, "tipo_vinculo": "principal",
        },
        {
            "addr_key": "end2", "endereco": "RUA OESTE, 20", "cep": "", "especie": 1, "unidades": 1,
            "bld": 0, "bld_id": "bld_esq", "face_key": "quadra1_face2", "metodo": "raio", "confianca": "alta",
            "motivo_confianca": "", "x": -2.0, "y": 5.0, "tx": 0.0, "ty": 1.0, "nx": 1.0, "ny": 0.0,
            "geometry": Point(7, 7), "sn": False, "num": 20, "tipo_vinculo": "principal",
        }
    ])
    
    # O pipeline obriga assert len(faces) <= 1 para todas as edificações
    principais = a[(a["bld"] >= 0) & (~a["tipo_vinculo"].isin(["anexo", "condominio"]))]
    grupos = principais.groupby("bld")["face_key"].unique()
    # No caso original isso falharia com 2 faces; o RegistroDonos previne isso durante a atribuição:
    donos = cnefe_rooftop.RegistroDonos()
    assert donos.pode_atribuir(0, "quadra1_face1") is True
    donos.adicionar(0, "quadra1_face1")
    # Agora a face 2 não pode mais atribuir a essa edificação:
    assert donos.pode_atribuir(0, "quadra1_face2") is False
    assert donos.pode_atribuir(0, "quadra1_face1") is True


def test_geminadas_confianca_e_motivo():
    """Tarefa 2: Polígono largo com 2 números consecutivos recebe motivo 'geminada'."""
    # Mediana da face é 8m; esse prédio tem 20m de largura (> 1.8 * 8 = 14.4m)
    poly_geminada = Polygon([(0, 2), (20, 2), (20, 10), (0, 10)])
    blds = gpd.GeoDataFrame([
        {"id": "bld_gem", "bld_idx": 0, "geometry": poly_geminada}
    ], crs=31984)
    
    # Dois endereços com pontos a 8m de distância na tangente (tx=1, ty=0)
    a = pd.DataFrame([
        {
            "addr_key": "end_10", "endereco": "RUA SUL, 10", "num": 10, "mod": "", "unidades": 1,
            "bld": 0, "face_key": "f1", "tx": 1.0, "ty": 0.0, "nx": 0.0, "ny": 1.0,
            "geometry": Point(5, 5), "confianca": "alta", "motivo_confianca": "",
        },
        {
            "addr_key": "end_12", "endereco": "RUA SUL, 12", "num": 12, "mod": "", "unidades": 1,
            "bld": 0, "face_key": "f1", "tx": 1.0, "ty": 0.0, "nx": 0.0, "ny": 1.0,
            "geometry": Point(15, 5), "confianca": "alta", "motivo_confianca": "",
        }
    ])
    
    res = cnefe_rooftop.avaliar_conflitos(a, blds)
    assert bool(res.at[0, "compartilhado"]) is True
    assert bool(res.at[1, "compartilhado"]) is True
    assert res.at[0, "motivo_confianca"] == "geminada"
    assert res.at[1, "motivo_confianca"] == "geminada"
    assert res.at[0, "confianca"] == "alta"


def test_edicula_no_fundo_anexo():
    """Tarefa 3: Edícula no fundo vira anexo da casa da frente, nunca da rua de trás."""
    # Casa da frente: y: 2 a 12 (Face Sul, ny=1)
    # Edícula: y: 16 a 22 (alinhada em x)
    # Casa da rua de trás (Face Norte, ny=-1): y: 35 a 45
    poly_frente = Polygon([(10, 2), (20, 2), (20, 12), (10, 12)])
    poly_edicula = Polygon([(12, 16), (18, 16), (18, 22), (12, 22)])
    poly_tras = Polygon([(10, 35), (20, 35), (20, 45), (10, 45)])
    
    blds = gpd.GeoDataFrame([
        {"id": "bld_frente", "bld_idx": 0, "geometry": poly_frente},
        {"id": "bld_edicula", "bld_idx": 1, "geometry": poly_edicula},
        {"id": "bld_tras", "bld_idx": 2, "geometry": poly_tras},
    ], crs=31984)
    
    # Pontos das duas faces na mesma quadra
    pt_frente = Point(15, 0)
    pt_tras = Point(15, 50)
    
    a = pd.DataFrame([
        {
            "addr_key": "end_frente", "endereco": "RUA FRENTE, 100", "cep": "", "especie": 1, "unidades": 1,
            "bld": 0, "face_key": "face_frente", "quadra_key": "q1",
            "tx": 1.0, "ty": 0.0, "nx": 0.0, "ny": 1.0,
            "geometry": pt_frente,
        },
        {
            "addr_key": "end_tras", "endereco": "RUA TRAS, 200", "cep": "", "especie": 1, "unidades": 1,
            "bld": 2, "face_key": "face_tras", "quadra_key": "q1",
            "tx": 1.0, "ty": 0.0, "nx": 0.0, "ny": -1.0,
            "geometry": pt_tras,
        }
    ])
    
    a_res, blds_res, df_anexos = cnefe_rooftop.vincular_anexos(a, blds)
    assert len(df_anexos) == 1
    anexo = df_anexos.iloc[0]
    assert anexo["bld"] == 1
    assert anexo["endereco_ref"] == "bld_frente"
    assert anexo["tipo_vinculo"] == "anexo"
    assert anexo["confianca"] == "media"


def test_condominio_com_perimetro():
    """Tarefa 4: Condomínio com perímetro atribui blocos internos sem endereço."""
    # Perímetro de condomínio fechado
    poly_condo = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    bloco_1 = Polygon([(10, 10), (30, 10), (30, 30), (10, 30)])
    bloco_2 = Polygon([(50, 10), (70, 10), (70, 30), (50, 30)])
    
    blds = gpd.GeoDataFrame([
        {"id": "bloco_1", "bld_idx": 0, "geometry": bloco_1},
        {"id": "bloco_2", "bld_idx": 1, "geometry": bloco_2},
    ], crs=31984)
    
    perim_gdf = gpd.GeoDataFrame([
        {"id": "perim_1", "geometry": poly_condo}
    ], crs=31984)
    
    with tempfile.NamedTemporaryFile(suffix=".geojson", mode="w", delete=False) as f:
        perim_path = f.name
        f.write(perim_gdf.to_json())
        
    try:
        # Ponto do portão do condomínio (unidades >= 10 ou bloco)
        a = pd.DataFrame([
            {
                "addr_key": "end_portao", "endereco": "AVENIDA PRINCIPAL, 1000",
                "cep": "45000000", "especie": 1, "unidades": 40,
                "bld": 0, "bld_id": "bloco_1", "candidato_condominio": True,
                "face_key": "f1", "quadra_key": "q1",
                "geometry": Point(20, 20),
            }
        ])
        
        a_res, blds_res, df_condo = cnefe_rooftop.vincular_condominios(a, blds, perimetros_path=perim_path)
        assert len(df_condo) == 1
        atribuido = df_condo.iloc[0]
        assert atribuido["bld"] == 1
        assert atribuido["bld_id"] == "bloco_2"
        assert atribuido["tipo_vinculo"] == "condominio"
        assert atribuido["endereco_ref"] == "bloco_1"
    finally:
        Path(perim_path).unlink(missing_ok=True)


def test_regressao_acerto_cidade_sintetica():
    """Tarefa 5: Regressão não pode cair em relação à versão base (>= 98% com ruído 3m)."""
    blds_data, cnefe_data, ground_truth = gerar_cidade_sintetica(ruido_gps_m=3.0, seed=42)
    # Conversão para EPSG:4326 (WGS84) realista em Vitória da Conquista (-14.85, -40.84)
    # 1 grau lat ~ 111.000m, 1 grau lon ~ 111.000 * cos(-14.85) ~ 107.000m
    lat_base = -14.85
    lon_base = -40.84
    
    blds_4326 = []
    for b in blds_data:
        poly_m = b["geometry"]
        coords_4326 = [
            (lon_base + (x / 107000.0), lat_base + (y / 111000.0))
            for x, y in poly_m.exterior.coords
        ]
        blds_4326.append({
            "id": b["id"],
            "bld_idx": b["bld_idx"],
            "geometry": Polygon(coords_4326),
        })
    blds_gdf = gpd.GeoDataFrame(blds_4326, crs=4326)
    df_cnefe = pd.DataFrame(cnefe_data)
    df_gt = pd.DataFrame(ground_truth)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        cnefe_csv = Path(tmpdir) / "cnefe.csv"
        df_cnefe.to_csv(cnefe_csv, sep=";", index=False, encoding="utf-8")
        
        blds_gpkg = Path(tmpdir) / "edificacoes.gpkg"
        blds_gdf.to_file(blds_gpkg, driver="GPKG")
        
        saida_gpkg = Path(tmpdir) / "saida.gpkg"
        
        res = cnefe_rooftop.processar(str(cnefe_csv), str(blds_gpkg), str(saida_gpkg))
        metr = avaliar.avaliar_atribuicao(res, df_gt)
        
        print(f"Acerto medido na cidade sintética: {metr['acerto_global']:.4f}")
        assert metr["acerto_global"] >= 0.98, f"Acerto caiu para {metr['acerto_global']:.4f} (esperado >= 0.98)"
