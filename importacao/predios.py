"""Monta a lista de prédios (Overture + fallback OSM) e liga os endereços a eles."""

from shapely import strtree

from . import config
from .espacial import match_points, polygon_of
from .fontes import load_buildings, load_osm_buildings, load_qg_points


def fonte_principal(sources):
    """(nome, licença, confiança) do 1º dataset Overture; OSM se não houver."""
    s = dict(sources[0]) if sources else {}
    return (s.get("dataset") or config.FONTE_OSM, s.get("license") or config.LICENCA_OSM,
            s.get("confidence"))


def _predios_overture():
    predios = []
    for b in load_buildings():
        g = polygon_of(b)
        if g is None:
            continue
        props = dict(b["properties"])
        sources = props.pop("sources", None) or []
        nome, licenca, conf = fonte_principal(sources)
        predios.append({"geom": g, "id_origem": b["id"], "fonte": nome,
                        "licenca": licenca, "confianca": conf,
                        "fontes": sources, "props": props,
                        "num_pav": props.get("num_floors")})
    return predios


def _predios_osm():
    osm = []
    for i, o in enumerate(load_osm_buildings()):
        g = polygon_of(o)
        if g is None:
            continue
        tags = o["properties"] or {}
        oid = str(tags.get("osm_id", i)).replace("/", "-")
        osm.append({"geom": g, "id_origem": f"osm-{oid}", "fonte": config.FONTE_OSM,
                    "licenca": config.LICENCA_OSM, "confianca": None,
                    "fontes": [{"dataset": config.FONTE_OSM}],
                    "props": {**tags, "class": tags.get("building")},
                    "num_pav": None})
    return osm


def _sem_sobreposicao(osm, predios):
    """Fallback só onde o Overture não tem polígono: o Overture já inclui
    prédios derivados do OSM, e inseri-los de novo duplicaria footprints."""
    if not (osm and predios):
        return osm
    sobrepostos = set(strtree.STRtree([p["geom"] for p in predios]).query(
        [o["geom"] for o in osm], predicate="intersects")[0].tolist())
    mantidos = [o for i, o in enumerate(osm) if i not in sobrepostos]
    print(f"  OSM: {len(osm) - len(mantidos)} descartados por sobrepor o Overture")
    return mantidos


def montar_predios():
    """Lê as fontes e devolve (predios, qg_features, assoc).

    predios: dicts com geom, id_origem, fonte, licença, confiança, props.
    assoc: {índice do endereço: (índice do prédio, METODO, DISTANCIA_M)}.
    """
    print("Lendo edificações do Overture...")
    predios = _predios_overture()
    n_overture = len(predios)
    print(f"  {n_overture} prédios")

    print("Lendo endereços do QG...")
    qg_features = load_qg_points()
    print(f"  {len(qg_features)} pontos de endereço")

    print("Relacionando endereços x prédios (espacialmente)...")
    assoc = match_points([p["geom"] for p in predios], qg_features) if predios else {}
    print(f"  {len(assoc)} endereços dentro de prédios")

    print("2ª fonte (OSM) para os endereços restantes...")
    osm = _sem_sobreposicao(_predios_osm(), predios)
    sem_predio = [qi for qi in range(len(qg_features)) if qi not in assoc]
    osm_match = match_points([p["geom"] for p in osm], qg_features, sem_predio) if osm else {}
    for qi, (oi, metodo, dist) in osm_match.items():
        assoc[qi] = (n_overture + oi, metodo, dist)
    predios.extend(osm)
    print(f"  OSM: {len(osm)} polígonos, {len(osm_match)} endereços adotados")
    return predios, qg_features, assoc
