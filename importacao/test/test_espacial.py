import pytest

from conftest import quadrado
from importacao.espacial import match_points, polygon_of
from shapely.geometry import shape

LADO = 0.001
POLIGONO = shape(quadrado(0, 0, LADO, LADO))


def ponto(x, y):
    return {"geometry": {"type": "Point", "coordinates": [x, y]}}


def test_polygon_of_valido():
    g = polygon_of({"geometry": quadrado(0, 0, 1, 1)})
    assert g.geom_type == "Polygon"


def test_polygon_of_conserta_geometria_invalida():
    gravata = {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}
    g = polygon_of({"geometry": gravata})
    assert g is not None and g.is_valid


def test_polygon_of_rejeita_nao_poligono():
    assert polygon_of({"geometry": {"type": "Point", "coordinates": [0, 0]}}) is None


def test_ponto_dentro_e_contido():
    r = match_points([POLIGONO], [ponto(0.0005, 0.0005)])
    assert r == {0: (0, "contido", 0.0)}


def test_ponto_perto_da_borda_usa_tolerancia():
    r = match_points([POLIGONO], [ponto(LADO + 0.0001, 0.0005)])
    poligono, metodo, dist = r[0]
    assert (poligono, metodo) == (0, "tolerancia")
    assert dist == pytest.approx(0.0001 * 111320, abs=0.01)


def test_ponto_longe_fica_sem_vinculo():
    assert match_points([POLIGONO], [ponto(0.01, 0.01)]) == {}


def test_q_indices_restringe_e_preserva_indice_original():
    pontos = [ponto(0.0005, 0.0005), ponto(0.0006, 0.0006), ponto(0.0007, 0.0007)]
    r = match_points([POLIGONO], pontos, q_indices=[2])
    assert list(r) == [2]


def test_q_indices_vazio():
    assert match_points([POLIGONO], [ponto(0.0005, 0.0005)], q_indices=[]) == {}


def test_escolhe_o_poligono_certo_entre_varios():
    outro = shape(quadrado(1, 1, 1.001, 1.001))
    r = match_points([POLIGONO, outro], [ponto(1.0005, 1.0005)])
    assert r == {0: (1, "contido", 0.0)}
