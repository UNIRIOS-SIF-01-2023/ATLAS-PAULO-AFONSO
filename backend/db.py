"""Conexão com o MySQL e consultas usadas pela API."""

import json
import queue
from concurrent.futures import CancelledError
from contextlib import contextmanager

import pymysql
import shapely

from .config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, DB_SSL_CA, SRID

FROM_EDIFICACOES = (
    "FROM EDIFICACOES E JOIN FONTES F ON F.ID = E.FONTE_ID "
    "LEFT JOIN BAIRROS B ON B.ID = E.BAIRRO_ID"
)

# Endereço/estabelecimento exibidos: o primeiro endereço associado ao prédio.
_DO_PRIMEIRO_ENDERECO = (
    "(SELECT EN.{col} FROM ENDERECO_EDIFICACAO EE "
    "JOIN ENDERECOS EN ON EN.ID = EE.ENDERECO_ID "
    "WHERE EE.EDIFICACAO_ID = E.ID AND EN.{col} IS NOT NULL "
    "ORDER BY EN.ID LIMIT 1)"
)

# Identifica o footprint. Cada EDIFICACAO já é única (medido: 55.255 linhas,
# 55.255 geometrias distintas), então o ID basta e evita SHA1 por linha.
GEOMETRY_KEY = "CAST(E.ID AS CHAR) AS geometry_key"

SELECT_FIELDS = (
    f"CAST(E.ID AS CHAR) AS id, ST_AsGeoJSON(E.GEOM) AS geojson, {GEOMETRY_KEY}, "
    "CAST(E.AREA_M2 AS DOUBLE) AS area_m2, E.TIPO_USO AS tipo, "
    f"{_DO_PRIMEIRO_ENDERECO.format(col='LOGRADOURO_NUM')} AS endereco, "
    "B.NOME AS bairro, "
    f"{_DO_PRIMEIRO_ENDERECO.format(col='ESTABELECIMENTO')} AS estabelecimento, "
    "E.NUM_PAVIMENTOS AS num_pavimentos, F.NOME AS fonte"
)

SELECT_LIGHT_FIELDS = (
    f"CAST(E.ID AS CHAR) AS id, ST_AsGeoJSON(E.GEOM) AS geojson, {GEOMETRY_KEY}, "
    "E.TIPO_USO AS tipo, F.NOME AS fonte, E.NUM_PAVIMENTOS AS num_pavimentos"
)

# WKB é bem mais barato de transferir e decodificar que GeoJSON.
TILE_FIELDS = (
    f"CAST(E.ID AS CHAR) AS id, ST_AsBinary(E.GEOM) AS wkb, {GEOMETRY_KEY}, "
    "E.TIPO_USO AS tipo, F.NOME AS fonte, E.NUM_PAVIMENTOS AS num_pavimentos, "
    "CAST(E.AREA_M2 AS DOUBLE) AS area_m2"
)

MAX_LIMIT = 20000


def parse_bbox(raw):
    """Valida `minLng,minLat,maxLng,maxLat` → tupla de floats.

    Retorna None se raw for None. Levanta ValueError se inválido.
    """
    if raw is None:
        return None
    try:
        parts = [float(x) for x in raw.split(",")]
    except (ValueError, AttributeError):
        raise ValueError(f"bbox inválido: {raw!r}")
    if len(parts) != 4:
        raise ValueError(f"bbox inválido: {raw!r}")
    min_lng, min_lat, max_lng, max_lat = parts
    if not (-180 <= min_lng <= 180 and -180 <= max_lng <= 180):
        raise ValueError(f"bbox inválido: {raw!r}")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ValueError(f"bbox inválido: {raw!r}")
    if not (min_lng < max_lng and min_lat < max_lat):
        raise ValueError(f"bbox inválido: {raw!r}")
    return (min_lng, min_lat, max_lng, max_lat)


