"""Leitura dos arquivos GeoJSON de entrada (Overture, OSM, CNEFE/QG, contorno)."""

import json

from shapely.geometry import shape
from shapely.prepared import prep

from . import config


def _ler_geojson(caminho):
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def boundary_shape():
    """Contorno do município (FeatureCollection ou Feature único)."""
    doc = _ler_geojson(config.MUNICIPIO_BOUNDARY)
    if doc.get("type") == "FeatureCollection":
        doc = doc["features"][0]
    return shape(doc["geometry"])


def recortar_no_municipio(features, rotulo):
    """Mantém só as features que tocam o município. Sem contorno, não recorta."""
    try:
        contorno = prep(boundary_shape())
    except FileNotFoundError:
        print(f"  (aviso: contorno do município ausente, {rotulo} sem recorte)")
        return features
    antes = len(features)
    dentro = [f for f in features if contorno.intersects(shape(f["geometry"]))]
    print(f"  {rotulo}: {antes} no bbox, {len(dentro)} dentro do município")
    return dentro


def load_buildings():
    """Junta os arquivos Overture (dedupe por id) e recorta no município."""
    unicos = {}
    for caminho in config.OVERTURE_FILES:
        try:
            fc = _ler_geojson(caminho)
        except FileNotFoundError:
            print(f"  (aviso: {caminho} não encontrado, pulando)")
            continue
        for feat in fc["features"]:
            unicos.setdefault(feat["id"], feat)
    return recortar_no_municipio(list(unicos.values()), "Overture")


def load_osm_buildings():
    """2ª fonte: polígonos do OSM (fallback). [] se o arquivo não existir."""
    try:
        fc = _ler_geojson(config.OSM_BUILDINGS)
    except FileNotFoundError:
        return []
    return recortar_no_municipio(fc["features"], "OSM")


def load_qg_points():
    return _ler_geojson(config.QG_ENDERECOS)["features"]
