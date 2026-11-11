"""Camadas locais do CNEFE rooftop, sem API externa.

O `cnefe_rooftop.py` gera dois GeoJSON 4326:

- pontos: um por endereço, já dentro do prédio atribuído;
- polígonos: um por prédio, com o resumo dos endereços que caíram nele.

Esta camada serve os arquivos para o mapa. Os caminhos podem ser trocados por
`ROOFTOP_GEOJSON` (pontos) e `ROOFTOP_POLYGONS` (polígonos).
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from threading import Lock

from shapely.geometry import shape

RAIZ = Path(__file__).resolve().parent.parent
DEFAULT_PATH = RAIZ / "data" / "processed" / "pontos_rooftop.geojson"
DEFAULT_POLYGONS_PATH = RAIZ / "data" / "processed" / "pontos_rooftop_poligonos.geojson"
DEFAULT_CNEFE_PATH = RAIZ / "data" / "processed" / "pontos_rooftop_debug" / "cnefe_original.csv"
EMPTY_PONTOS = {"type": "FeatureCollection", "name": "cnefe_rooftop", "features": []}
EMPTY_POLIGONOS = {"type": "FeatureCollection", "name": "cnefe_rooftop_poligonos", "features": []}
EMPTY_CNEFE = {"type": "FeatureCollection", "name": "cnefe_original", "features": []}

_lock = Lock()
_cache_pontos: dict = {"path": None, "mtime": None, "doc": None, "feature_bounds": None}
_cache_poligonos: dict = {"path": None, "mtime": None, "doc": None, "feature_bounds": None}
_cache_cnefe: dict = {"path": None, "mtime": None, "doc": None, "feature_bounds": None}


def caminho() -> Path:
    """Arquivo de pontos configurado por `ROOFTOP_GEOJSON`."""
    return Path(os.environ.get("ROOFTOP_GEOJSON", DEFAULT_PATH))


def caminho_poligonos() -> Path:
    """Arquivo de polígonos configurado por `ROOFTOP_POLYGONS`."""
    return Path(os.environ.get("ROOFTOP_POLYGONS", DEFAULT_POLYGONS_PATH))


def caminho_cnefe() -> Path:
    """CSV bruto do CNEFE configurado por `ROOFTOP_CNEFE`."""
    return Path(os.environ.get("ROOFTOP_CNEFE", DEFAULT_CNEFE_PATH))


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
            cache.update(path=str(path), mtime=mtime, doc=doc, feature_bounds=None)
        return cache["doc"]


def _caixa(bbox: str) -> tuple[float, float, float, float]:
    """`minLng,minLat,maxLng,maxLat` → tupla validada (ValueError se inválida)."""
    partes = [float(parte) for parte in bbox.split(",")]
    if len(partes) != 4:
        raise ValueError("bbox precisa de 4 valores")
    oeste, sul, leste, norte = partes
    if not (oeste < leste and sul < norte):
        raise ValueError("bbox invertida")
    return (oeste, sul, leste, norte)


def _features_com_bounds(doc: dict, cache: dict):
    """Pares (feature, bounds|None); os bounds ficam no cache até o arquivo mudar.

    Filtrar por bbox per request sem isso exigiria reconstruir 76k geometrias
    shapely a cada chamada.
    """
    features = doc.get("features") or []
    bounds = cache.get("feature_bounds")
    if bounds is None or len(bounds) != len(features):
        bounds = []
        for feature in features:
            geom = feature.get("geometry")
            try:
                bounds.append(shape(geom).bounds if geom else None)
            except Exception:
                bounds.append(None)
        cache["feature_bounds"] = bounds
    return zip(features, bounds)


def _selecionar(doc: dict, cache: dict, caixa: tuple[float, float, float, float]) -> dict:
    """Só as features cujo bounds cruza a caixa (oeste, sul, leste, norte)."""
    oeste, sul, leste, norte = caixa
    features = [
        feature
        for feature, bounds in _features_com_bounds(doc, cache)
        if bounds is not None
        and bounds[0] <= leste and bounds[2] >= oeste
        and bounds[1] <= norte and bounds[3] >= sul
    ]
    return {**doc, "features": features}


def geojson(bbox: str | None = None) -> dict:
    """FeatureCollection dos pontos finais (vazia se ainda não gerada).

    Com `bbox`, só os pontos dentro da área pedida — o modo debug do mapa
    pede a área visível a cada navegação, em vez dos 22MB de uma vez.
    """
    doc = _ler(caminho(), _cache_pontos, EMPTY_PONTOS)
    if not bbox:
        return doc
    return _selecionar(doc, _cache_pontos, _caixa(bbox))


def poligonos(bbox: str | None = None) -> dict:
    """FeatureCollection dos prédios que receberam pontos (com `bbox`, filtrada)."""
    doc = _ler(caminho_poligonos(), _cache_poligonos, EMPTY_POLIGONOS)
    if not bbox:
        return doc
    return _selecionar(doc, _cache_poligonos, _caixa(bbox))


def _extent(doc: dict, cache: dict) -> list[float] | None:
    boxes = [bounds for _, bounds in _features_com_bounds(doc, cache) if bounds]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


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
        "bounds": _extent(doc, _cache_pontos),
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


def _cnefe_doc(path: Path, cache: dict) -> dict:
    """CSV bruto do CNEFE → FeatureCollection (uma vez por versão do arquivo)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return dict(EMPTY_CNEFE)
    with _lock:
        if cache["path"] != str(path) or cache["mtime"] != mtime:
            features = []
            try:
                with path.open(encoding="utf-8-sig", newline="") as file:
                    for row in csv.DictReader(file, delimiter=";"):
                        try:
                            lon = float(row["lon"])
                            lat = float(row["lat"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        numero = (row.get("NUM_ENDERECO") or "").strip()
                        rua = (row.get("NOM_SEGLOGR") or "").strip()
                        features.append({
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [lon, lat]},
                            "properties": {
                                "addr_key": (row.get("COD_UNICO_ENDERECO") or "").strip(),
                                "endereco": f"{rua}, {numero or 'S/N'}",
                                "cep": (row.get("CEP") or "").strip(),
                                "localidade": (row.get("DSC_LOCALIDADE") or "").strip(),
                                "especie": (row.get("COD_ESPECIE") or "").strip(),
                            },
                        })
            except OSError:
                features = []
            doc = {"type": "FeatureCollection", "name": "cnefe_original", "features": features}
            cache.update(path=str(path), mtime=mtime, doc=doc, feature_bounds=None)
        return cache["doc"]


def cnefe_geojson(bbox: str | None = None) -> dict:
    """Pontos ORIGINAIS do CNEFE, antes do rooftop, para comparar no mapa.

    Com `bbox`, só os endereços da área pedida (o modo debug carrega por
    viewport, igual às camadas geradas).
    """
    doc = _cnefe_doc(caminho_cnefe(), _cache_cnefe)
    if not bbox:
        return doc
    return _selecionar(doc, _cache_cnefe, _caixa(bbox))