def build_where(bbox=None, bairros=None, tipo=None, fonte=None):
    """Monta (cláusula WHERE, params) a partir dos filtros opcionais.

    O filtro espacial usa MBRIntersects para aproveitar o SPATIAL INDEX.
    O WKT vem em lon/lat; o SRID 4674 é lat/lon por padrão, daí o axis-order.
    Sem filtros retorna ("", []).
    """
    clauses = []
    params = []
    if bbox is not None:
        min_lng, min_lat, max_lng, max_lat = bbox
        wkt = (
            f"POLYGON(({min_lng} {min_lat},{max_lng} {min_lat},"
            f"{max_lng} {max_lat},{min_lng} {max_lat},"
            f"{min_lng} {min_lat}))"
        )
        clauses.append(
            "MBRIntersects(E.GEOM, ST_GeomFromText(%s, "
            f"{SRID}, 'axis-order=long-lat'))")
        params.append(wkt)
    if bairros:
        sem_bairro = "Não informado" in bairros
        bairros_validos = [b for b in bairros if b != "Não informado"]
        bairro_clauses = []
        if bairros_validos:
            bairro_clauses.append(f"B.NOME IN ({','.join(['%s'] * len(bairros_validos))})")
            params.extend(bairros_validos)
        if sem_bairro:
            bairro_clauses.append("(B.NOME IS NULL OR B.NOME = '' OR B.NOME = %s)")
            params.append("Não informado")
        clauses.append("(" + " OR ".join(bairro_clauses) + ")")
    if tipo:
        clauses.append("E.TIPO_USO = %s")
        params.append(tipo)
    if fonte:
        clauses.append("F.NOME = %s")
        params.append(fonte)
    if not clauses:
        return ("", [])
    return ("WHERE " + " AND ".join(clauses), params)


def connect(database=DB_NAME, autocommit=True):
    kwargs = dict(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        # Sem transação aberta entre pedidos: uma conexão reaproveitada
        # precisa enxergar uma reimportação feita depois.
        autocommit=autocommit,
    )
    if DB_SSL_CA:
        kwargs["ssl"] = {"ca": DB_SSL_CA}
    return pymysql.connect(**kwargs)


_idle_connections = queue.LifoQueue(maxsize=8)


@contextmanager
def connection():
    """Conexão reaproveitada entre pedidos (abrir uma custa ~45 ms)."""
    try:
        conn = _idle_connections.get_nowait()
        conn.ping(reconnect=True)
    except queue.Empty:
        conn = connect()
    try:
        yield conn
    except BaseException:
        conn.close()
        raise
    try:
        _idle_connections.put_nowait(conn)
    except queue.Full:
        conn.close()


def close_idle_connections():
    while True:
        try:
            _idle_connections.get_nowait().close()
        except queue.Empty:
            return


def row_to_feature(row):
    geometry = json.loads(row["geojson"])
    properties = {k: row[k] for k in (
        "id", "area_m2", "tipo", "endereco", "bairro",
        "estabelecimento", "num_pavimentos", "fonte",
    )}
    properties["tipo"] = properties["tipo"] or "desconhecido"
    properties["geometry_key"] = row["geometry_key"]
    return {
        "type": "Feature",
        "id": row["id"],
        "geometry": geometry,
        "properties": properties,
    }


def row_to_light_feature(row):
    """Feature mínima para renderizar o mapa (cor, filtro, 3D)."""
    geometry = json.loads(row["geojson"])
    return {
        "type": "Feature",
        "id": row["id"],
        "geometry": geometry,
        "properties": {
            "id": row["id"],
            "tipo": row.get("tipo") or "desconhecido",
            "fonte": row.get("fonte"),
            "num_pavimentos": row.get("num_pavimentos"),
            "geometry_key": row["geometry_key"],
        },
    }


def fetch_all_buildings(bbox=None, bairros=None, tipo=None, fonte=None,
                         light=False, limit=None, offset=0, after_id=None):
    with connection() as conn, conn.cursor() as cur:
        fields = SELECT_LIGHT_FIELDS if light else SELECT_FIELDS
        converter = row_to_light_feature if light else row_to_feature
        where, params = build_where(
            bbox=bbox, bairros=bairros, tipo=tipo, fonte=fonte)
        if after_id is not None:
            where += (" AND " if where else "WHERE ") + "E.ID > %s"
            params.append(int(after_id))
        sql = f"SELECT {fields} {FROM_EDIFICACOES} {where} ORDER BY E.ID"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
            if after_id is None:
                sql += " OFFSET %s"
                params.append(int(offset))
        cur.execute(sql, params)
        return [converter(r) for r in cur.fetchall()]


