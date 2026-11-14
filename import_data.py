#!/usr/bin/env python3
"""Compatibilidade: entrada histórica para o pipeline de importação.

A implementação principal vive em ``importacao/`` (ver importacao/cli.py),
mas funções usadas pelos testes/código legado continuam expostas aqui.
"""

import json
import os
import re

import pymysql

from importacao.cli import main

DATA_DIR = "data/raw"
DEFAULT_ROOFTOP_GEOJSON = os.environ.get(
    "ROOFTOP_GEOJSON", "data/processed/pontos_rooftop.geojson"
)

DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "3307"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "ar120604")
DB_NAME = os.environ.get("DB_NAME", "atlas_urbano")
DB_SSL_CA = os.environ.get("DB_SSL_CA", "")


def connect_db():
    """Conexão simples (compat); carga nova usa importacao.banco."""
    kwargs = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
        "autocommit": False,
    }
    if DB_SSL_CA:
        kwargs["ssl"] = {"ca": DB_SSL_CA}
    return pymysql.connect(**kwargs)


def cnefe_address_key(props: dict) -> tuple[str, str]:
    """Retorna (id_unico, chave_composta) para correspondência com o rooftop.

    Mesma decomposição de LOGRAD_NUM usada em carregar_cnefe (algoritmo_raycast):
    "Rua X, 13 A" -> rua="Rua X", num=13, mod="A"; "Rua X, 89 SMS" -> num=89,
    mod="SMS". Sem isso endereços com modificador viram S/N indevidamente.
    """
    uid = str(props.get("COD_UNICO_ENDERECO") or "").strip()
    ln = str(props.get("LOGRAD_NUM") or "").strip()
    m = re.match(r"^(?P<rua>.+?),\s*(?P<num>\d+)(?:\s+(?P<mod>\S.*?))?\s*$", ln)
    num_str = str(props.get("NUM_ENDERECO") or "").strip()
    if not num_str or num_str in ("nan", "None"):
        num_str = m.group("num") if m else ""
    try:
        num_i = int(float(num_str)) if num_str else 0
    except (ValueError, TypeError):
        num_i = 0
    mod = str(props.get("DSC_MODIFICADOR") or "").strip()
    if (not mod or mod in ("nan", "None")) and m and m.group("mod"):
        mod = m.group("mod").strip()
    sn = (num_i == 0) or ("SN" in mod.upper())
    if sn:
        return uid, uid
    logr = str(props.get("NOM_SEGLOGR") or "").strip()
    if not logr or logr in ("nan", "None"):
        logr = (
            m.group("rua").strip()
            if m
            else ln.rsplit(",", 1)[0].strip() if "," in ln else ln
        )
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


if __name__ == "__main__":
    main()
