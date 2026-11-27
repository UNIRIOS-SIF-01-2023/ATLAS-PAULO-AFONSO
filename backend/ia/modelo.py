"""Modelo de uso na API: carrega o joblib uma vez e prevê os prédios desconhecidos.

As features dependem da vizinhança de cada prédio, então não dá para calcular
um prédio isolado por pedido (ele viraria um prédio sem vizinhos). A API monta
as features da base inteira uma vez, como no treino, e guarda as previsões por id.
"""

import json
import logging
from pathlib import Path
from threading import Lock

import joblib
import numpy as np

from .dados import Base
from .features import NOMES, mascara_previsao, mascara_treino, montar_features

logger = logging.getLogger(__name__)

PASTA = Path(__file__).resolve().parents[2] / "ai-model" / "models"
CAMINHO_MODELO = PASTA / "modelo_uso.joblib"
CAMINHO_INFO = PASTA / "modelo_uso.json"


def carregar(caminho: Path = CAMINHO_MODELO) -> dict | None:
    """Payload do treino (ai-model/scripts/treinar_uso.py), ou None se faltar ou não servir."""
    try:
        payload = joblib.load(caminho)
    except Exception:
        logger.warning("modelo de uso indisponível em %s", caminho, exc_info=True)
        return None
    if payload.get("nomes_features") != NOMES:
        # Features de treino diferentes das de agora dariam previsões erradas sem erro.
        logger.error("modelo %s treinado com outras features; retreine", payload.get("versao"))
        return None
    return payload


def carregar_info(caminho: Path = CAMINHO_INFO) -> dict | None:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def prever_base(payload: dict, base: Base) -> dict:
    """{"tipos": {id: tipo oficial}, "previsoes": {id: previsão}} para a base inteira."""
    X, _ = montar_features(base.geoms, base.fontes, base.tipos, mascara_treino(base.tipos, base.origens))
    alvo = np.flatnonzero(mascara_previsao(base.tipos))
    previsoes = {}
    if len(alvo):
        modelo = payload["modelo"]
        classes = [str(c) for c in modelo.classes_]
        proba = modelo.predict_proba(X[alvo])
        for linha, p in zip(alvo, proba):
            k = int(p.argmax())
            previsoes[int(base.ids[linha])] = {
                "tipo_previsto": classes[k],
                "confianca": round(float(p[k]), 4),
                "probabilidades": {c: round(float(v), 4) for c, v in zip(classes, p)},
            }
    return {"tipos": {int(i): t for i, t in zip(base.ids, base.tipos)}, "previsoes": previsoes}


class Estado:
    """Modelo carregado e previsões calculadas uma vez por versão dos dados."""

    def __init__(self):
        self.payload: dict | None = None
        self._lock = Lock()
        self._memo: dict = {}

    def versao_modelo(self) -> str:
        return self.payload["versao"] if self.payload else "sem-modelo"

    def previsoes(self, versao_dados: str, ler_base) -> dict:
        """`ler_base()` devolve a Base; só é chamado quando a versão muda."""
        with self._lock:
            if self._memo.get("versao") != versao_dados:
                self._memo = {"versao": versao_dados, **prever_base(self.payload, ler_base())}
            return self._memo


estado = Estado()