SEARCH_FIELDS = ("EN.LOGRADOURO_NUM", "EN.ESTABELECIMENTO", "B.NOME")
MAX_SEARCH_TERMS = 6

FROM_SEARCH = (
    "FROM EDIFICACOES E "
    "JOIN FONTES F ON F.ID = E.FONTE_ID "
    "LEFT JOIN BAIRROS B ON B.ID = E.BAIRRO_ID "
    "LEFT JOIN ENDERECO_EDIFICACAO EE ON EE.EDIFICACAO_ID = E.ID "
    "LEFT JOIN ENDERECOS EN ON EN.ID = EE.ENDERECO_ID"
)


def _like_escape(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_search_where(query):
    """Cada palavra precisa aparecer em algum campo (endereço, estabelecimento
    ou bairro). A collation da tabela já ignora acento e caixa."""
    terms = query.split()[:MAX_SEARCH_TERMS]
    clauses, params = [], []
    for term in terms:
        pattern = f"%{_like_escape(term)}%"
        clauses.append("(" + " OR ".join(f"{field} LIKE %s" for field in SEARCH_FIELDS) + ")")
        params.extend([pattern] * len(SEARCH_FIELDS))
    return " AND ".join(clauses), params


def search(query, limit=8):
    """Prédios e bairros que batem com a busca, com um ponto para o mapa ir até lá."""
    where, params = build_search_where(query)
    if not where:
        return {"buildings": [], "bairros": []}
    prefix = f"{_like_escape(query.strip())}%"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT CAST(E.ID AS CHAR) AS id, EN.LOGRADOURO_NUM AS endereco, "
            "B.NOME AS bairro, EN.ESTABELECIMENTO AS estabelecimento, E.TIPO_USO AS tipo, "
            "ST_X(ST_Centroid(ST_SRID(E.GEOM, 0))) AS lng, ST_Y(ST_Centroid(ST_SRID(E.GEOM, 0))) AS lat "
            f"{FROM_SEARCH} WHERE {where} "
            "AND (EN.LOGRADOURO_NUM IS NOT NULL OR EN.ESTABELECIMENTO IS NOT NULL) "
            # Quem começa com o texto digitado vem primeiro.
            "ORDER BY (EN.ESTABELECIMENTO LIKE %s OR EN.LOGRADOURO_NUM LIKE %s) DESC, EN.LOGRADOURO_NUM "
            "LIMIT %s",
            [*params, prefix, prefix, int(limit)],
        )
        buildings = [
            {**row, "tipo": row["tipo"] or "desconhecido",
             "lng": float(row["lng"]), "lat": float(row["lat"])}
            for row in cur.fetchall()
        ]
        terms = query.split()[:MAX_SEARCH_TERMS]
        cur.execute(
            "SELECT B.NOME AS bairro, COUNT(*) AS total, "
            "AVG(ST_X(ST_Centroid(ST_SRID(E.GEOM, 0)))) AS lng, AVG(ST_Y(ST_Centroid(ST_SRID(E.GEOM, 0)))) AS lat "
            f"{FROM_EDIFICACOES} WHERE "
            + " AND ".join(["B.NOME LIKE %s"] * len(terms))
            + " GROUP BY B.NOME ORDER BY total DESC LIMIT 4",
            [f"%{_like_escape(t)}%" for t in terms],
        )
        bairros = [
            {"bairro": row["bairro"], "total": int(row["total"]),
             "lng": float(row["lng"]), "lat": float(row["lat"])}
            for row in cur.fetchall()
        ]
    return {"buildings": buildings, "bairros": bairros}


def fetch_building_addresses(building_id):
    """Todos os endereços associados a este prédio."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT CAST(EN.ID AS CHAR) AS id, EN.LOGRADOURO_NUM AS endereco, "
            "EN.ESTABELECIMENTO AS estabelecimento, EN.TIPO_USO AS tipo "
            "FROM ENDERECO_EDIFICACAO EE "
            "JOIN ENDERECOS EN ON EN.ID = EE.ENDERECO_ID "
            "WHERE EE.EDIFICACAO_ID = %s ORDER BY EN.LOGRADOURO_NUM, EN.ID LIMIT 100",
            (int(building_id),),
        )
        return [{**row, "tipo": row["tipo"] or "desconhecido"} for row in cur.fetchall()]


def fetch_data_version():
    """Muda a cada importação com base no término da última carga concluída."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT UNIX_TIMESTAMP(CONCLUIDA_EM) AS created "
            "FROM CARGAS WHERE CONCLUIDA_EM IS NOT NULL "
            "ORDER BY ID DESC LIMIT 1"
        )
        row = cur.fetchone()
    return str(int(row["created"])) if row and row["created"] is not None else "0"


