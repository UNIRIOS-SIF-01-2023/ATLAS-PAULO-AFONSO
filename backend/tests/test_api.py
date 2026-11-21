"""Testes essenciais (P6): só pontos críticos, sem cobertura completa.

Roda com: doppler run -- python -m pytest backend/tests -q
(dependências de dev: pytest, httpx)
"""

import pytest

from backend import db
from backend.schemas import (
    FeatureFull,
    FeatureLight,
    StatsResponse,
)


# --- P1: construção/validação do BBOX -------------------------------------

def test_parse_bbox_valido():
    assert db.parse_bbox("-38.24,-9.41,-38.21,-9.39") == (
        -38.24, -9.41, -38.21, -9.39)


def test_parse_bbox_none():
    assert db.parse_bbox(None) is None


@pytest.mark.parametrize("raw", [
    "a,b,c,d", "1,2,3", "1,2,3,4,5", "",
    "-38.21,-9.41,-38.24,-9.39",  # min > max
    "0,0,0,0",
    "-200,0,10,0", "0,-100,10,0",  # fora do planeta
    "-38.24,-9.41,-38.21",  # faltando número
])
def test_parse_bbox_invalido_rejeita(raw):
    with pytest.raises(ValueError):
        db.parse_bbox(raw)


# --- P2: filtros combinados (builder puro, sem banco) -----------------------

def test_build_where_vazio():
    assert db.build_where() == ("", [])


def test_build_where_bbox_usa_indice_espacial():
    clause, params = db.build_where(bbox=(-38.24, -9.41, -38.21, -9.39))
    assert "MBRIntersects(E.GEOM, ST_GeomFromText(%s, 4674" in clause
    assert params[0].startswith("POLYGON((")


def test_build_where_combina_tudo_na_ordem():
    clause, params = db.build_where(
        bbox=(-38.24, -9.41, -38.21, -9.39),
        bairros=["CENTRO", "BNH"],
        tipo="comercial",
        fonte="QG/CNEFE",
    )
    assert clause.startswith("WHERE ")
    assert "AND" in clause
    assert params[1:] == ["CENTRO", "BNH", "comercial", "QG/CNEFE"]


def test_build_where_apenas_bairro():
    clause, params = db.build_where(bairros=["CENTRO"])
    assert clause == "WHERE (B.NOME IN (%s))"
    assert params == ["CENTRO"]


# --- P3: conversão para GeoJSON ---------------------------------------------

def _row(full=True):
    base = {"geojson": '{"type":"Point","coordinates":[-38.2,-9.4]}',
            "geometry_key": "k1"}
    if full:
        base.update({
            "id": "x1", "area_m2": 112.5, "tipo": None, "endereco": "Rua A",
            "bairro": "CENTRO", "estabelecimento": None,
            "num_pavimentos": 2, "fonte": "QG/CNEFE",
        })
    else:
        base.update({"id": "x1", "tipo": "comercial", "fonte": "OSM",
                     "num_pavimentos": None})
    return base


def test_row_to_feature_normaliza_tipo():
    f = db.row_to_feature(_row())
    assert f["type"] == "Feature"
    assert f["properties"]["tipo"] == "desconhecido"  # None → fallback
    FeatureFull.model_validate(f)  # contrato válido


def test_row_to_light_feature_minimo():
    f = db.row_to_light_feature(_row(full=False))
    assert set(f["properties"]) == {"id", "geometry_key", "tipo", "fonte", "num_pavimentos"}
    assert "endereco" not in f["properties"]
    FeatureLight.model_validate(f)


def test_stats_schema():
    s = StatsResponse.model_validate({
        "total": 10, "by_tipo": {"residencial": 10},
        "by_bairro": {"CENTRO": 10},
        "area_total_m2": 1000.0, "area_media_m2": 100.0,
    })
    assert s.total == 10


# --- Endpoints principais (db mockado: sem banco) ---------------------------

@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main

    vistas = {}

    def fake_list(**kwargs):
        vistas.update(kwargs)
        return [{"type": "Feature", "id": "a",
                 "geometry": {"type": "Point", "coordinates": [0, 0]},
                 "properties": {"tipo": "residencial", "fonte": None,
                                "num_pavimentos": None}}]

    monkeypatch.setattr(main.db, "fetch_all_buildings", fake_list)
    monkeypatch.setattr(main.db, "fetch_stats",
                        lambda **k: {"total": 1, "by_tipo": {},
                                     "by_bairro": {}, "area_total_m2": None,
                                     "area_media_m2": None})
    monkeypatch.setattr(main.db, "fetch_building", lambda i: None)
    return TestClient(main.app), vistas


