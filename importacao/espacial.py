"""Operações geométricas: validar polígonos e associar pontos a polígonos."""

import numpy as np
import shapely
from shapely import strtree
from shapely.geometry import shape

from . import config


def polygon_of(feat):
    """Polígono válido do GeoJSON, ou None (o banco só aceita (MULTI)POLYGON)."""
    g = shape(feat["geometry"])
    if not g.is_valid:
        g = g.buffer(0)
    return g if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty else None


def _contidos(arvore, pts):
    """Pontos dentro de algum polígono: {posição do ponto: (polígono, "contido", 0.0)}.

    Vetorizado (GEOS em C). predicate testa ponto→polígono: "within", não
    "contains". Se vários polígonos contêm o ponto, vale o primeiro devolvido.
    """
    pi, gi = arvore.query(pts, predicate="within")
    posicoes, primeiro = np.unique(pi, return_index=True)
    return {int(p): (int(gi[k]), "contido", 0.0) for p, k in zip(posicoes, primeiro)}


def _por_tolerancia(arvore, pts, posicoes):
    """Pontos a até ~15 m de um polígono: {posição: (polígono, "tolerancia", metros)}."""
    if len(posicoes) == 0:
        return {}
    (ri, gi), dist = arvore.query_nearest(
        pts[posicoes], max_distance=config.POINT_TOLERANCE_DEG,
        return_distance=True, all_matches=False)
    return {int(posicoes[r]): (int(g), "tolerancia",
                               round(float(d) * config.METROS_POR_GRAU, 2))
            for r, g, d in zip(ri, gi, dist)}


def match_points(polys, points, q_indices=None):
    """Associa pontos a polígonos: contém → tolerância de ~15 m → mais próximo.

    Retorna {índice do ponto: (índice do polígono, METODO, DISTANCIA_M)}.
    """
    q_indices = (np.arange(len(points)) if q_indices is None
                 else np.asarray(q_indices, dtype=int))
    if len(q_indices) == 0:
        return {}
    arvore = strtree.STRtree(polys)
    pts = shapely.points([points[qi]["geometry"]["coordinates"][:2] for qi in q_indices])

    contidos = _contidos(arvore, pts)
    restantes = np.setdiff1d(np.arange(len(pts)), np.array(list(contidos), dtype=int))
    proximos = _por_tolerancia(arvore, pts, restantes)

    return {int(q_indices[pos]): vinculo
            for pos, vinculo in {**contidos, **proximos}.items()}