def fetch_centroids():
    """Um ponto por footprint; defensivamente deduplica por geometry_key."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ST_X(ST_Centroid(ST_SRID(E.GEOM, 0))) AS x, ST_Y(ST_Centroid(ST_SRID(E.GEOM, 0))) AS y, "
            f"E.TIPO_USO AS tipo, F.NOME AS fonte, {GEOMETRY_KEY} "
            f"{FROM_EDIFICACOES} ORDER BY E.ID"
        )
        rows = cur.fetchall()
    unique = {}
    for row in rows:
        unique.setdefault(row["geometry_key"], row)
    return list(unique.values())


def fetch_building_tile(bounds, cancelled=None):
    """Busca footprints do tile e devolve features prontas para MVT."""
    where, params = build_where(bbox=bounds)
    if cancelled is not None and cancelled.is_set():
        raise CancelledError()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {TILE_FIELDS} {FROM_EDIFICACOES} {where} ORDER BY E.ID",
            params,
        )
        rows = cur.fetchall()
    if cancelled is not None and cancelled.is_set():
        raise CancelledError()
    # Muitos endereços compartilham a mesma cobertura. O mapa 2D precisa
    # desenhar cada footprint uma vez; o detalhe continua disponível pela
    # rota individual.
    unique = {}
    for row in rows:
        unique.setdefault(row["geometry_key"], row)
    rows = list(unique.values())
    # SRID 4674 armazena lat/lon; shapely.from_wkb interpreta x=lat, y=lon.
    # Invertemos para lon/lat que o MVT e o frontend esperam.
    raw = shapely.from_wkb([row["wkb"] for row in rows])
    geometries = shapely.transform(raw, lambda c: c[:, ::-1])
    return [
        {
            "geometry": geometry,
            "properties": {
                "id": str(row["id"]),
                "tipo": row.get("tipo") or "desconhecido",
                "fonte": row.get("fonte") or "",
                "num_pavimentos": row.get("num_pavimentos") or 0,
                # O ponto da visão intermediária tem a mesma área do prédio.
                "area_m2": float(row.get("area_m2") or 0),
                "geometry_key": row["geometry_key"],
            },
        }
        for row, geometry in zip(rows, geometries)
    ]


def fetch_stats(bbox=None, bairros=None, tipo=None, fonte=None):
    """Agregados (total, por tipo, por bairro, áreas) com os mesmos filtros."""
    where, params = build_where(
        bbox=bbox, bairros=bairros, tipo=tipo, fonte=fonte)
    base = f"{FROM_EDIFICACOES} {where}"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n, CAST(SUM(E.AREA_M2) AS DOUBLE) AS total, "
            f"CAST(AVG(E.AREA_M2) AS DOUBLE) AS media {base}", params)
        totais = cur.fetchone()

        cur.execute(
            f"SELECT E.TIPO_USO AS tipo, COUNT(*) AS n {base} GROUP BY E.TIPO_USO",
            params)
        by_tipo = {(r["tipo"] or "desconhecido"): r["n"] for r in cur.fetchall()}

        cur.execute(
            f"SELECT B.NOME AS bairro, COUNT(*) AS n {base} GROUP BY B.NOME",
            params)
        by_bairro = {(r["bairro"] or "Não informado"): r["n"]
                      for r in cur.fetchall()}

        return {
            "total": totais["n"] or 0,
            "by_tipo": by_tipo,
            "by_bairro": by_bairro,
            "area_total_m2": totais["total"],
            "area_media_m2": totais["media"],
        }


def fetch_building(building_id: str):
    if not building_id.isdigit():  # ID é inteiro; evita cast implícito do MySQL
        return None
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {SELECT_FIELDS} {FROM_EDIFICACOES} WHERE E.ID = %s",
            (int(building_id),))
        row = cur.fetchone()
        return row_to_feature(row) if row else None