def test_list_passa_bbox_e_filtros(client):
    t, vistas = client
    r = t.get("/api/buildings", params={
        "bbox": "-38.24,-9.41,-38.21,-9.39",
        "bairro": ["CENTRO", "BNH"], "tipo": "residencial"})
    assert r.status_code == 200
    assert vistas["bbox"] == (-38.24, -9.41, -38.21, -9.39)
    assert vistas["bairros"] == ["CENTRO", "BNH"]
    assert vistas["tipo"] == "residencial"
    assert r.json()["features"][0]["properties"]["tipo"] == "residencial"


def test_list_bbox_invalido_422(client):
    t, _ = client
    r = t.get("/api/buildings", params={"bbox": "a,b,c,d"})
    assert r.status_code == 422


def test_detail_404_mantido(client):
    t, _ = client
    r = t.get("/api/buildings/nao-existe")
    assert r.status_code == 404
    assert r.json() == {"detail": "building not found"}


def test_stats_endpoint(client):
    t, _ = client
    r = t.get("/api/stats", params={"tipo": "comercial"})
    assert r.status_code == 200
    assert r.json()["total"] == 1


@pytest.mark.parametrize("z", [14, 17])
def test_tile_preserva_eixo_y_para_coordenadas_mvt(monkeypatch, z):
    """Em MVT, latitude sul deve aumentar Y a partir do topo do tile."""
    import math
    from shapely.geometry import Polygon
    from backend import main
    import mapbox_vector_tile

    scale = 1 << z
    x, y = scale // 2, scale // 2
    longitude = (x + 0.5) / scale * 360 - 180
    local_y = 1024
    latitude = math.degrees(math.atan(math.sinh(
        math.pi * (1 - 2 * (y + local_y / 4096) / scale))))
    delta = 360 / scale * 0.01

    monkeypatch.setattr(main.db, "fetch_building_tile", lambda bounds: [{
        "type": "Feature",
        "id": "tile-test",
        "geometry": Polygon([
            (longitude - delta, latitude - delta),
            (longitude + delta, latitude - delta),
            (longitude + delta, latitude + delta),
            (longitude - delta, latitude + delta),
            (longitude - delta, latitude - delta),
        ]),
        "properties": {"id": "tile-test", "geometry_key": "tile-test"},
    }])

    body = main._building_tile_bytes(z, x, y)
    # O decoder padrão também inverte Y. Leia os valores MVT crus, como o
    # navegador, para não mascarar uma inversão incorreta do encoder.
    decoded = mapbox_vector_tile.decode(body, default_options={"y_coord_down": True})
    ring = decoded["buildings"]["features"][0]["geometry"]["coordinates"][0]

    extent = decoded["buildings"]["extent"]
    assert sum(tile_y for _, tile_y in ring) / len(ring) < extent / 2


def test_tile_recorta_predio_que_cruza_blocos_sem_perder_partes(monkeypatch):
    import math
    import mapbox_vector_tile
    from shapely.geometry import Polygon, shape
    from backend import main

    z, x, y = 17, 51619, 68974
    scale = 1 << z
    border = (x + 1) / scale * 360 - 180
    latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / scale))))
    delta = 360 / scale * 0.1
    geometry = Polygon([(border - delta, latitude - delta), (border + delta, latitude - delta),
                        (border + delta, latitude + delta), (border - delta, latitude + delta)])
    monkeypatch.setattr(main.db, "fetch_building_tile", lambda bounds: [{
        "geometry": geometry, "properties": {"id": "boundary", "geometry_key": "same-building"},
    }])
    for tile_x in [x, x + 1]:
        tile = mapbox_vector_tile.decode(main._building_tile_bytes(z, tile_x, y),
                                        default_options={"y_coord_down": True})["buildings"]
        assert len(tile["features"]) == 1
        feature = tile["features"][0]
        assert feature["properties"]["geometry_key"] == "same-building"
        bounds = shape(feature["geometry"]).bounds
        assert -8 <= bounds[0] < bounds[2] <= tile["extent"] + 8


def test_tile_cache_reutiliza_blocos_e_expira(monkeypatch):
    from backend import main
    from backend.tile_cache import TileCache
    from fastapi.testclient import TestClient

    calls = []
    now = [301]
    monkeypatch.setattr(main, "tile_cache", TileCache(clock=lambda: now[0]))
    monkeypatch.setattr(main, "_building_tile_bytes", lambda *args: calls.append(args) or b'tile')
    with TestClient(main.app) as client:
        first = client.get('/api/tiles/17/51619/68974.pbf')
        second = client.get('/api/tiles/17/51619/68974.pbf')
        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        assert len(calls) == 1
        now[0] = 602
        assert client.get('/api/tiles/17/51619/68974.pbf').status_code == 200
        assert len(calls) == 2


