"""Features do classificador de uso, iguais no treino e na API.

Só usa o que todo prédio tem: o polígono, a fonte do footprint e a
vizinhança. Bairro, endereço, origem do tipo e pavimentos ficam de fora:
só os prédios rotulados (com endereço CNEFE) os têm, e usá-los vazaria o rótulo.
"""

from typing import Sequence

import numpy as np
import shapely

CLASSES = ("residencial", "comercial", "servico", "religioso",
           "institucional", "hospedagem", "agropecuario")
FONTES_CONHECIDAS = ("Google Open Buildings", "Microsoft ML Buildings", "OpenStreetMap")
RAIO_PERTO_M = 50
RAIO_VIZINHANCA_M = 150
CELULA_GRUPO_M = 500
# shortcut: sem comercial rotulado por perto a distância vira este teto,
# troque por uma feature "tem comercial" se o teto pesar no modelo.
DIST_MAXIMA_M = 5000.0

M_POR_GRAU = 111320.0
_INDICE_CLASSE = {c: i for i, c in enumerate(CLASSES)}

NOMES = [
    "area_m2", "perimetro_m", "compacidade", "ret_comprimento_m", "ret_largura_m",
    "alongamento", "ocupacao_ret", "n_vertices",
    "fonte_google", "fonte_microsoft", "fonte_osm", "fonte_outra",
    "n_perto_50m", "n_viz_150m", "viz_area_media_m2", "viz_area_mediana_m2",
    "viz_frac_rotulados", *[f"viz_frac_{c}" for c in CLASSES], "dist_comercial_m",
]


def mascara_treino(tipos: Sequence[str], origens: Sequence[str]) -> np.ndarray:
    """Rotulados pelo CNEFE com um uso conhecido."""
    return np.array([o == "cnefe" and t in _INDICE_CLASSE for t, o in zip(tipos, origens)], dtype=bool)


def mascara_previsao(tipos: Sequence[str]) -> np.ndarray:
    return np.array([t == "desconhecido" for t in tipos], dtype=bool)


def _escala(lat0):
    return np.array([M_POR_GRAU * np.cos(np.radians(lat0)), M_POR_GRAU])


def _lat_media(pontos):
    return float(np.mean(pontos[:, 1])) if len(pontos) else 0.0


def pontos_representativos(geoms: np.ndarray) -> np.ndarray:
    """[n, 2] lon/lat de um ponto dentro de cada polígono."""
    return shapely.get_coordinates(shapely.point_on_surface(geoms))


def _retangulos(geoms_m):
    """(comprimento, largura, área) do retângulo mínimo girado de cada prédio."""
    rets = shapely.minimum_rotated_rectangle(geoms_m)
    comp = np.zeros(len(rets))
    larg = np.zeros(len(rets))
    for i, r in enumerate(rets):
        if r.geom_type != "Polygon":
            comp[i] = r.length
            continue
        c = np.asarray(r.exterior.coords)
        lados = np.hypot(*np.diff(c[:3], axis=0).T)
        comp[i], larg[i] = lados.max(), lados.min()
    return comp, larg, shapely.area(rets)


