"""Features do classificador de uso: geometria, fonte e vizinhança."""

import math

import numpy as np
import pytest
from shapely.geometry import box

from backend.ia.features import (
    bairro_mais_proximo,
    grupos_espaciais,
    mascara_previsao,
    mascara_treino,
    montar_features,
)

M_POR_GRAU = 111320.0


def quadrado(lng, lat, lado_m):
    dx = lado_m / (M_POR_GRAU * math.cos(math.radians(lat)))
    dy = lado_m / M_POR_GRAU
    return box(lng, lat, lng + dx, lat + dy)


def dois_predios():
    """Dois quadrados de 10 m, 30 m um do outro (leste-oeste)."""
    passo = 30 / (M_POR_GRAU * math.cos(math.radians(-9.40)))
    return np.array([quadrado(-38.22, -9.40, 10), quadrado(-38.22 + passo, -9.40, 10)])


def _f(X, nomes, i=0):
    return dict(zip(nomes, X[i]))


def test_quadrado_tem_compacidade_de_quadrado():
    X, nomes = montar_features(np.array([quadrado(-38.22, -9.40, 20)]), ["OpenStreetMap"],
                               ["residencial"], np.array([True]))
    f = _f(X, nomes)
    assert f["area_m2"] == pytest.approx(400, rel=0.03)
    assert f["perimetro_m"] == pytest.approx(80, rel=0.03)
    assert f["compacidade"] == pytest.approx(math.pi / 4, rel=0.03)
    assert f["ocupacao_ret"] == pytest.approx(1, rel=0.03)


def test_proprio_rotulo_nao_entra_na_vizinhanca():
    X, nomes = montar_features(dois_predios(), ["OpenStreetMap"] * 2,
                               ["comercial", "residencial"], np.array([True, True]))
    f0 = _f(X, nomes)
    assert f0["viz_frac_comercial"] == 0 and f0["viz_frac_residencial"] == 1
    assert f0["n_viz_150m"] == 1 and f0["n_perto_50m"] == 1


def test_rotulo_escondido_nao_vaza():
    X, nomes = montar_features(dois_predios(), ["OpenStreetMap"] * 2,
                               ["comercial", "residencial"], np.array([True, False]))
    f0 = _f(X, nomes)
    assert f0["viz_frac_residencial"] == 0 and f0["viz_frac_rotulados"] == 0
    # O comercial conhecido é o próprio prédio 0: não conta como vizinho.
    assert _f(X, nomes, 1)["dist_comercial_m"] == pytest.approx(30, rel=0.1)


def test_nenhuma_feature_proibida():
    _, nomes = montar_features(dois_predios(), ["OpenStreetMap"] * 2,
                               ["comercial", "residencial"], np.array([True, True]))
    assert not any(p in n for n in nomes for p in ("bairro", "endereco", "origem", "pavimento"))


def test_predio_isolado_e_fonte_nova_geram_features_finitas():
    X, nomes = montar_features(np.array([quadrado(-38.0, -9.0, 10)]), ["Fonte Nova"],
                               ["desconhecido"], np.array([False]))
    assert np.isfinite(X).all()
    assert _f(X, nomes)["fonte_outra"] == 1


def test_mascaras():
    tipos = ["residencial", "desconhecido", "comercial", "desconhecido"]
    origens = ["cnefe", "sem_dado", "overture_subtype", "cnefe"]
    assert mascara_treino(tipos, origens).tolist() == [True, False, False, False]
    assert mascara_previsao(tipos).tolist() == [False, True, False, True]


def test_bairro_mais_proximo():
    pontos = np.array([[-38.22, -9.40], [-38.2201, -9.40], [-38.30, -9.40]])
    assert bairro_mais_proximo(pontos, ["CENTRO", None, "BTN"]) == ["CENTRO", "CENTRO", "BTN"]


def test_bairro_mais_proximo_sem_nenhum_bairro():
    assert bairro_mais_proximo(np.array([[-38.22, -9.40]]), [None]) == [None]


def test_grupos_espaciais_separam_celulas_de_500m():
    g = grupos_espaciais(np.array([[-38.22, -9.40], [-38.2201, -9.40], [-38.21, -9.40]]))
    assert g[0] == g[1] and g[0] != g[2]