def test_tile_cache_limita_bytes_e_quantidade():
    from backend.tile_cache import TileCache

    cache = TileCache(max_bytes=10, max_items=2)
    cache.put('a', b'1234')
    cache.put('b', b'5678')
    assert cache.get('a') == b'1234'
    cache.put('c', b'9012')
    assert cache.get('b') is None
    assert cache.size == 8
    cache.put('large', b'12345678901')
    assert cache.size == 8
    assert cache.get('large') is None


def test_tile_cancelado_para_a_geracao_no_servidor(monkeypatch):
    import asyncio
    from threading import Event
    from concurrent.futures import CancelledError
    from backend import main
    from backend.tile_cache import TileCache

    started, stopped = Event(), Event()
    def slow_tile(z, x, y, cancelled):
        started.set()
        try:
            if cancelled.wait(timeout=2):
                raise CancelledError()
            pytest.fail('Geração continuou mesmo após cancelar o pedido')
        finally:
            stopped.set()
    class Disconnected:
        async def is_disconnected(self):
            return started.is_set()

    monkeypatch.setattr(main, 'tile_cache', TileCache())
    monkeypatch.setattr(main, '_building_tile_bytes', slow_tile)
    response = asyncio.run(main.building_tile(17, 51619, 68974, Disconnected()))
    assert response.status_code == 499
    assert stopped.wait(timeout=1)
    assert main.tile_cache.size == 0


def test_encoder_interrompe_antes_de_consultar_banco(monkeypatch):
    from threading import Event
    from concurrent.futures import CancelledError
    from backend import main

    cancelled = Event()
    cancelled.set()
    monkeypatch.setattr(main.db, 'fetch_building_tile', lambda *args, **kwargs: pytest.fail('Consultou banco após cancelamento'))
    with pytest.raises(CancelledError):
        main._building_tile_bytes(17, 51619, 68974, cancelled)


def test_blocos_pendentes_nao_bloqueiam_api_e_geram_so_dois_por_vez(monkeypatch):
    import asyncio
    from threading import Lock
    from concurrent.futures import CancelledError
    import httpx
    from backend import main
    from backend.tile_cache import TileCache

    active = [0]
    maximum = [0]
    lock = Lock()
    def slow_tile(z, x, y, cancelled):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        try:
            cancelled.wait(timeout=2)
            raise CancelledError()
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(main, 'tile_cache', TileCache())
    monkeypatch.setattr(main, '_building_tile_bytes', slow_tile)
    async def exercise():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            requests = [asyncio.create_task(client.get(f'/api/tiles/17/{51619 + i}/68974.pbf'))
                        for i in range(6)]
            try:
                for _ in range(100):
                    if active[0] == 2:
                        break
                    await asyncio.sleep(0.01)
                response = await asyncio.wait_for(client.get('/'), timeout=0.5)
                assert response.status_code == 200
                assert maximum[0] == 2
            finally:
                for request in requests:
                    request.cancel()
                await asyncio.gather(*requests, return_exceptions=True)
    asyncio.run(exercise())


def test_tile_consulta_todos_poligonos_do_bloco_sem_filtro_de_cobertura(monkeypatch):
    from unittest.mock import MagicMock
    from shapely.geometry import Point, Polygon

    first = {"id": "x1", "tipo": "comercial", "fonte": "OSM", "num_pavimentos": None,
             "wkb": Point(-38.2, -9.4).wkb, "geometry_key": "k1"}
    duplicate = {**first, "id": "duplicate"}
    other = {**first, "id": "other", "geometry_key": "k2",
             "wkb": Polygon([(-38.2, -9.4), (-38.1, -9.4), (-38.1, -9.3)]).wkb}
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [first, duplicate, other]
    db.close_idle_connections()
    monkeypatch.setattr(db, "connect", lambda: connection)

    try:
        features = db.fetch_building_tile((-38.3, -9.5, -38.0, -9.2))
    finally:
        db.close_idle_connections()
    sql, params = cursor.execute.call_args.args
    assert "MBRIntersects" in sql
    assert "LIMIT" not in sql
    assert len(params) == 1
    assert len(features) == 2


# --- Integração real (pula se o banco estiver fora) --------------------------

def _db_vivo():
    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_vivo(), reason="MariaDB local fora do ar")
