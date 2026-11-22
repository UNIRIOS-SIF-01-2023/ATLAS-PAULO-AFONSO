"""Treino e avaliação do classificador de uso, num bairro sintético."""

import json
import math

import numpy as np
import pytest
from shapely.geometry import box

from backend.ia.dados import Base
from backend.ia.features import mascara_treino, montar_features, pontos_representativos
from backend.ia.treino import avaliar, escrever_saidas, treinar_final

M_POR_GRAU = 111320.0


@pytest.fixture(scope="module")
def base_sintetica():
    """20 x 15 prédios a cada 60 m: um em 7 é comercial grande, um em 11 desconhecido."""
    cos = math.cos(math.radians(-9.40))
    geoms, tipos, origens = [], [], []
    for k in range(300):
        lng = -38.22 + (k % 20) * 60 / (M_POR_GRAU * cos)
        lat = -9.40 + (k // 20) * 60 / M_POR_GRAU
        lado = 25 if k % 7 == 0 else 10
        geoms.append(box(lng, lat, lng + lado / (M_POR_GRAU * cos), lat + lado / M_POR_GRAU))
        if k % 7 == 0:
            tipos.append("comercial"); origens.append("cnefe")
        elif k % 11 == 0:
            tipos.append("desconhecido"); origens.append("sem_dado")
        else:
            tipos.append("residencial"); origens.append("cnefe")
    geoms = np.array(geoms, dtype=object)
    return Base(ids=np.arange(1, 301), geoms=geoms, pontos=pontos_representativos(geoms),
                tipos=tipos, origens=origens, fontes=["OpenStreetMap"] * 300, bairros=[None] * 300)


@pytest.fixture(scope="module")
def metricas(base_sintetica):
    return avaliar(base_sintetica)


def test_avaliar_tem_baselines_modelos_e_metricas_por_classe(metricas):
    assert set(metricas["modelos"]) >= {"mais_frequente", "regressao_logistica",
                                        "random_forest", "gradient_boosting"}
    for nome in metricas["modelos"]:
        for cv in ("estratificada", "espacial"):
            r = metricas["modelos"][nome][cv]
            assert {"f1_macro", "acuracia", "por_classe", "matriz_confusao"} <= set(r)
    assert metricas["melhor"]["modelo"] in metricas["modelos"] and "params" in metricas["melhor"]
    assert {"brier_por_classe", "erros_mais_confiantes"} <= set(metricas["modelo_final"])


def test_modelo_ganha_do_mais_frequente(metricas):
    melhor = metricas["melhor"]["modelo"]
    assert (metricas["modelos"][melhor]["espacial"]["f1_macro"]
            > metricas["modelos"]["mais_frequente"]["espacial"]["f1_macro"])


def test_avaliar_e_reprodutivel(base_sintetica, metricas):
    assert avaliar(base_sintetica) == metricas


def test_treino_final_preve_probabilidades_somando_1(base_sintetica):
    p = treinar_final(base_sintetica, {"modelo": "random_forest"})
    b = base_sintetica
    X, _ = montar_features(b.geoms, b.fontes, b.tipos, mascara_treino(b.tipos, b.origens))
    proba = p["modelo"].predict_proba(X[:5])
    assert np.allclose(proba.sum(axis=1), 1)
    assert set(p["classes"]) == {"comercial", "residencial"}
    assert p["versao"].startswith("uso-") and p["nomes_features"][0] == "area_m2"


def test_escrever_saidas_gera_joblib_json_e_relatorio(tmp_path, base_sintetica, metricas):
    payload = treinar_final(base_sintetica, metricas["melhor"]["params"])
    relatorio = tmp_path / "avaliacao.md"
    escrever_saidas(payload, metricas, tmp_path, relatorio)
    assert (tmp_path / "modelo_uso.joblib").stat().st_size > 0
    info = json.loads((tmp_path / "modelo_uso.json").read_text(encoding="utf-8"))
    assert {"metricas", "contagem_por_classe", "versao", "semente", "treinado_em"} <= set(info)
    assert info["semente"] == 42 and info["versao"] == payload["versao"]
    texto = relatorio.read_text(encoding="utf-8")
    assert "Matriz de confusão" in texto and "F1" in texto