def _vizinhanca(pts_m, areas, tipos, conhecidos):
    n = len(pts_m)
    pontos = shapely.points(pts_m)
    arvore = shapely.STRtree(pontos)
    i, j = arvore.query(pontos, predicate="dwithin", distance=RAIO_VIZINHANCA_M)
    fora_si = i != j
    i, j = i[fora_si], j[fora_si]
    d = np.hypot(*(pts_m[i] - pts_m[j]).T)

    n_perto = np.bincount(i[d <= RAIO_PERTO_M], minlength=n).astype(float)
    n_viz = np.bincount(i, minlength=n).astype(float)
    soma_area = np.bincount(i, weights=areas[j], minlength=n)
    media_area = np.divide(soma_area, n_viz, out=np.zeros(n), where=n_viz > 0)
    mediana_area = np.zeros(n)
    if len(i):
        ordem = np.argsort(i, kind="stable")
        grupos = np.split(areas[j][ordem], np.cumsum(np.bincount(i, minlength=n))[:-1])
        mediana_area = np.array([np.median(g) if len(g) else 0.0 for g in grupos])

    classe = np.array([_INDICE_CLASSE.get(t, -1) for t in tipos])
    rotulado = conhecidos & (classe >= 0)
    jj = rotulado[j]
    n_rot = np.bincount(i[jj], minlength=n).astype(float)
    frac_rot = np.divide(n_rot, n_viz, out=np.zeros(n), where=n_viz > 0)
    por_classe = np.zeros((n, len(CLASSES)))
    np.add.at(por_classe, (i[jj], classe[j][jj]), 1)
    frac_classe = np.divide(por_classe, n_rot[:, None], out=np.zeros_like(por_classe),
                            where=n_rot[:, None] > 0)

    dist_com = np.full(n, DIST_MAXIMA_M)
    comerciais = np.flatnonzero(rotulado & (classe == _INDICE_CLASSE["comercial"]))
    if len(comerciais):
        arv_com = shapely.STRtree(pontos[comerciais])
        # exclusive: o próprio ponto não é o "comercial mais próximo" dele.
        (qi, _), dist = arv_com.query_nearest(pontos, max_distance=DIST_MAXIMA_M, return_distance=True,
                                              exclusive=True, all_matches=False)
        dist_com[qi] = dist

    return np.column_stack([n_perto, n_viz, media_area, mediana_area, frac_rot,
                            frac_classe, dist_com])


def montar_features(geoms: np.ndarray, fontes: Sequence[str], tipos: Sequence[str],
                    conhecidos: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Matriz [n, k] de features (geoms em lon/lat) e os nomes das colunas.

    `conhecidos[i]` diz se o rótulo do prédio i pode servir de contexto para
    os vizinhos (no treino, o fold de teste fica escondido).
    """
    geoms = np.asarray(geoms, dtype=object)
    pts = pontos_representativos(geoms)
    escala = _escala(_lat_media(pts))
    geoms_m = shapely.transform(geoms, lambda c: c * escala)
    pts_m = pts * escala

    area = shapely.area(geoms_m)
    perim = shapely.length(geoms_m)
    compac = np.divide(4 * np.pi * area, perim ** 2, out=np.zeros_like(area), where=perim > 0)
    comp, larg, area_ret = _retangulos(geoms_m)
    along = comp / np.maximum(larg, 0.1)
    ocup = np.divide(area, area_ret, out=np.zeros_like(area), where=area_ret > 0)
    n_vert = shapely.get_num_coordinates(geoms_m).astype(float)

    fontes = list(fontes)
    one_hot = np.array([[f == c for c in FONTES_CONHECIDAS] + [f not in FONTES_CONHECIDAS]
                        for f in fontes], dtype=float).reshape(len(fontes), 4)

    viz = _vizinhanca(pts_m, area, list(tipos), np.asarray(conhecidos, dtype=bool))
    X = np.column_stack([area, perim, compac, comp, larg, along, ocup, n_vert, one_hot, viz])
    return X, list(NOMES)


def grupos_espaciais(pontos: np.ndarray) -> np.ndarray:
    """Id da célula de CELULA_GRUPO_M de cada ponto (lon/lat), para a CV espacial."""
    x = pontos[:, 0] * M_POR_GRAU * np.cos(np.radians(pontos[:, 1]))
    y = pontos[:, 1] * M_POR_GRAU
    cx = np.floor(x / CELULA_GRUPO_M).astype(np.int64)
    cy = np.floor(y / CELULA_GRUPO_M).astype(np.int64)
    return cx * 1_000_003 + cy


def bairro_mais_proximo(pontos: np.ndarray, bairros: Sequence[str | None]) -> list[str | None]:
    """Quem não tem bairro herda o do prédio com bairro mais próximo."""
    bairros = list(bairros)
    com = np.array([b is not None for b in bairros], dtype=bool)
    if not com.any() or com.all():
        return bairros
    pts_m = pontos * _escala(_lat_media(pontos))
    idx_com = np.flatnonzero(com)
    idx_sem = np.flatnonzero(~com)
    arvore = shapely.STRtree(shapely.points(pts_m[idx_com]))
    qi, qj = arvore.query_nearest(shapely.points(pts_m[idx_sem]), all_matches=False)
    saida = list(bairros)
    for a, b in zip(qi, qj):
        saida[idx_sem[a]] = bairros[idx_com[b]]
    return saida