@pytest.mark.parametrize("z,x,y", [(17, 51619, 68974), (14, 6452, 8621)])
def test_integracao_tile_contem_todos_footprints_visiveis(z, x, y):
    import math
    import mapbox_vector_tile
    from shapely.geometry import box
    from backend import main

    scale = 1 << z
    def latitude(tile_y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * tile_y / scale))))
    bounds = (x / scale * 360 - 180, latitude(y + 1),
              (x + 1) / scale * 360 - 180, latitude(y))
    window = box(*bounds)
    # A consulta MBR pode incluir polígonos cuja caixa toca o bloco, mas cuja
    # área fica inteiramente fora. Todos os que realmente entram devem sair.
    expected = {
        feature["properties"]["geometry_key"]
        for feature in db.fetch_building_tile(bounds)
        if feature["geometry"].intersection(window).area > 0
    }
    tile = mapbox_vector_tile.decode(main._building_tile_bytes(z, x, y),
                                    default_options={"y_coord_down": True})["buildings"]
    actual = {feature["properties"]["geometry_key"] for feature in tile["features"]}
    assert expected
    assert expected <= actual, f"Footprints ausentes: {sorted(expected - actual)}"


@pytest.mark.skipif(not _db_vivo(), reason="MariaDB local fora do ar")
def test_integracao_geometry_key_igual_no_tile_e_no_detalhe():
    # O mapa isola os endereços do prédio clicado comparando essas chaves.
    tile = db.fetch_building_tile((-38.2265, -9.4035, -38.2220, -9.4000))
    assert tile
    for feature in tile[:25]:
        detail = db.fetch_building(feature["properties"]["id"])
        assert detail["properties"]["geometry_key"] == feature["properties"]["geometry_key"]


@pytest.mark.skipif(not _db_vivo(), reason="MariaDB local fora do ar")
def test_integracao_bbox_coerente_com_stats():
    bbox = db.parse_bbox("-38.24,-9.41,-38.21,-9.39")
    feats = db.fetch_all_buildings(bbox=bbox, tipo="comercial", light=True)
    stats = db.fetch_stats(bbox=bbox, tipo="comercial")
    assert stats["total"] == len(feats) > 0
    assert stats["by_tipo"] == {"comercial": len(feats)}
    for f in feats:
        FeatureLight.model_validate(f)


def test_fetch_stats_sem_bbox_nem_fonte_le_o_resumo(monkeypatch):
    from contextlib import contextmanager

    chamadas = []
    linhas = [
        {"BAIRRO_NOME": "CENTRO", "TIPO_USO": "comercial", "N_EDIFICACOES": 3, "AREA_SOMA_M2": 300},
        {"BAIRRO_NOME": "CENTRO", "TIPO_USO": "residencial", "N_EDIFICACOES": 1, "AREA_SOMA_M2": 100},
    ]

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params): chamadas.append((sql, params))
        def fetchall(self): return linhas

    class Conn:
        def cursor(self): return Cur()

    @contextmanager
    def conn():
        yield Conn()

    monkeypatch.setattr(db, "connection", conn)
    r = db.fetch_stats(bairros=["CENTRO"], tipo="comercial")
    assert len(chamadas) == 1
    sql, params = chamadas[0]
    assert "FROM AGG_BAIRRO_TIPO" in sql and "EDIFICACOES E" not in sql
    assert "BAIRRO_NOME IN (%s)" in sql and "TIPO_USO = %s" in sql
    assert params == ["CENTRO", "comercial"]
    assert r == {"total": 4, "by_tipo": {"comercial": 3, "residencial": 1},
                 "by_bairro": {"CENTRO": 4}, "area_total_m2": 400.0, "area_media_m2": 100.0}


def test_fetch_stats_resumo_vazio_nao_divide_por_zero(monkeypatch):
    from contextlib import contextmanager

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): pass
        def fetchall(self): return []

    class Conn:
        def cursor(self): return Cur()

    @contextmanager
    def conn():
        yield Conn()

    monkeypatch.setattr(db, "connection", conn)
    assert db.fetch_stats()["area_media_m2"] is None


@pytest.mark.skipif(not _db_vivo(), reason="MariaDB local fora do ar")
def test_integracao_resumo_bate_com_edificacoes():
    resumo = db.fetch_stats()
    # Caixa regional que cobre o município: o globo inteiro volta vazio no MariaDB.
    direto = db.fetch_stats(bbox=(-39, -10, -37, -9))
    assert resumo["total"] == direto["total"]
    assert resumo["by_tipo"] == direto["by_tipo"]
    assert resumo["by_bairro"] == direto["by_bairro"]
    assert resumo["area_total_m2"] == pytest.approx(direto["area_total_m2"])
    assert resumo["area_media_m2"] == pytest.approx(direto["area_media_m2"])


# --- Visão afastada e versão dos dados (db mockado) -------------------------

