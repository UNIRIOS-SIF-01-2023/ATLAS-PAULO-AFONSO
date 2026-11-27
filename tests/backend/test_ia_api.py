"""Rotas do classificador de uso com um modelo falso (sem banco)."""

import math

import joblib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from backend import main
from backend.ia import modelo as ia_modelo
from backend.ia.dados import Base
from backend.ia.features import NOMES, pontos_representativos

M_POR_GRAU = 111320.0
ID_RESIDENCIAL, ID_DESCONHECIDO = 1, 2


class ModeloFalso:
    classes_ = np.array(["comercial", "residencial"])

    def predict_proba(self, X):
        assert X.shape[1] == len(NOMES)
        return np.tile([0.3, 0.7], (len(X), 1))


def _base():
    cos = math.cos(math.radians(-9.40))
    geoms = np.array([box(-38.22 + k * 30 / (M_POR_GRAU * cos), -9.40,
                          -38.22 + (k * 30 + 10) / (M_POR_GRAU * cos), -9.40 + 10 / M_POR_GRAU)
                      for k in range(3)], dtype=object)
    return Base(ids=np.array([1, 2, 3]), geoms=geoms, pontos=pontos_representativos(geoms),
                tipos=["residencial", "desconhecido", "desconhecido"],
                origens=["cnefe", "sem_dado", "sem_dado"], fontes=["OpenStreetMap"] * 3,
                bairros=[None] * 3)


@pytest.fixture()
def leituras():
    return []


@pytest.fixture()
def cliente(monkeypatch, leituras):
    def ler():
        leituras.append(1)
        return _base()

    monkeypatch.setattr(main, "_ler_base_ia", ler)
    monkeypatch.setattr(main, "current_version", lambda: "v1")
    monkeypatch.setattr(ia_modelo, "estado", ia_modelo.Estado())
    ia_modelo.estado.payload = {"modelo": ModeloFalso(), "nomes_features": list(NOMES), "versao": "uso-teste"}
    main.app.dependency_overrides[main.auth.get_current_user] = lambda: {"id": 1}
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_carregar_recusa_arquivo_ausente_corrompido_ou_de_outras_features(tmp_path):
    assert ia_modelo.carregar(tmp_path / "nao_existe.joblib") is None
    ruim = tmp_path / "ruim.joblib"
    ruim.write_bytes(b"lixo")
    assert ia_modelo.carregar(ruim) is None
    outro = tmp_path / "outro.joblib"
    joblib.dump({"modelo": None, "nomes_features": ["area_m2"], "versao": "x"}, outro)
    assert ia_modelo.carregar(outro) is None


def test_modelo_versionado_carrega():
    payload = ia_modelo.carregar()
    assert payload is not None and payload["nomes_features"] == NOMES


@pytest.mark.parametrize("body", [{"id": "abc"}, {"id": ""}, {"id": "-1"}, {}])
def test_predict_uso_valida_entrada(cliente, body):
    assert cliente.post("/api/predict-uso", json=body).status_code == 422


def test_predict_uso_404(cliente):
    r = cliente.post("/api/predict-uso", json={"id": "999"})
    assert r.status_code == 404 and r.json()["detail"] == "Prédio não encontrado."


def test_predict_uso_desconhecido_traz_classe_confianca_e_probabilidades(cliente):
    r = cliente.post("/api/predict-uso", json={"id": str(ID_DESCONHECIDO)})
    assert r.status_code == 200
    j = r.json()
    assert j["tipo_oficial"] == "desconhecido" and j["tipo_previsto"] == "residencial"
    assert j["confianca"] == pytest.approx(0.7)
    assert sum(j["probabilidades"].values()) == pytest.approx(1)
    assert j["versao_modelo"] == "uso-teste"


def test_predict_uso_predio_oficial_nao_recebe_previsao(cliente):
    j = cliente.post("/api/predict-uso", json={"id": str(ID_RESIDENCIAL)}).json()
    assert j["tipo_oficial"] == "residencial" and j["tipo_previsto"] is None and j["probabilidades"] == {}


def test_previsoes_calculadas_uma_vez_por_versao(cliente, leituras, monkeypatch):
    for _ in range(3):
        cliente.post("/api/predict-uso", json={"id": "2"})
    assert len(leituras) == 1
    monkeypatch.setattr(main, "current_version", lambda: "v2")
    cliente.post("/api/predict-uso", json={"id": "2"})
    assert len(leituras) == 2


def test_rotas_503_sem_modelo(cliente):
    ia_modelo.estado.payload = None
    r = cliente.post("/api/predict-uso", json={"id": "2"})
    assert r.status_code == 503 and "indisponível" in r.json()["detail"]
    assert cliente.get("/api/predict-uso/modelo").status_code == 503


def test_sobre_o_modelo_traz_metricas(cliente):
    j = cliente.get("/api/predict-uso/modelo").json()
    assert "modelo_final" in j["metricas"] and "mais_frequente" in j["metricas"]["modelos"]


def test_rotas_de_ia_exigem_login():
    main.app.dependency_overrides.clear()
    cliente = TestClient(main.app)
    assert cliente.post("/api/predict-uso", json={"id": "2"}).status_code == 401
    assert cliente.get("/api/predict-uso/modelo").status_code == 401
