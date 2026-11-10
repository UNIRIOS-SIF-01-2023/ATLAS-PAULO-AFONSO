"""API do Atlas Urbano: detalhes GeoJSON e mapa em tiles vetoriais."""

import asyncio
import gzip
import json
import logging
import math
import os
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Event, Lock

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
import mapbox_vector_tile
import numpy as np
import shapely
from shapely.geometry import box

from . import auth, db, rooftop
from .overview import LEVEL_CELL_DEG, build_overview
from .schemas import AuthToken, UserCreate, UserLogin, UserPublic
from .tile_cache import TileCache

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Garante a tabela `users` na subida; sem banco, só avisa no log
    (os testes de integração já pulam sozinhos nesse caso)."""
    try:
        auth.ensure_auth_schema()
    except Exception:
        logger.warning("tabela users não garantida (banco fora do ar?)", exc_info=True)
    yield


app = FastAPI(title="Atlas Urbano", version="0.1.0", lifespan=lifespan)
tile_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="map-tiles")
warmup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tile-warmup")
# A chave inclui a versão dos dados: uma reimportação gera chaves novas e as
# antigas saem pelo LRU. Comprimidos, cabem bem mais blocos no mesmo espaço.
tile_cache = TileCache(max_bytes=128 * 1024 * 1024, max_items=4096, ttl=24 * 3600)

# Níveis de detalhe dos tiles (o cliente segue os mesmos, frontend/src/mapTiles.ts).
# De 12 a 14, um prédio continua polígono enquanto tiver ~3 px de largura no
# tile; os menores viram um ponto no centro (camada "dots"). Afastando, cada
# vez mais prédios viram ponto, dos menores para os maiores. A partir do 14
# todos são polígonos, e o 14 traz também os pontos para a troca ser gradual.
# O servidor deixa esses zooms prontos quando o mapa abre.
DOT_MAX_ZOOM = 14
POLYGON_MIN_ZOOM = 14
MIN_POLYGON_PX = 3
WARMUP_ZOOMS = (12, 13, 14)
VERSION_TTL_S = 5
# Suba ao mudar o que vai dentro dos tiles ou da grade. Ela entra na versão
# das URLs; sem isso o navegador seguiria usando os tiles imutáveis antigos.
MAP_FORMAT = 2
IMMUTABLE = "public, max-age=86400, immutable"

# Mantém compactas as respostas JSON e os tiles vetoriais maiores.
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health():
    return {"status": "ok", "service": "atlas-urbano-api"}


_version_lock = Lock()
_version_memo = {"checked": 0.0, "version": None}
_overview_lock = Lock()
_overview_memo = {}
_warmed_versions = set()


def current_version():
    """Versão dos dados + formato do mapa, relida no máximo a cada VERSION_TTL_S."""
    with _version_lock:
        now = time.monotonic()
        if _version_memo["version"] is None or now - _version_memo["checked"] > VERSION_TTL_S:
            _version_memo["version"] = f"{db.fetch_data_version()}-{MAP_FORMAT}"
            _version_memo["checked"] = now
        return _version_memo["version"]


def _overview_for(version):
    """Grades da visão afastada, uma por nível, calculadas uma vez por versão."""
    with _overview_lock:
        if _overview_memo.get("version") != version:
            rows = db.fetch_centroids()
            levels = [build_overview(rows, cell_deg) for cell_deg in LEVEL_CELL_DEG]
            finest = levels[-1][0]["features"]
            _overview_memo.update(
                version=version,
                bodies=[json.dumps(geojson, separators=(",", ":")).encode() for geojson, _ in levels],
                # A grade mais grossa contém todas as outras.
                bounds=levels[0][1],
                cells=[f["geometry"]["coordinates"][0][0] for f in finest],
            )
        return _overview_memo


def _tiles_with_buildings(cells, z):
    """Tiles do zoom z que contêm o canto de alguma célula com prédios."""
    scale = 1 << z
    tiles = set()
    for lng, lat in cells:
        x = int((lng + 180) / 360 * scale)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)
        tiles.add((x, y))
    return sorted(tiles)


def _warm_tiles(version, tiles):
    for z, x, y in tiles:
        key = (version, z, x, y)
        if tile_cache.get(key) is not None:
            continue
        try:
            tile_cache.put(key, _compressed_tile(z, x, y, None))
        except Exception:
            logger.exception("pré-aquecimento parou no tile %s/%s/%s", z, x, y)
            return


def _start_warmup(version, cells):
    with _overview_lock:
        if version in _warmed_versions:
            return
        _warmed_versions.add(version)
    tiles = [(z, x, y) for z in WARMUP_ZOOMS for x, y in _tiles_with_buildings(cells, z)]
    warmup_executor.submit(_warm_tiles, version, tiles)


@app.get("/api/map/meta")
def map_meta(usuario: dict = Depends(auth.get_current_user)):
    """Versão dos dados (entra nas URLs do mapa) e a área com prédios."""
    version = current_version()
    overview = _overview_for(version)
    _start_warmup(version, overview["cells"])
    return {"version": version, "bounds": overview["bounds"], "levels": len(LEVEL_CELL_DEG)}


@app.get("/api/rooftop/meta")
@app.get("/api/rooftop/vila-nobre/meta")
def rooftop_vila_nobre_meta(usuario: dict = Depends(auth.get_current_user)):
    """Extent e contagens da camada local do CNEFE rooftop; sem API externa."""
    return rooftop.map_meta()


@app.get("/api/rooftop")
@app.get("/api/rooftop/vila-nobre")
def rooftop_vila_nobre(usuario: dict = Depends(auth.get_current_user)):
    """Pontos rooftop gerados localmente pelo cnefe_rooftop.py."""
    return JSONResponse(content=rooftop.geojson())


@app.get("/api/rooftop/poligonos")
@app.get("/api/rooftop/vila-nobre/poligonos")
def rooftop_vila_nobre_poligonos(usuario: dict = Depends(auth.get_current_user)):
    """Prédios que receberam pontos, com o resumo do cruzamento."""
    return JSONResponse(content=rooftop.poligonos())


@app.get("/api/map/overview")
def map_overview(
    level: int = Query(default=0, ge=0, lt=len(LEVEL_CELL_DEG),
                       description="0 = grade mais grossa (~350 m)"),
    v: str | None = None,
    usuario: dict = Depends(auth.get_current_user),
):
    """Grade de densidade de um nível de detalhe (GeoJSON)."""
    version = current_version()
    overview = _overview_for(version)
    return Response(
        content=overview["bodies"][level],
        media_type="application/geo+json",
        headers={"Cache-Control": IMMUTABLE if v == version else "no-cache"},
    )


@app.get("/api/buildings")
def list_buildings(
    bbox: str | None = Query(
        default=None,
        description="Área visível: minLng,minLat,maxLng,maxLat",
    ),
    bairro: list[str] | None = Query(default=None),
    tipo: str | None = None,
    fonte: str | None = None,
    light: bool = Query(
        default=False,
        description="Só campos de renderização (tipo, fonte, num_pavimentos)",
    ),
    em_cobertura: bool = Query(
        default=False,
        description="Esconde bolinhas fora da janela com polígonos",
    ),
    limit: int | None = Query(default=None, ge=1, le=db.MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    after_id: str | None = Query(default=None),
    usuario: dict = Depends(auth.get_current_user),
):
    """Edificações em GeoJSON, com filtros e paginação opcionais.

    Sem parâmetros mantém o comportamento antigo (tudo de uma vez).
    """
    try:
        parsed_bbox = db.parse_bbox(bbox)
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox inválido")
    features = db.fetch_all_buildings(
        bbox=parsed_bbox,
        bairros=bairro,
        tipo=tipo,
        fonte=fonte,
        light=light,
        limit=limit,
        offset=offset,
        after_id=after_id,
        em_cobertura=em_cobertura,
    )
    body = {
        "type": "FeatureCollection",
        "name": "buildings",
        "features": features,
    }
    return JSONResponse(content=body)


@app.get("/api/tiles/{z}/{x}/{y}.pbf")
async def building_tile(z: int, x: int, y: int, request: Request, v: str | None = None,
                        usuario: dict = Depends(auth.get_current_user)):
    """Tile vetorial binário: só transfere geometrias da área pedida.

    `v` é a versão dos dados que o cliente recebeu de /api/map/meta. Com ela
    a URL muda a cada reimportação e o tile pode ficar no cache do navegador.
    """
    if z < 0 or z > 20:
        raise HTTPException(status_code=404, detail="tile fora do zoom suportado")
    scale = 1 << z
    if x < 0 or y < 0 or x >= scale or y >= scale:
        raise HTTPException(status_code=404, detail="tile fora do mapa")
    key = (v, z, x, y)
    body = tile_cache.get(key)
    if body is None:
        cancelled = Event()
        future = tile_executor.submit(_compressed_tile, z, x, y, cancelled)
        waiter = asyncio.wrap_future(future)
        # Pedidos abandonados não leem o resultado; evita aviso no log.
        waiter.add_done_callback(lambda done: done.cancelled() or done.exception())
        try:
            # O cancelamento HTTP precisa chegar à consulta e ao encoder.
            # Só cancelar o fetch deixava dezenas de gerações consumindo RAM/CPU.
            # A espera acorda assim que o tile fica pronto; o intervalo só
            # define a frequência de checar a desconexão.
            while not (await asyncio.wait({waiter}, timeout=0.05))[0]:
                if await request.is_disconnected():
                    cancelled.set()
                    future.cancel()
                    return Response(status_code=499)
            body = future.result()
        except asyncio.CancelledError:
            cancelled.set()
            future.cancel()
            raise
        except CancelledError:
            return Response(status_code=499)
        tile_cache.put(key, body)
    headers = {"Cache-Control": IMMUTABLE if v else "no-cache", "Vary": "Accept-Encoding"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
    else:
        body = gzip.decompress(body)
    return Response(
        content=body,
        media_type="application/vnd.mapbox-vector-tile",
        headers=headers,
    )


def _compressed_tile(z: int, x: int, y: int, cancelled: Event):
    """Gera e comprime no worker, fora do event loop."""
    return gzip.compress(_building_tile_bytes(z, x, y, cancelled), compresslevel=6)


def _building_tile_bytes(z: int, x: int, y: int, cancelled: Event | None = None):
    """Gera um bloco, interrompendo entre as etapas se ele saiu da visão."""
    def check_cancelled():
        if cancelled is not None and cancelled.is_set():
            raise CancelledError()

    check_cancelled()
    scale = 1 << z

    def tile_lat(tile_y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * tile_y / scale))))

    west = x / scale * 360 - 180
    east = (x + 1) / scale * 360 - 180
    north = tile_lat(y)
    south = tile_lat(y + 1)
    geographic_bounds = (west, south, east, north)
    projected_bounds = (
        EARTH_RADIUS * math.radians(west),
        EARTH_RADIUS * math.asinh(math.tan(math.radians(south))),
        EARTH_RADIUS * math.radians(east),
        EARTH_RADIUS * math.asinh(math.tan(math.radians(north))),
    )

    # Mantém precisão submétrica também ao afastar a câmera. Uma resolução
    # muito baixa faz pequenos prédios desaparecerem durante a quantização.
    extent = min(1048576, 8192 << max(0, 14 - z))
    tolerance = (projected_bounds[2] - projected_bounds[0]) / extent * 0.75
    clip_bounds = box(*projected_bounds).buffer(tolerance * 8, cap_style=3, join_style=2)
    shapely.prepare(clip_bounds)

    rows = (db.fetch_building_tile(geographic_bounds, cancelled=cancelled)
            if cancelled is not None else db.fetch_building_tile(geographic_bounds))
    check_cancelled()

    # Cada etapa roda vetorizada sobre o bloco inteiro (GEOS em C), em vez de
    # chamar Python por coordenada. Isso é o grosso do tempo de geração.
    geometries = np.empty(len(rows), dtype=object)
    geometries[:] = [feature["geometry"] for feature in rows]
    geometries = shapely.transform(geometries, _to_mercator)
    invalid = ~shapely.is_valid(geometries)
    if invalid.any():
        geometries[invalid] = shapely.buffer(geometries[invalid], 0)
    check_cancelled()

    # Quantiza para a grade do tile aqui, de uma vez. Assim o encoder só serializa.
    minx, miny, maxx, maxy = projected_bounds
    origin = np.array([minx, miny])
    factor = np.array([extent / (maxx - minx), extent / (maxy - miny)])

    def to_tile_grid(geoms):
        return shapely.transform(geoms, lambda coords: np.round((coords - origin) * factor))

    areas = np.array([row["properties"].get("area_m2") or 0 for row in rows], dtype=float)
    small = areas < _min_polygon_area(z, (north + south) / 2)

    layers = []
    if z <= DOT_MAX_ZOOM:
        # Cada ponto vai só no tile que contém o centro, para não repetir na borda.
        centers = shapely.centroid(geometries)
        cx, cy = shapely.get_x(centers), shapely.get_y(centers)
        inside = small & (cx >= minx) & (cx < maxx) & (cy >= miny) & (cy < maxy)
        layers.append({
            "name": "dots",
            "features": [
                {"geometry": geometry, "properties": _dot_properties(rows[i]["properties"])}
                for geometry, i in zip(to_tile_grid(centers[inside]), np.flatnonzero(inside))
            ],
        })
        check_cancelled()

    polygon_rows = np.arange(len(rows)) if z >= POLYGON_MIN_ZOOM else np.flatnonzero(~small)
    if len(polygon_rows):
        polygons = geometries[polygon_rows]
        # Só recorta quem cruza a borda; a maioria cabe inteira no bloco.
        crossing = ~shapely.contains_properly(clip_bounds, polygons)
        if crossing.any():
            polygons[crossing] = shapely.intersection(polygons[crossing], clip_bounds)
        keep = ~shapely.is_empty(polygons) & np.isin(shapely.get_type_id(polygons), TILE_TYPE_IDS)
        polygons = shapely.simplify(polygons[keep], tolerance, preserve_topology=True)
        check_cancelled()
        # Orienta os anéis como o encoder faria (externo horário com Y para cima).
        polygons = shapely.orient_polygons(to_tile_grid(polygons), exterior_cw=True)
        properties = [rows[i]["properties"] for i in polygon_rows[keep]]
        layers.append({
            "name": "buildings",
            "features": [
                {"geometry": geometry, "properties": props}
                for geometry, props in zip(polygons, properties)
                if not geometry.is_empty
            ],
        })

    tile = mapbox_vector_tile.encode(
        [layer for layer in layers if layer["features"]],
        # A projeção acima tem Y para cima. O encoder precisa invertê-lo uma
        # vez para as coordenadas MVT (origem no topo, Y para baixo).
        default_options={
            "quantize_bounds": None,
            "check_winding_order": False,
            "extents": extent,
            "y_coord_down": False,
        },
    )
    return tile


EARTH_RADIUS = 6378137.0
MERCATOR_MAX_LAT = 85.05112878
# Point, Polygon, MultiPoint, MultiPolygon (shapely.get_type_id).
TILE_TYPE_IDS = [0, 3, 4, 6]


def _min_polygon_area(z, latitude):
    """Área (m²) a partir da qual um prédio tem MIN_POLYGON_PX de largura no zoom z."""
    meters_per_pixel = 2 * math.pi * EARTH_RADIUS * math.cos(math.radians(latitude)) / (512 << z)
    return (MIN_POLYGON_PX * meters_per_pixel) ** 2


def _dot_properties(props):
    """Só o que o ponto usa. O geometry_key (40 bytes, único por prédio) era
    a maior parte do tile; a área inteira se repete e comprime bem."""
    return {
        "id": props.get("id"),
        "tipo": props.get("tipo") or "desconhecido",
        "fonte": props.get("fonte") or "",
        "area_m2": round(props.get("area_m2") or 0),
    }


def _to_mercator(coords):
    """lon/lat (N×2) → Web Mercator em metros."""
    lat = np.clip(coords[:, 1], -MERCATOR_MAX_LAT, MERCATOR_MAX_LAT)
    return np.column_stack((
        EARTH_RADIUS * np.radians(coords[:, 0]),
        EARTH_RADIUS * np.arcsinh(np.tan(np.radians(lat))),
    ))


@app.get("/api/stats")
def get_stats(
    bbox: str | None = Query(default=None),
    bairro: list[str] | None = Query(default=None),
    tipo: str | None = None,
    fonte: str | None = None,
    em_cobertura: bool = Query(default=False),
    usuario: dict = Depends(auth.get_current_user),
):
    """Agregados (total, por tipo/bairro, áreas) com os mesmos filtros."""
    try:
        parsed_bbox = db.parse_bbox(bbox)
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox inválido")
    return JSONResponse(content=db.fetch_stats(
        bbox=parsed_bbox, bairros=bairro, tipo=tipo, fonte=fonte,
        em_cobertura=em_cobertura))


@app.get("/api/buildings/{building_id}")
def get_building(building_id: str, usuario: dict = Depends(auth.get_current_user)):
    """Uma edificação específica, ou 404."""
    feature = db.fetch_building(building_id)
    if feature is None:
        return JSONResponse(
            content={"detail": "building not found"}, status_code=404
        )
    # Um prédio pode ter vários endereços e estabelecimentos.
    feature["properties"]["enderecos"] = db.fetch_building_addresses(building_id)
    return JSONResponse(content=feature)


@app.get("/api/search")
def search(
    q: str = Query(min_length=2, max_length=120, description="Endereço, estabelecimento ou bairro"),
    limit: int = Query(default=8, ge=1, le=20),
    usuario: dict = Depends(auth.get_current_user),
):
    """Busca para a barra do mapa: prédios e bairros com um ponto para centralizar."""
    return db.search(q, limit=limit)


@app.post("/api/auth/cadastro", response_model=AuthToken, status_code=201)
def cadastro(dados: UserCreate):
    """Cadastra (senha vira hash bcrypt) e já devolve o token de acesso."""
    try:
        user = auth.create_user(dados.nome, dados.email, dados.senha)
    except auth.EmailEmUso:
        raise HTTPException(status_code=409, detail="e-mail já cadastrado")
    return {"access_token": auth.create_token(user["id"]), "user": user}


@app.post("/api/auth/login", response_model=AuthToken)
def login(dados: UserLogin):
    """Confere e-mail + senha e devolve o token Bearer."""
    user = auth.authenticate(dados.email, dados.senha)
    if user is None:
        raise HTTPException(status_code=401, detail="e-mail ou senha inválidos")
    return {"access_token": auth.create_token(user["id"]), "user": user}


@app.get("/api/auth/eu", response_model=UserPublic)
def eu(usuario: dict = Depends(auth.get_current_user)):
    """Quem é o dono do token. Único ponto que confere o usuário no banco;
    o front usa para validar a sessão salva a cada abertura."""
    atual = auth.find_user_by_id(usuario["id"])
    if atual is None:
        raise HTTPException(status_code=401, detail="sessão inválida ou expirada")
    return atual