def test_overview_agrupa_por_celula_e_separa_enderecos():
    from backend.overview import build_overview

    rows = [
        {"x": 0.0001, "y": 0.0001, "tipo": "residencial", "fonte": "Google"},
        {"x": 0.0002, "y": 0.0002, "tipo": "desconhecido", "fonte": "Google"},
        {"x": 0.0003, "y": 0.0003, "tipo": "comercial", "fonte": "QG/CNEFE"},
        {"x": 0.0031, "y": 0.0001, "tipo": None, "fonte": "QG/CNEFE"},
    ]
    geojson, bounds = build_overview(rows, cell_deg=0.001)
    cells = {f["id"]: f["properties"] for f in geojson["features"]}
    assert set(cells) == {"0:0", "3:0"}
    assert cells["0:0"]["n_b"] == 2 and cells["0:0"]["n_p"] == 1
    assert cells["0:0"]["b_residencial"] == 1 and cells["0:0"]["p_comercial"] == 1
    # `desconhecido` não vence um uso conhecido.
    assert cells["0:0"]["tipo_b"] == "residencial"
    assert cells["3:0"] == {**cells["3:0"], "n_b": 0, "n_p": 1,
                            "tipo_b": "desconhecido", "tipo": "desconhecido"}
    assert bounds == [0.0, 0.0, 0.004, 0.001]


def test_overview_vazio_sem_bounds():
    from backend.overview import build_overview

    assert build_overview([]) == ({"type": "FeatureCollection", "features": []}, None)


@pytest.fixture()
def map_api(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    from backend.tile_cache import TileCache

    state = {"version": "100", "warmups": []}
    monkeypatch.setattr(main.db, "fetch_data_version", lambda: state["version"])
    monkeypatch.setattr(main.db, "fetch_centroids", lambda: [
        {"x": -38.22, "y": -9.40, "tipo": "residencial", "fonte": "Google"},
    ])
    monkeypatch.setattr(main, "_version_memo", {"checked": 0.0, "version": None})
    monkeypatch.setattr(main, "_overview_memo", {})
    monkeypatch.setattr(main, "_warmed_versions", set())
    monkeypatch.setattr(main, "VERSION_TTL_S", -1)
    monkeypatch.setattr(main, "tile_cache", TileCache())

    class FakeExecutor:
        def submit(self, fn, *args):
            state["warmups"].append(args)

    monkeypatch.setattr(main, "warmup_executor", FakeExecutor())
    return TestClient(main.app), state


def test_meta_traz_versao_bounds_e_aquece_uma_vez_por_versao(map_api):
    from backend import main

    client, state = map_api
    body = client.get("/api/map/meta").json()
    assert body["version"] == f"100-{main.MAP_FORMAT}"
    west, south, east, north = body["bounds"]
    assert west <= -38.22 < east and south <= -9.40 < north
    client.get("/api/map/meta")
    assert len(state["warmups"]) == 1
    version, tiles = state["warmups"][0]
    assert version == f"100-{main.MAP_FORMAT}" and tiles == [(12, 1613, 2155), (13, 3226, 4310), (14, 6452, 8621)]

    state["version"] = "200"
    assert client.get("/api/map/meta").json()["version"] == f"200-{main.MAP_FORMAT}"
    assert len(state["warmups"]) == 2


def test_overview_so_fica_no_cache_do_navegador_com_a_versao_atual(map_api):
    from backend import main

    client, state = map_api
    atual = client.get(f"/api/map/overview?v=100-{main.MAP_FORMAT}")
    assert atual.status_code == 200
    assert atual.json()["features"][0]["properties"]["b_residencial"] == 1
    assert "immutable" in atual.headers["cache-control"]
    assert client.get("/api/map/overview?v=99").headers["cache-control"] == "no-cache"


def test_overview_serve_cada_nivel_de_detalhe(map_api):
    from backend.overview import LEVEL_CELL_DEG

    client, _ = map_api
    assert client.get("/api/map/meta").json()["levels"] == len(LEVEL_CELL_DEG)
    for level, cell_deg in enumerate(LEVEL_CELL_DEG):
        ring = client.get(f"/api/map/overview?level={level}").json()["features"][0]["geometry"]["coordinates"][0]
        assert ring[1][0] - ring[0][0] == pytest.approx(cell_deg)
    assert client.get(f"/api/map/overview?level={len(LEVEL_CELL_DEG)}").status_code == 422


def test_tile_separa_cache_por_versao_dos_dados(monkeypatch, map_api):
    from backend import main

    client, _ = map_api
    calls = []
    monkeypatch.setattr(main, "_building_tile_bytes", lambda *args: calls.append(args) or b"tile")
    first = client.get("/api/tiles/15/12905/17243.pbf?v=100")
    client.get("/api/tiles/15/12905/17243.pbf?v=100")
    assert len(calls) == 1
    assert "immutable" in first.headers["cache-control"]
    client.get("/api/tiles/15/12905/17243.pbf?v=200")
    assert len(calls) == 2
    assert client.get("/api/tiles/15/12905/17243.pbf").headers["cache-control"] == "no-cache"


def _square_at(z, x, y, fx, fy, frac=0.01):
    """Quadrado pequeno na fração (fx, fy) do tile z/x/y, em lon/lat."""
    import math
    from shapely.geometry import box as shp_box
    scale = 1 << z
    lng = (x + fx) / scale * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + fy) / scale))))
    d = 360 / scale * frac
    return shp_box(lng - d, lat - d, lng + d, lat + d)


