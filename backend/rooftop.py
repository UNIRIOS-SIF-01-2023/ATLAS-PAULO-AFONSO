"""Camadas locais do CNEFE rooftop, sem API externa.

O `cnefe_rooftop.py` gera dois GeoJSON 4326:

- pontos: um por endereço, já dentro do prédio atribuído;
- polígonos: um por prédio, com o resumo dos endereços que caíram nele.

Esta camada serve os arquivos para o mapa. Os caminhos podem ser trocados por
`ROOFTOP_GEOJSON` (pontos) e `ROOFTOP_POLYGONS` (polígonos).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock

from shapely.geometry import shape

RAIZ = Path(__file__).resolve().parent.parent
DEFAULT_PATH = RAIZ / "data" / "processed" / "pontos_rooftop.geojson"
DEFAULT_POLYGONS_PATH = RAIZ / "data" / "processed" / "pontos_rooftop_poligonos.geojson"
EMPTY_PONTOS = {"type": "FeatureCollection", "name": "cnefe_rooftop", "features": []}
EMPTY_POLIGONOS = {"type": "FeatureCollection", "name": "cnefe_rooftop_poligonos", "features": []}

_lock = Lock()
_cache_pontos: dict = {"path": None, "mtime": None, "doc": None}
_cache_poligonos: dict = {"path": None, "mtime": None, "doc": None}


def caminho() -> Path:
    """Arquivo de pontos configurado por `ROOFTOP_GEOJSON`."""
    return Path(os.environ.get("ROOFTOP_GEOJSON", DEFAULT_PATH))


def caminho_poligonos() -> Path:
    """Arquivo de polígonos configurado por `ROOFTOP_POLYGONS`."""
    return Path(os.environ.get("ROOFTOP_POLYGONS", DEFAULT_POLYGONS_PATH))


def _ler(path: Path, cache: dict, vazio: dict) -> dict:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return dict(vazio)
    with _lock:
        if cache["path"] != str(path) or cache["mtime"] != mtime:
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                doc = dict(vazio)
            cache.update(path=str(path), mtime=mtime, doc=doc)
        return cache["doc"]


def geojson() -> dict:
    """FeatureCollection dos pontos finais (vazia se ainda não gerada)."""
    return _ler(caminho(), _cache_pontos, EMPTY_PONTOS)


def poligonos() -> dict:
    """FeatureCollection dos prédios que receberam pontos."""
    return _ler(caminho_poligonos(), _cache_poligonos, EMPTY_POLIGONOS)


def _bounds(doc: dict) -> list[float] | None:
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for feature in doc.get("features") or []:
        geom = feature.get("geometry")
        if not geom:
            continue
        try:
            left, bottom, right, top = shape(geom).bounds
        except Exception:
            continue
        minx, miny = min(minx, left), min(miny, bottom)
        maxx, maxy = max(maxx, right), max(maxy, top)
    if minx == float("inf"):
        return None
    return [minx, miny, maxx, maxy]


def map_meta() -> dict:
    """Extent, contagens e estado dos arquivos locais; não consulta a rede."""
    doc = geojson()
    features = doc.get("features") or []
    poly_doc = poligonos()
    poly_features = poly_doc.get("features") or []

    def conta(pred) -> int:
        return sum(1 for f in features if pred(f.get("properties") or {}))

    path = caminho()
    polys_path = caminho_poligonos()
    try:
        mtime = path.stat().st_mtime
        version = f"rooftop-{int(mtime)}"
        updated_at = path.stat().st_mtime
    except OSError:
        version = "rooftop-vazio"
        updated_at = None

    bld_ids = {
        f.get("properties", {}).get("bld_id")
        for f in features
        if f.get("properties", {}).get("bld_id")
    }
    total_addr = len(features)
    high_count = conta(lambda p: p.get("confianca") == "alta")
    medium_count = conta(lambda p: p.get("confianca") == "media")
    low_count = conta(lambda p: p.get("confianca") == "baixa")
    poly_total = len(poly_features) or len(bld_ids)
    coverage_pct = round((len(bld_ids) / max(1, poly_total)) * 100, 1)

    return {
        "version": version,
        "bounds": _bounds(doc),
        "source": "CNEFE rooftop (local)",
        "file": str(path),
        "missing": not features,
        "address_count": total_addr,
        "building_count": len(bld_ids),
        "coverage_pct": coverage_pct,
        "changed_count": conta(lambda p: bool(p.get("mudou_bld") or p.get("mudou_ponto"))),
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        "high_pct": round((high_count / max(1, total_addr)) * 100, 1),
        "medium_pct": round((medium_count / max(1, total_addr)) * 100, 1),
        "low_pct": round((low_count / max(1, total_addr)) * 100, 1),
        "compartilhado_count": conta(lambda p: bool(p.get("compartilhado"))),
        "anexo_count": sum(1 for f in poly_features if (f.get("properties") or {}).get("tipo_vinculo") == "anexo"),
        "revise_face_count": conta(lambda p: bool(p.get("revisar_face"))),
        "polygon_count": len(bld_ids),
        "polygon_file": str(polys_path),
        "polygon_missing": not polys_path.exists(),
        "updated_at": updated_at,
    }
