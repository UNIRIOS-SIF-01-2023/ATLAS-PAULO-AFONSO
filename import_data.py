#!/usr/bin/env python3
"""Importa edificações (Overture) e endereços (QG/CNEFE) para o MySQL:
uma linha por endereço, mais os prédios sem nenhum endereço."""

import json
import math
import argparse
import os
import re
from collections import Counter

import pymysql

from shapely.geometry import Point, shape, mapping
from shapely.ops import transform
from shapely import strtree

DATA_DIR = "data/raw"
DEFAULT_ROOFTOP_GEOJSON = os.environ.get("ROOFTOP_GEOJSON", "data/processed/pontos_rooftop.geojson")
OVERTURE_FILES = [
    f"{DATA_DIR}/overture/overture-2026-08-19.0-building--38.236,-9.410,-38.212,-9.395.geojson",
    f"{DATA_DIR}/overture/overture-2026-08-19.0-building-municipio.geojson",
]
# 2ª fonte (API aberta Overpass/OSM, sem login): fallback onde o Overture
# não tem polígono. Ausência do arquivo só desliga o fallback.
OSM_BUILDINGS = f"{DATA_DIR}/osm/osm_buildings_paulo_afonso.geojson"
MUNICIPIO_BOUNDARY = f"{DATA_DIR}/municipio_boundary.geojson"
QG_ENDERECOS = f"{DATA_DIR}/qg/qg_810_endereco_Munic2924009.json"
MUNICIPIO = "2924009"  # Paulo Afonso/BA

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "3307"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "ar120604")
DB_NAME = os.environ.get("DB_NAME", "atlas_urbano")
# TLS do banco (ex.: Aiven). Sem DB_SSL_CA, conecta puro (ambiente local).
DB_SSL_CA = os.environ.get("DB_SSL_CA", "")

# ~15 m de tolerância na borda (ex.: porta na fachada, erro de GPS).
POINT_TOLERANCE_DEG = 0.000135

# Raio do círculo usado para endereços sem prédio (~5 m).
POINT_BUFFER_DEG = 0.000045

SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (
    id                  VARCHAR(64)  NOT NULL PRIMARY KEY,
    geom                GEOMETRY     NOT NULL{srid_attr},
    area_m2             DOUBLE       NULL,
    tipo                VARCHAR(32)  NOT NULL DEFAULT 'desconhecido',
    endereco            VARCHAR(255) NULL,
    bairro              VARCHAR(128) NULL,
    estabelecimento     VARCHAR(255) NULL,
    num_pavimentos      INT          NULL,
    fonte               VARCHAR(128) NULL,
    SPATIAL INDEX idx_geom (geom)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# COD_ESPECIE do QG (CNEFE/IBGE) -> tipo de uso. Códigos oficiais:
# 1=domicílio particular, 2=domicílio coletivo, 3=agropecuário,
# 4=ensino, 5=saúde, 6=outras finalidades, 7=em construção,
# 8=religioso. Sem categoria para "em construção": desconhecido.
ESPECIE_TO_TIPO = {
    "1": "residencial",
    "2": "hospedagem",
    "3": "agropecuario",
    "4": "institucional",
    "5": "servico",
    "6": "comercial",
    "7": "desconhecido",
    "8": "religioso",
}

# Fallback para prédios sem ponto QG associado.
OVAERTURE_SUBTYPE_TO_TIPO = {
    "residential": "residencial",
    "commercial": "comercial",
    "religious": "religioso",
    "civic": "institucional",
    "medical": "servico",
    "education": "institucional",
    "transportation": "servico",
    "entertainment": "servico",
    "outbuilding": "desconhecido",
}
OVERTURE_CLASS_TO_TIPO = {
    "house": "residencial",
    "retail": "comercial",
    "church": "religioso",
    "school": "institucional",
    "library": "institucional",
    "post_office": "institucional",
    "hotel": "hospedagem",
    "roof": "desconhecido",
}


def connect_db():
    kwargs = dict(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, charset="utf8mb4"
    )
    if DB_SSL_CA:
        kwargs["ssl"] = {"ca": DB_SSL_CA}
    return pymysql.connect(**kwargs)


def _area_m2(polygon) -> float:
    """Área aproximada em m², projetando localmente ao redor do próprio prédio."""
    cx, cy = polygon.centroid.x, polygon.centroid.y
    kx = 111320.0 * math.cos(math.radians(cy))
    ky = 110540.0

    def _to_meters(lon, lat):
        return (lon - cx) * kx, (lat - cy) * ky

    return transform(_to_meters, polygon).area


def load_buildings():
    """Junta os arquivos Overture (dedupe por id) e recorta no município."""
    merged = {}
    for path in OVERTURE_FILES:
        try:
            with open(path) as f:
                fc = json.load(f)
        except FileNotFoundError:
            print(f"  (aviso: {path} não encontrado, pulando)")
            continue
        for feat in fc["features"]:
            merged.setdefault(feat["id"], feat)
    features = list(merged.values())
    try:
        boundary = boundary_shape()
        from shapely.prepared import prep
        prepared = prep(boundary)
        before = len(features)
        features = [b for b in features
                    if prepared.intersects(shape(b["geometry"]))]
        print(f"  {before} prédios no bbox, {len(features)} dentro do município")
    except FileNotFoundError:
        print("  (aviso: contorno do município ausente, sem recorte)")
    return features


def boundary_shape():
    """Contorno do município (FeatureCollection ou Feature único)."""
    with open(MUNICIPIO_BOUNDARY) as f:
        doc = json.load(f)
    if doc.get("type") == "FeatureCollection":
        doc = doc["features"][0]
    return shape(doc["geometry"])


def load_osm_buildings():
    """2ª fonte: polígonos do OSM (fallback). [] se o arquivo não existir."""
    try:
        with open(OSM_BUILDINGS) as f:
            fc = json.load(f)
    except FileNotFoundError:
        return []
    features = fc["features"]
    try:
        boundary = boundary_shape()
        from shapely.prepared import prep
        prepared = prep(boundary)
        before = len(features)
        features = [b for b in features
                    if prepared.intersects(shape(b["geometry"]))]
        print(f"  OSM: {before} no bbox, {len(features)} dentro do município")
    except FileNotFoundError:
        pass
    return features


def load_qg_points():
    with open(QG_ENDERECOS) as f:
        fc = json.load(f)
    return fc["features"]


def best_type(qg_props, overture_props):
    especie = (qg_props or {}).get("COD_ESPECIE")
    if especie in ESPECIE_TO_TIPO:
        return ESPECIE_TO_TIPO[especie]
    subtype = (overture_props or {}).get("subtype")
    if subtype in OVAERTURE_SUBTYPE_TO_TIPO:
        return OVAERTURE_SUBTYPE_TO_TIPO[subtype]
    cls = (overture_props or {}).get("class")
    if cls in OVERTURE_CLASS_TO_TIPO:
        return OVERTURE_CLASS_TO_TIPO[cls]
    return "desconhecido"


def match_points(polys, points, q_indices=None):
    """Associa pontos a polígonos: contém → tolerância de ~15 m → mais próximo."""
    tree = strtree.STRtree(polys)
    if q_indices is None:
        q_indices = range(len(points))
    point_to_poly = {}
    for qi in q_indices:
        x, y = points[qi]["geometry"]["coordinates"]
        pt = Point(x, y)
        hits = tree.query(pt, predicate="contains")
        if len(hits) == 0:
            hits = tree.query(pt.buffer(POINT_TOLERANCE_DEG), predicate="intersects")
        if len(hits) == 0:
            continue
        cands = [int(i) for i in hits]
        best = min(cands, key=lambda bi: pt.distance(polys[bi]))
        point_to_poly[qi] = best
    return point_to_poly


def cnefe_address_key(props: dict) -> tuple[str, str]:
    """Retorna (id_unico, chave_composta) para correspondência com o rooftop."""
    uid = str(props.get("COD_UNICO_ENDERECO") or "").strip()
    ln = str(props.get("LOGRAD_NUM") or "").strip()
    num_str = str(props.get("NUM_ENDERECO") or "").strip()
    if not num_str or num_str in ("nan", "None"):
        m = re.search(r",\s*([0-9]+)\s*$", ln)
        num_str = m.group(1) if m else ""
    try:
        num_i = int(float(num_str)) if num_str else 0
    except (ValueError, TypeError):
        num_i = 0
    mod = str(props.get("DSC_MODIFICADOR") or "").strip()
    sn = (num_i == 0) or ("SN" in mod.upper())
    if sn:
        return uid, uid
    logr = str(props.get("NOM_SEGLOGR") or "").strip()
    if not logr or logr in ("nan", "None"):
        logr = ln.rsplit(",", 1)[0].strip() if "," in ln else ln
    quadra_key = f"{props.get('COD_SETOR')}_{props.get('NUM_QUADRA')}"
    face_key = f"{quadra_key}_{props.get('NUM_FACE')}"
    return uid, f"{face_key}|{logr}|{num_i}|{mod}"


def load_rooftop_matches(path: str = DEFAULT_ROOFTOP_GEOJSON) -> dict[str, dict]:
    """Carrega correspondências do cnefe_rooftop (addr_key -> propriedades)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            fc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    matches = {}
    for feat in fc.get("features", []):
        props = feat.get("properties") or {}
        key = str(props.get("addr_key") or feat.get("id") or "")
        if key:
            matches[key] = props
    return matches


def relate_points_via_rooftop(building_features, osm_features, qg_features, rooftop_matches):
    """Associa endereços a edificações usando o matching refinado do rooftop."""
    ov_id_to_idx = {b.get("id"): bi for bi, b in enumerate(building_features) if b.get("id")}
    osm_id_to_idx = {}
    for oi, b in enumerate(osm_features):
        otags = b.get("properties") or {}
        oid = str(otags.get("osm_id", oi)).replace("/", "-")
        osm_id_to_idx[oid] = oi
        osm_id_to_idx[f"osm-{oid}"] = oi

    point_to_building = {}
    osm_match = {}

    for qi, qf in enumerate(qg_features):
        qp = qf.get("properties") or {}
        uid, comp_key = cnefe_address_key(qp)
        match = rooftop_matches.get(uid) or rooftop_matches.get(comp_key)
        if not match:
            continue
        bld_id = match.get("bld_id")
        if not bld_id:
            continue
        if bld_id in ov_id_to_idx:
            point_to_building[qi] = ov_id_to_idx[bld_id]
        elif bld_id in osm_id_to_idx:
            osm_match[qi] = osm_id_to_idx[bld_id]

    return point_to_building, osm_match


def relate_points_to_buildings(buildings, points):
    polys = []
    for b in buildings:
        g = shape(b["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        polys.append(g)

    point_to_building = match_points(polys, points)
    building_to_points = {}
    for qi, bi in point_to_building.items():
        building_to_points.setdefault(bi, []).append(qi)
    return building_to_points, polys


def main(if_empty=False):
    if if_empty:
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'buildings'")
            if cur.fetchone():
                cur.execute("SELECT COUNT(*) FROM buildings")
                if cur.fetchone()[0] > 0:
                    print("Dados já importados; pulando carga. Use --reimport para atualizar.")
                    return

    print("Lendo edificações do Overture...")
    building_features = load_buildings()
    print(f"  {len(building_features)} prédios")

    print("Lendo endereços do QG...")
    qg_features = load_qg_points()
    print(f"  {len(qg_features)} pontos de endereço")

    polys = []
    for b in building_features:
        g = shape(b["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        polys.append(g)

    osm_features = load_osm_buildings()
    osm_polys = []
    for b in osm_features:
        g = shape(b["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        osm_polys.append(g)

    print("Carregando matching CNEFE rooftop...")
    rooftop_matches = load_rooftop_matches()
    if rooftop_matches:
        print(f"  {len(rooftop_matches)} correspondências rooftop carregadas")
        point_to_building, osm_match = relate_points_via_rooftop(
            building_features, osm_features, qg_features, rooftop_matches
        )
        print(f"  Rooftop: {len(point_to_building)} endereços em Overture, {len(osm_match)} em OSM")
        # Fallback geométrico apenas para endereços residuais não cobertos pelo rooftop
        unmatched = [qi for qi in range(len(qg_features))
                     if qi not in point_to_building and qi not in osm_match]
        if unmatched:
            extra_ov = match_points(polys, qg_features, unmatched)
            point_to_building.update(extra_ov)
            unmatched_osm = [qi for qi in unmatched if qi not in point_to_building]
            if unmatched_osm and osm_polys:
                extra_osm = match_points(osm_polys, qg_features, unmatched_osm)
                osm_match.update(extra_osm)
    else:
        print("  (Aviso: rooftop não encontrado; usando matching geométrico)")
        point_to_building = match_points(polys, qg_features)
        unmatched = [qi for qi in range(len(qg_features)) if qi not in point_to_building]
        osm_match = match_points(osm_polys, qg_features, unmatched) if osm_polys else {}

    building_to_points = {}
    for qi, bi in point_to_building.items():
        building_to_points.setdefault(bi, []).append(qi)
    print(f"  Total de {len(point_to_building)} endereços em prédios Overture e {len(osm_match)} em OSM")

    print("Gravando no MySQL...")
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            # MySQL 8 só usa o SPATIAL INDEX se a coluna fixar o SRID, e o
            # ST_GeomFromGeoJSON dele assume 4326. O MariaDB já grava SRID 0
            # e não aceita esse atributo.
            cur.execute("SELECT VERSION()")
            is_mysql = "mariadb" not in cur.fetchone()[0].lower()
            cur.execute("DROP TABLE IF EXISTS buildings")
            cur.execute(SCHEMA.format(srid_attr=" SRID 0" if is_mysql else ""))
            geom_sql = ("ST_GeomFromGeoJSON(%s, 1, 0)" if is_mysql
                        else "ST_GeomFromGeoJSON(%s)")

            codes = [str(q["properties"].get("COD_UNICO_ENDERECO")) for q in qg_features]
            dupes = {c for c, n in Counter(codes).items() if n > 1}
            seen = Counter()

            def row_id(code):
                if code not in dupes:
                    return code
                seen[code] += 1
                return f"{code}#{seen[code]}"

            def fonte_of(props):
                sources = props.get("sources") or []
                return "; ".join(dict(s).get("dataset", "?") for s in sources) or None

            batch = []
            area_cache = {}

            def cached_area(kind, index, geom):
                key = (kind, index)
                if key not in area_cache:
                    area_cache[key] = _area_m2(geom)
                return area_cache[key]

            for qi, qf in enumerate(qg_features):
                qp = qf["properties"]
                bi = point_to_building.get(qi)
                if bi is not None:
                    oprops = building_features[bi]["properties"]
                    geom = polys[bi]
                    num_pav = oprops.get("num_floors")
                    fonte = fonte_of(oprops)
                    area = cached_area("overture", bi, geom)
                elif qi in osm_match:
                    oi = osm_match[qi]
                    otags = osm_features[oi]["properties"] or {}
                    oprops = {"class": otags.get("building")}
                    geom = osm_polys[oi]
                    num_pav = None
                    fonte = "OpenStreetMap"
                    area = cached_area("osm", oi, geom)
                else:
                    oprops = {}
                    x, y = qf["geometry"]["coordinates"]
                    geom = Point(x, y).buffer(POINT_BUFFER_DEG)
                    num_pav = None
                    fonte = "QG/CNEFE"
                    area = _area_m2(geom)
                batch.append((
                    row_id(codes[qi]), json.dumps(mapping(geom)), area,
                    best_type(qp, oprops),
                    qp.get("LOGRAD_NUM") or None, qp.get("DSC_LOCALIDADE") or None,
                    qp.get("DSC_ESTABELECIMENTO") or None, num_pav, fonte,
                ))

            for bi, b in enumerate(building_features):
                if bi in building_to_points:
                    continue
                props = b["properties"]
                geom = polys[bi]
                batch.append((
                    b["id"], json.dumps(mapping(geom)),
                    cached_area("overture", bi, geom),
                    best_type(None, props),
                    None, None, None, props.get("num_floors"), fonte_of(props),
                ))

            osm_to_points = {}
            for qi, oi in osm_match.items():
                osm_to_points.setdefault(oi, []).append(qi)
            for oi, o in enumerate(osm_features):
                if oi in osm_to_points:
                    continue
                otags = o["properties"] or {}
                oid = str(otags.get("osm_id", oi)).replace("/", "-")
                batch.append((
                    f"osm-{oid}", json.dumps(mapping(osm_polys[oi])),
                    cached_area("osm", oi, osm_polys[oi]),
                    best_type(None, {"class": otags.get("building")}),
                    None, None, None, None, "OpenStreetMap",
                ))

            # SRID 0 em toda a tabela (tudo em lon/lat).
            sql = (
                "INSERT INTO buildings "
                "(id, geom, area_m2, tipo, endereco, bairro, estabelecimento, num_pavimentos, fonte) "
                f"VALUES (%s, {geom_sql}, %s, %s, %s, %s, %s, %s, %s)"
            )
            CHUNK = 500
            for i in range(0, len(batch), CHUNK):
                cur.executemany(sql, batch[i:i + CHUNK])
            conn.commit()
            cur.execute("SELECT COUNT(*), SUM(tipo='desconhecido') FROM buildings")
            total, unknown = cur.fetchone()
            print(f"  Inseridos {total} prédios em `buildings` "
                  f"({unknown} sem tipo identificado = desconhecido)")
    finally:
        conn.close()
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-empty", action="store_true")
    main(if_empty=parser.parse_args().if_empty)