def test_tile_afastado_traz_so_pontos_e_cada_um_uma_vez(monkeypatch):
    import mapbox_vector_tile
    from backend import main

    z, x, y = 12, 1613, 2155
    near_edge = _square_at(z, x, y, 0.999, 0.5, frac=0.002)  # cruza a borda leste
    middle = _square_at(z, x, y, 0.5, 0.5)
    rows = [
        {"geometry": g, "properties": {"id": i, "tipo": "residencial", "fonte": "Google",
                                       "num_pavimentos": 0, "area_m2": 120.4,
                                       "geometry_key": "k" * 40}}
        for i, g in (("edge", near_edge), ("middle", middle))
    ]
    monkeypatch.setattr(main.db, "fetch_building_tile", lambda bounds: rows)
    tile = mapbox_vector_tile.decode(main._building_tile_bytes(z, x, y))
    neighbour = mapbox_vector_tile.decode(main._building_tile_bytes(z, x + 1, y))
    assert "buildings" not in tile
    ids = sorted(f["properties"]["id"] for f in tile["dots"]["features"])
    assert ids == ["edge", "middle"]
    assert "dots" not in neighbour or not neighbour["dots"]["features"]
    props = tile["dots"]["features"][0]["properties"]
    assert props["area_m2"] == 120 and "geometry_key" not in props


def test_tile_14_traz_pontos_e_poligonos_para_a_transicao(monkeypatch):
    import mapbox_vector_tile
    from backend import main

    z, x, y = 14, 6452, 8621
    square = _square_at(z, x, y, 0.5, 0.5)
    monkeypatch.setattr(main.db, "fetch_building_tile", lambda bounds: [{
        "geometry": square,
        "properties": {"id": "a", "tipo": "comercial", "fonte": "Google",
                       "num_pavimentos": 2, "area_m2": 50.0, "geometry_key": "a"},
    }])
    tile = mapbox_vector_tile.decode(main._building_tile_bytes(z, x, y))
    assert len(tile["dots"]["features"]) == len(tile["buildings"]["features"]) == 1
    assert "dots" not in mapbox_vector_tile.decode(main._building_tile_bytes(15, 2 * x, 2 * y))


def test_tile_afastado_mantem_poligono_dos_predios_grandes(monkeypatch):
    import mapbox_vector_tile
    from backend import main

    z, x, y = 12, 1613, 2155
    big_area = main._min_polygon_area(z, -9.4) * 1.5
    monkeypatch.setattr(main.db, "fetch_building_tile", lambda bounds: [
        {"geometry": _square_at(z, x, y, 0.3, 0.5, frac=0.004),
         "properties": {"id": "grande", "tipo": "comercial", "fonte": "Google",
                        "area_m2": big_area, "geometry_key": "g"}},
        {"geometry": _square_at(z, x, y, 0.7, 0.5, frac=0.0005),
         "properties": {"id": "pequeno", "tipo": "residencial", "fonte": "Google",
                        "area_m2": 80.0, "geometry_key": "p"}},
    ])
    tile = mapbox_vector_tile.decode(main._building_tile_bytes(z, x, y))
    assert [f["properties"]["id"] for f in tile["buildings"]["features"]] == ["grande"]
    assert [f["properties"]["id"] for f in tile["dots"]["features"]] == ["pequeno"]
    # O limite cai pela metade da largura a cada nível de zoom.
    assert main._min_polygon_area(13, -9.4) == pytest.approx(main._min_polygon_area(12, -9.4) / 4)


def _sql_capturado(monkeypatch):
    """db.connection() de mentira que anota o SQL e devolve sempre 0 linhas."""
    from contextlib import contextmanager

    sqls = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): sqls.append(sql)
        def fetchall(self): return []

    class Conn:
        def cursor(self): return Cur()

    @contextmanager
    def conn():
        yield Conn()

    monkeypatch.setattr(db, "connection", conn)
    return sqls


def test_consultas_quentes_usam_ponto_rotulo_e_from_enxuto(monkeypatch):
    sqls = _sql_capturado(monkeypatch)

    db.fetch_centroids()
    db.fetch_building_tile((-38.23, -9.41, -38.22, -9.40))
    db.search("centro")

    centroides, tile, predios, bairros = sqls
    for sql in (centroides, tile):
        assert "JOIN FONTES F" in sql and "BAIRROS" not in sql and "ORDER BY" not in sql
    assert "PONTO_ROTULO" in centroides and "ST_Centroid" not in centroides
    for sql in (predios, bairros):
        assert "PONTO_ROTULO" in sql and "ST_Centroid" not in sql and "FONTES" not in sql
    assert "JOIN BAIRROS B" in bairros and "LEFT JOIN BAIRROS" not in bairros


