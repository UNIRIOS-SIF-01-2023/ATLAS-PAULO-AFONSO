"""Linhas do banco -> arrays que o classificador usa (treino e API)."""

from dataclasses import dataclass

import numpy as np
import shapely

from .features import pontos_representativos


@dataclass
class Base:
    ids: np.ndarray        # int
    geoms: np.ndarray      # polígonos shapely em lon/lat
    pontos: np.ndarray     # [n, 2] lon/lat, um ponto dentro de cada prédio
    tipos: list
    origens: list
    fontes: list
    bairros: list          # str ou None


def de_linhas(rows: list[dict]) -> Base:
    """Saída de db.fetch_buildings_for_model() -> Base."""
    # O MariaDB devolve o WKB como foi gravado: x = lon, y = lat.
    geoms = np.asarray(shapely.from_wkb([r["wkb"] for r in rows]), dtype=object)
    return Base(
        ids=np.array([int(r["id"]) for r in rows], dtype=np.int64),
        geoms=geoms,
        pontos=pontos_representativos(geoms) if len(rows) else np.zeros((0, 2)),
        tipos=[r["tipo"] or "desconhecido" for r in rows],
        origens=[r["origem"] or "" for r in rows],
        fontes=[r["fonte"] or "" for r in rows],
        bairros=[r["bairro"] or None for r in rows],
    )