@pytest.mark.skipif(not _db_vivo(), reason="MariaDB local fora do ar")
def test_integracao_centroides_vem_em_lng_lat():
    for p in db.fetch_centroids()[:200]:
        assert -39 < p["x"] < -37 and -10 < p["y"] < -9


# --- Busca e endereços do prédio ---------------------------------------------

def test_busca_exige_cada_palavra_em_algum_campo_e_escapa_curingas():
    where, params = db.build_search_where("  rua 50%_ ")
    assert where.count("(") == 2 and " AND " in where
    assert params[:3] == ["%rua%"] * 3
    assert params[3:] == ["%50\\%\\_%"] * 3
    assert db.build_search_where("   ") == ("", [])


def test_busca_endpoint_valida_e_repassa(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main

    calls = []
    monkeypatch.setattr(main.db, "search", lambda q, limit: calls.append((q, limit)) or {"buildings": [], "bairros": []})
    client = TestClient(main.app)
    assert client.get("/api/search?q=a").status_code == 422
    assert client.get("/api/search?q=veneza&limit=5").json() == {"buildings": [], "bairros": []}
    assert calls == [("veneza", 5)]


def test_detalhe_lista_todos_os_enderecos_do_predio(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main

    monkeypatch.setattr(main.db, "fetch_building", lambda i: {
        "type": "Feature", "id": i, "geometry": {"type": "Point", "coordinates": [0, 0]},
        "properties": {"id": i, "tipo": "comercial"},
    })
    monkeypatch.setattr(main.db, "fetch_building_addresses", lambda i: [
        {"id": i, "endereco": "RUA A, 1", "estabelecimento": None, "tipo": "comercial"},
        {"id": "x", "endereco": "RUA A, 1", "estabelecimento": "PADARIA", "tipo": "comercial"},
    ])
    body = TestClient(main.app).get("/api/buildings/abc").json()
    assert [e["id"] for e in body["properties"]["enderecos"]] == ["abc", "x"]


@pytest.mark.skipif(not _db_vivo(), reason="MariaDB local fora do ar")
def test_integracao_busca_ignora_acento_e_caixa():
    result = db.search("sao", limit=5)
    assert result["buildings"], "nenhum resultado para 'sao'"
    for item in result["buildings"]:
        text = " ".join(filter(None, [item["endereco"], item["estabelecimento"], item["bairro"]])).upper()
        assert "SAO" in text or "SÃO" in text
        assert -39 < item["lng"] < -37 and -10 < item["lat"] < -9


# --- Auth superficial: cadastro e login --------------------------------------

@pytest.fixture(autouse=True)
def _usuario_logado():
    """Toda rota /api exige Bearer: os testes que não falam de auth
    ganham um usuário fake para continuar testando só a sua camada."""
    from backend import main

    main.app.dependency_overrides[main.auth.get_current_user] = lambda: {
        "id": 1, "nome": "Teste", "email": "t@x.com", "criado_em": None,
    }
    yield
    main.app.dependency_overrides.clear()


def test_auth_hash_nunca_expoe_a_senha():
    from backend import auth

    h = auth.hash_senha("secreta123")
    assert h != "secreta123"
    assert auth.confere_senha("secreta123", h)
    assert not auth.confere_senha("errada", h)


def test_auth_schemas_recusam_senha_curta():
    from pydantic import ValidationError

    from backend.schemas import UserCreate, UserPublic

    with pytest.raises(ValidationError):
        UserCreate(nome="Ana", email="ana@x.com", senha="123")
    UserPublic.model_validate({"id": 1, "nome": "Ana", "email": "ana@x.com"})


def test_cadastro_201_com_token_e_duplicado_409(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    monkeypatch.setattr(
        main.auth, "create_user",
        lambda n, e, s: {"id": 1, "nome": n, "email": e, "criado_em": None},
    )
    client = TestClient(main.app)
    body = {"nome": "Ana", "email": "ana@x.com", "senha": "secreta123"}
    r = client.post("/api/auth/cadastro", json=body)
    assert r.status_code == 201
    assert r.json()["token_type"] == "bearer"
    assert r.json()["user"]["email"] == "ana@x.com"
    assert "senha" not in r.text and "senha_hash" not in r.text

    def duplicado(n, e, s):
        raise main.auth.EmailEmUso(e)

    monkeypatch.setattr(main.auth, "create_user", duplicado)
    assert client.post("/api/auth/cadastro", json=body).status_code == 409


def test_cadastro_recusa_senha_curta_sem_banco():
    from fastapi.testclient import TestClient

    from backend import main

    r = TestClient(main.app).post(
        "/api/auth/cadastro",
        json={"nome": "Ana", "email": "ana@x.com", "senha": "123"},
    )
    assert r.status_code == 422


def test_login_200_com_token_e_401(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    user = {"id": 1, "nome": "Ana", "email": "ana@x.com", "criado_em": None}
    monkeypatch.setattr(main.auth, "authenticate", lambda e, s: user)
    client = TestClient(main.app)
    r = client.post("/api/auth/login", json={"email": "ana@x.com", "senha": "x"})
    assert r.status_code == 200
    assert r.json()["user"]["nome"] == "Ana"
    assert r.json()["access_token"]

    monkeypatch.setattr(main.auth, "authenticate", lambda e, s: None)
    assert client.post("/api/auth/login", json={"email": "ana@x.com", "senha": "x"}).status_code == 401


def _conexao_falsa(linha):
    """db.connection() de mentira que devolve sempre a mesma linha."""
    from contextlib import contextmanager

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            pass

        def fetchone(self):
            return linha

    class Conn:
        def cursor(self):
            return Cur()

    @contextmanager
    def conn():
        yield Conn()

    return conn


def _sem_usuario_fake():
    """Desliga o usuário fake do autouse: testa a guarda de verdade."""
    from backend import main

    main.app.dependency_overrides.clear()


def test_jwt_guarda_eu_e_as_rotas(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    _sem_usuario_fake()
    linha = {"ID": 7, "NOME": "T", "EMAIL": "t@x.com", "CRIADO_EM": None}
    monkeypatch.setattr(main.db, "fetch_data_version", lambda: "100")
    monkeypatch.setattr(main, "_version_memo", {"checked": 0.0, "version": None})
    monkeypatch.setattr(main, "_overview_for", lambda v: {"bounds": [0, 0, 1, 1], "cells": []})
    monkeypatch.setattr(main, "_start_warmup", lambda *a: None)
    client = TestClient(main.app)
    token = main.auth.create_token(7)
    auth = {"Authorization": f"Bearer {token}"}

    # Guarda stateless: o mapa abre sem nenhuma ida ao banco.
    from contextlib import contextmanager

    @contextmanager
    def _banco_proibido():
        raise AssertionError("guarda encostou no banco")
        yield

    monkeypatch.setattr(main.db, "connection", _banco_proibido)
    assert client.get("/api/map/meta", headers=auth).status_code == 200
    assert client.get("/api/map/meta").status_code == 401  # sem token
    assert client.get("/api/map/meta", headers={"Authorization": "Bearer adulterado"}).status_code == 401

    # O /eu é o único que confere o usuário no banco.
    monkeypatch.setattr(main.db, "connection", _conexao_falsa(linha))
    assert client.get("/api/auth/eu", headers=auth).json()["email"] == "t@x.com"
    assert client.get("/api/auth/eu").status_code == 401  # sem token
    monkeypatch.setattr(main.db, "connection", _conexao_falsa(None))
    assert client.get("/api/auth/eu", headers=auth).status_code == 401  # usuário sumiu


def test_jwt_expirado_e_de_outro_usuario(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    _sem_usuario_fake()
    client = TestClient(main.app)

    monkeypatch.setattr(main.auth, "JWT_EXPIRE_MINUTES", -1)
    expirado = main.auth.create_token(7)
    assert client.get(
        "/api/auth/eu", headers={"Authorization": f"Bearer {expirado}"}
    ).status_code == 401  # nem chega ao banco


@pytest.mark.skipif(not _db_vivo(), reason="MariaDB local fora do ar")
def test_integracao_cadastro_e_login_reais():
    import uuid

    from fastapi.testclient import TestClient

    from backend import auth, main

    email = f"teste-{uuid.uuid4().hex[:8]}@x.com"
    user = auth.create_user("Teste", email, "secreta123")
    assert user["email"] == email
    assert auth.authenticate(email, "secreta123")["id"] == user["id"]
    assert auth.authenticate(email, "errada") is None
    with pytest.raises(auth.EmailEmUso):
        auth.create_user("Outro", email, "secreta123")

    # De ponta a ponta no HTTP, com a guarda de verdade.
    main.app.dependency_overrides.clear()
    client = TestClient(main.app)
    r = client.post("/api/auth/login", json={"email": email, "senha": "secreta123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    eu = client.get("/api/auth/eu", headers={"Authorization": f"Bearer {token}"})
    assert eu.status_code == 200
    assert eu.json()["id"] == user["id"]
    assert client.get("/api/auth/eu").status_code == 401
