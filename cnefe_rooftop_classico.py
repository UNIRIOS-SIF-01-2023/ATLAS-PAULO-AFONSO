"""
cnefe_rooftop.py
Atribui endereços do CNEFE 2022 a polígonos de edificação (Overture/OSM/MySQL)
e gera um ponto "rooftop" dentro de cada edificação.

Ideia: o ponto do CNEFE fica na rua, em frente ao imóvel. Usando QUADRA e FACE
do próprio CNEFE, estimamos a direção da rua (reta ajustada nos pontos da face)
e o lado "para dentro" da quadra (centro dos pontos da quadra). Daí lançamos um
leque de raios do ponto para dentro da quadra; a primeira edificação atingida
é a dona do endereço. Sem API externa, sem restrição de armazenamento.

Uso básico:
    python cnefe_rooftop.py cnefe.csv edificacoes.parquet saida.gpkg

Usando o banco do projeto como fonte de edificações:
    python cnefe_rooftop.py data/raw/qg/qg_810_endereco_Munic2924009.json mysql saida.gpkg

Modo debug (exporta cada etapa antes/depois em camadas e CSV):
    python cnefe_rooftop.py cnefe.csv mysql saida.gpkg --debug

Requer: geopandas >= 0.14, shapely >= 2.0, pandas >= 2.0, pyarrow (parquet).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import mapping, shape

try:
    import geopandas as gpd
except ImportError:  # pragma: no cover - mensagem amigável
    gpd = None


CFG = {
    # colunas do CSV do CNEFE 2022
    "col": {
        "id": "COD_UNICO_ENDERECO", "setor": "COD_SETOR", "quadra": "NUM_QUADRA",
        "face": "NUM_FACE", "tipo": "NOM_TIPO_SEGLOGR", "titulo": "NOM_TITULO_SEGLOGR",
        "logr": "NOM_SEGLOGR", "num": "NUM_ENDERECO", "mod": "DSC_MODIFICADOR",
        "especie": "COD_ESPECIE", "lat": "LATITUDE", "lon": "LONGITUDE",
        "nivel": "NV_GEO_COORD", "cep": "CEP",
    },
    "csv_sep": ";",
    "csv_encoding": "utf-8",
    "niveis_ok": [1],          # 1 = coordenada original do Censo (as demais são medianas da rua)
    "raio_max": 40.0,          # comprimento dos raios (m)
    "angulos": [-30, -20, -10, 0, 10, 20, 30],  # leque em graus
    "decaimento": 15.0,        # peso cai com a distância (m)
    "profundidade": 4.0,       # quanto entrar na edificação a partir da fachada (m)
    "fallback_raio": 25.0,     # vizinho mais próximo quando nenhum raio acerta
    "ambiguo_m": 2.0,          # centro da quadra quase em cima da face -> lado incerto
}

# Nomes alternativos vistos no GeoJSON do QG/IBGE e em exports do CNEFE.
ALIASES = {
    "id": ("COD_UNICO_ENDERECO", "id"),
    "setor": ("COD_SETOR", "setor"),
    "quadra": ("NUM_QUADRA", "quadra"),
    "face": ("NUM_FACE", "face"),
    "tipo": ("NOM_TIPO_SEGLOGR", "tipo"),
    "titulo": ("NOM_TITULO_SEGLOGR", "titulo"),
    "logr": ("NOM_SEGLOGR", "logradouro", "logr"),
    "num": ("NUM_ENDERECO", "num"),
    "mod": ("DSC_MODIFICADOR", "mod"),
    "especie": ("COD_ESPECIE", "especie"),
    "lat": ("LATITUDE", "lat"),
    "lon": ("LONGITUDE", "lon"),
    "nivel": ("NV_GEO_COORD", "nivel"),
    "cep": ("CEP", "cep"),
}


# ---------------------------------------------------------------- debug
def _wkt(geoms):
    return shapely.to_wkt(geoms, rounding_precision=7)


def _spearman(x, y) -> float:
    """Correlação de Spearman sem depender de scipy (rank + Pearson)."""
    rx = pd.Series(np.asarray(x, dtype=float)).rank()
    ry = pd.Series(np.asarray(y, dtype=float)).rank()
    if rx.nunique() < 2 or ry.nunique() < 2:
        return float("nan")
    return float(rx.corr(ry))


def _layer_para_csv(gdf: "gpd.GeoDataFrame") -> pd.DataFrame:
    """DataFrame com todos os atributos + WKT + lon/lat (centro da geometria)."""
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    geoms = gdf.geometry.to_numpy()
    df["wkt"] = _wkt(geoms)
    if gdf.crs is not None:
        w = gdf.to_crs(4326)
        centros = shapely.centroid(w.geometry.to_numpy())
        df["lon"] = shapely.get_x(centros)
        df["lat"] = shapely.get_y(centros)
    return df


class Debug:
    """Coleta camadas/tabelas de cada etapa e grava GPKG multi-camada + CSVs."""

    def __init__(self, gpkg: str | Path | None = None, csv_dir: str | Path | None = None):
        self.gpkg = Path(gpkg) if gpkg else None
        self.csv_dir = Path(csv_dir) if csv_dir else None
        self.layers: dict[str, "gpd.GeoDataFrame"] = {}
        self.tables: dict[str, pd.DataFrame] = {}
        self.resumo: list[dict] = []

    def layer(self, name: str, gdf: "gpd.GeoDataFrame") -> None:
        self.layers[name] = gdf

    def table(self, name: str, df: pd.DataFrame) -> None:
        self.tables[name] = df

    def etapa(self, nome: str, **campos) -> None:
        self.resumo.append({"etapa": nome, **campos})

    def escrever(self) -> dict:
        escritos = {"gpkg": None, "csv_dir": None, "layers": [], "tables": []}

        if self.gpkg is not None and self.layers:
            self.gpkg.parent.mkdir(parents=True, exist_ok=True)
            if self.gpkg.exists():
                self.gpkg.unlink()
            primeiro = True
            for nome, gdf in self.layers.items():
                if gdf is None or len(gdf) == 0:
                    continue
                g = gdf.to_crs(4326) if gdf.crs is not None else gdf
                g.to_file(
                    self.gpkg, layer=nome, driver="GPKG",
                    mode="w" if primeiro else "a",
                )
                primeiro = False
                escritos["layers"].append(nome)
            escritos["gpkg"] = str(self.gpkg)

        if self.csv_dir is not None:
            self.csv_dir.mkdir(parents=True, exist_ok=True)
            for nome, gdf in self.layers.items():
                if gdf is None or len(gdf) == 0:
                    continue
                _layer_para_csv(gdf).to_csv(
                    self.csv_dir / f"{nome}.csv",
                    sep=";", index=False, encoding="utf-8-sig",
                )
                escritos["tables"].append(nome)
            for nome, df in self.tables.items():
                df.to_csv(
                    self.csv_dir / f"{nome}.csv",
                    sep=";", index=False, encoding="utf-8-sig",
                )
                escritos["tables"].append(nome)
            if self.resumo:
                pd.DataFrame(self.resumo).to_csv(
                    self.csv_dir / "resumo_etapas.csv",
                    sep=";", index=False, encoding="utf-8-sig",
                )
                escritos["tables"].append("resumo_etapas")
            escritos["csv_dir"] = str(self.csv_dir)

        return escritos

    def imprimir(self) -> None:
        print("\n=== Resumo do debug ===")
        for linha in self.resumo:
            print(" | ".join(f"{k}={v}" for k, v in linha.items()))
        if self.gpkg is not None:
            print(f"GPKG: {self.gpkg}")
        if self.csv_dir is not None:
            print(f"CSVs: {self.csv_dir}")


# ---------------------------------------------------------------- carga
def _carregar_edificacoes_mysql() -> "gpd.GeoDataFrame":
    """Polígonos da tabela `buildings` (exclui as bolinhas QG/CNEFE)."""
    try:
        from backend import db
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Para usar 'mysql' como fonte, rode o script na raiz do projeto "
            "(backend/ acessível) ou informe um arquivo GeoJSON/GPKG/Parquet."
        ) from exc
    with db.connection() as conn, conn.cursor() as cur:
        # A tabela tem uma linha por endereço, repetindo o polígono do prédio.
        # Deduplicamos por geometria para o algoritmo não ver o mesmo prédio
        # várias vezes (o que quebraria a ordem das faces).
        cur.execute(
            "SELECT id, ST_AsGeoJSON(geom) AS geometry_json, tipo, fonte "
            "FROM buildings WHERE fonte IS NULL OR fonte <> 'QG/CNEFE'"
        )
        rows = cur.fetchall()
    registros, geoms, vistos = [], [], set()
    for row in rows:
        geom = shape(json.loads(row["geometry_json"]))
        if geom.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        chave = shapely.to_wkb(geom, hex=True, include_srid=False)
        if chave in vistos:
            continue
        vistos.add(chave)
        registros.append({"id": row.get("id"), "tipo": row.get("tipo"), "fonte": row.get("fonte")})
        geoms.append(geom)
    return gpd.GeoDataFrame(registros, geometry=geoms, crs=4326)


def carregar_edificacoes(path: str | Path) -> "gpd.GeoDataFrame":
    if gpd is None:
        raise RuntimeError("geopandas não está instalado; rode: pip install geopandas")
    origem = str(path).strip().lower()
    if origem in {"mysql", "db", "banco"}:
        g = _carregar_edificacoes_mysql()
    elif origem.endswith(".parquet"):
        g = gpd.read_parquet(path)
    else:
        g = gpd.read_file(path)
    g = g[g.geometry.notna() & g.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if g.crs is None:
        g = g.set_crs(4326)
    g = g.reset_index(drop=True)
    g["bld_idx"] = np.arange(len(g))
    return g


def _ler_cnefe_geojson(path: str | Path) -> pd.DataFrame:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    feats = doc.get("features") if doc.get("type") == "FeatureCollection" else [doc]
    registros = []
    for feat in feats or []:
        props = dict(feat.get("properties") or {})
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if coords and isinstance(coords, (list, tuple)) and len(coords) >= 2:
            props.setdefault("LONGITUDE", coords[0])
            props.setdefault("LATITUDE", coords[1])
        registros.append(props)
    return pd.DataFrame(registros)


def carregar_cnefe(path: str | Path, bbox4326) -> pd.DataFrame:
    c = CFG["col"]
    origem = str(path).lower()
    if origem.endswith((".json", ".geojson")):
        df = _ler_cnefe_geojson(path)
    else:
        df = pd.read_csv(
            path, sep=CFG["csv_sep"], encoding=CFG["csv_encoding"],
            dtype=str, usecols=lambda k: k in c.values(),
        )

    # Garante todas as colunas canônicas, aceitando os nomes alternativos.
    for chave, coluna in c.items():
        if coluna not in df.columns:
            achada = next((a for a in ALIASES.get(chave, ()) if a in df.columns), None)
            df[coluna] = df[achada] if achada is not None else ""

    # O GeoJSON do QG traz rua e número juntos em LOGRAD_NUM ("Rua X, 123").
    if "LOGRAD_NUM" in df.columns:
        ln = df["LOGRAD_NUM"].astype(str).str.strip()
        vazio_num = df[c["num"]].astype(str).str.strip().isin(["", "nan", "None"])
        if vazio_num.any():
            df.loc[vazio_num, c["num"]] = (
                ln[vazio_num].str.extract(r",\s*([0-9]+)\s*$")[0]
            )
        vazio_logr = df[c["logr"]].astype(str).str.strip().isin(["", "nan", "None"])
        if vazio_logr.any():
            rua = ln.str.rsplit(",", n=1).str[0].str.strip()
            df.loc[vazio_logr, c["logr"]] = rua[vazio_logr]

    for k in ("lat", "lon"):
        df[c[k]] = pd.to_numeric(
            df[c[k]].astype(str).str.replace(",", "."), errors="coerce"
        )
    if c["nivel"] not in df.columns or df[c["nivel"]].isna().all():
        df[c["nivel"]] = 1
    df[c["nivel"]] = pd.to_numeric(df[c["nivel"]], errors="coerce")

    minx, miny, maxx, maxy = bbox4326
    m = 0.001  # ~100 m de margem
    df = df[df[c["lon"]].between(minx - m, maxx + m) & df[c["lat"]].between(miny - m, maxy + m)]
    df = df[df[c["nivel"]].isin(CFG["niveis_ok"])]
    return df.fillna({c[k]: "" for k in ("tipo", "titulo", "logr", "mod", "cep")})


# ---------------------------------------------------------------- endereço-base
def agregar_enderecos(df, crs):
    """Apartamentos do mesmo prédio viram um único endereço-base (média dos pontos)."""
    c = CFG["col"]
    df = df.copy()
    df["num_i"] = pd.to_numeric(df[c["num"]], errors="coerce").fillna(0).astype(int)
    df["sn"] = (df["num_i"] == 0) | df[c["mod"]].astype(str).str.upper().str.contains("SN")
    df["quadra_key"] = df[c["setor"]].astype(str) + "_" + df[c["quadra"]].astype(str)
    df["face_key"] = df["quadra_key"] + "_" + df[c["face"]].astype(str)
    # sem número: cada registro é um endereço distinto
    df["addr_key"] = np.where(
        df["sn"], df[c["id"]].astype(str),
        df["face_key"] + "|" + df[c["logr"]].astype(str) + "|" + df["num_i"].astype(str) + "|" + df[c["mod"]].astype(str))

    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[c["lon"]], df[c["lat"]]), crs=4326).to_crs(crs)
    pts["x"], pts["y"] = pts.geometry.x, pts.geometry.y

    agg = pts.groupby("addr_key").agg(
        x=("x", "mean"), y=("y", "mean"), unidades=("x", "size"),
        quadra_key=("quadra_key", "first"), face_key=("face_key", "first"),
        tipo=(c["tipo"], "first"), titulo=(c["titulo"], "first"), logr=(c["logr"], "first"),
        num=("num_i", "first"), mod=(c["mod"], "first"), cep=(c["cep"], "first"),
        especie=(c["especie"], lambda s: s.mode().iat[0] if len(s.mode()) else ""),
        sn=("sn", "first"),
    ).reset_index()
    rua = (agg["tipo"] + " " + agg["titulo"] + " " + agg["logr"]).str.split().str.join(" ")
    agg["endereco"] = rua + ", " + np.where(agg["sn"], "S/N", agg["num"].astype(str) + agg["mod"].astype(str).str.strip())
    return agg


# ---------------------------------------------------------------- direção
def calcular_normais(a, crs=None, debug: Debug | None = None):
    """Normal 'para dentro da quadra' de cada endereço, a partir da reta da face."""
    qc = a.groupby("quadra_key")[["x", "y"]].transform("mean").to_numpy()

    def tangente(g):
        # reta só é confiável com >=3 pontos e extensão razoável; senão usa o centro da quadra
        if len(g) < 3:
            return pd.Series({"tx": np.nan, "ty": np.nan})
        p = g[["x", "y"]].to_numpy()
        p = p - p.mean(axis=0)
        sv, vt = np.linalg.svd(p, full_matrices=False)[1:]
        if sv[0] / np.sqrt(len(p)) < 5.0 or sv[1] > 0.5 * sv[0]:
            return pd.Series({"tx": np.nan, "ty": np.nan})
        t = vt[0]
        return pd.Series({"tx": t[0], "ty": t[1]})

    tg = a.groupby("face_key")[["x", "y"]].apply(tangente)
    t = a[["face_key"]].join(tg, on="face_key")[["tx", "ty"]].to_numpy()
    p = a[["x", "y"]].to_numpy()
    v = qc - p                                   # do ponto ao centro da quadra
    n = np.column_stack([-t[:, 1], t[:, 0]])     # perpendicular à face
    dot = (n * v).sum(axis=1)
    n[dot < 0] *= -1
    ambiguo = np.abs(dot) < CFG["ambiguo_m"]

    # face sem reta (1 ponto): usa direção ao centro da quadra
    sem_t = np.isnan(t[:, 0])
    norm = np.linalg.norm(v, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        n[sem_t] = v[sem_t] / norm[sem_t, None]
    ambiguo[sem_t] = norm[sem_t] < CFG["ambiguo_m"]
    n[ambiguo & sem_t] = np.nan
    a["nx"], a["ny"], a["tx"], a["ty"], a["ambiguo"] = n[:, 0], n[:, 1], t[:, 0], t[:, 1], ambiguo

    if debug is not None:
        ok = ~np.isnan(n[:, 0])
        pp = p[ok]
        nn = n[ok]
        comprimento = min(CFG["raio_max"], 20.0)
        linhas = shapely.linestrings(np.stack([pp, pp + nn * comprimento], axis=1))
        gdf = gpd.GeoDataFrame(
            a.loc[ok, ["addr_key", "endereco", "face_key", "x", "y", "nx", "ny", "ambiguo"]].reset_index(drop=True),
            geometry=linhas,
            crs=crs,
        )
        debug.layer("normais", gdf)
        debug.etapa(
            "normais",
            enderecos=len(a), com_normal=int(ok.sum()),
            sem_normal=int((~ok).sum()), ambiguos=int(np.nansum(ambiguo)),
            face_sem_reta=int(sem_t.sum()),
        )
    return a


# ---------------------------------------------------------------- raios
def lancar_raios(a, blds, debug: Debug | None = None):
    ang_deg = np.array(CFG["angulos"], dtype=float)
    ang = np.deg2rad(ang_deg)
    peso_ang = np.cos(ang) ** 2
    ok = ~np.isnan(a["nx"].to_numpy())
    ai = np.flatnonzero(ok)
    p = a.loc[ok, ["x", "y"]].to_numpy()
    n = a.loc[ok, ["nx", "ny"]].to_numpy()
    amb = a.loc[ok, "ambiguo"].to_numpy()
    crs = blds.crs

    vazio = pd.DataFrame(columns=["ray", "addr", "bld", "score", "dist", "dx", "dy", "ox", "oy", "centro"])
    if len(ai) == 0:
        if debug is not None:
            debug.etapa("raios", raios=0, enderecos=0, ambiguos_com_raio_duplo=0)
            debug.etapa("hits", raios=0, intersecoes=0, escolhidos=0)
        return vazio, np.array([])

    origens, dirs, addr, w, lado, angulos = [], [], [], [], [], []
    for sinal in (1, -1):
        sel = np.ones(len(ai), bool) if sinal == 1 else amb   # lado oposto só se ambíguo
        for k, th in enumerate(ang):
            c, s = np.cos(th), np.sin(th)
            d = sinal * np.column_stack([n[:, 0] * c - n[:, 1] * s, n[:, 0] * s + n[:, 1] * c])
            origens.append(p[sel]); dirs.append(d[sel]); addr.append(ai[sel])
            w.append(np.full(sel.sum(), peso_ang[k] * (1.0 if sinal == 1 else 0.5)))
            lado.append(np.full(sel.sum(), sinal))
            angulos.append(np.full(sel.sum(), ang_deg[k]))
    o, d = np.vstack(origens), np.vstack(dirs)
    addr, w = np.concatenate(addr), np.concatenate(w)
    lado, angulos = np.concatenate(lado), np.concatenate(angulos)
    raios = shapely.linestrings(np.stack([o, o + d * CFG["raio_max"]], axis=1))

    geoms = blds.geometry.to_numpy()
    r_idx, b_idx = STRtree(geoms).query(raios, predicate="intersects")

    if debug is not None:
        meta_raios = pd.DataFrame({
            "ray": np.arange(len(raios)), "addr": addr, "side": lado,
            "angle_deg": angulos, "weight": w,
            "ox": o[:, 0], "oy": o[:, 1], "dx": d[:, 0], "dy": d[:, 1],
        })
        # índice do endereço -> chave/endereço legível
        nomes = a[["addr_key", "endereco"]].reset_index(drop=True)
        meta_raios = meta_raios.join(nomes, on="addr")
        debug.layer("raios", gpd.GeoDataFrame(meta_raios, geometry=raios, crs=crs))
        debug.etapa("raios", raios=len(raios), enderecos=len(ai),
                    ambiguos_com_raio_duplo=int(amb.sum()))

    if len(r_idx) == 0:
        if debug is not None:
            debug.layer("hits_todos", gpd.GeoDataFrame(
                {"ray": [], "bld": [], "dist": []}, geometry=[], crs=crs))
            debug.layer("hits_escolhidos", gpd.GeoDataFrame(
                {"ray": [], "bld": [], "dist": []}, geometry=[], crs=crs))
            debug.etapa("hits", raios=0, intersecoes=0, escolhidos=0)
        return vazio, raios

    inter = shapely.intersection(raios[r_idx], geoms[b_idx])
    dist = shapely.distance(shapely.points(o[r_idx]), inter)
    todos = pd.DataFrame({"ray": r_idx, "bld": b_idx, "dist": dist})
    todos["addr"] = addr[todos["ray"].to_numpy()]
    todos["score"] = w[todos["ray"].to_numpy()] * np.exp(-todos["dist"] / CFG["decaimento"])
    todos["bld_id"] = blds["id"].to_numpy()[todos["bld"].to_numpy()] if "id" in blds else ""
    todos["centro"] = w[todos["ray"].to_numpy()] >= 0.99

    if debug is not None:
        nomes_hit = a[["addr_key", "endereco"]].reset_index(drop=True)
        gdf_todos = gpd.GeoDataFrame(
            todos.join(nomes_hit, on="addr"), geometry=inter, crs=crs)
        debug.layer("hits_todos", gdf_todos)

    hits = todos.loc[todos.groupby("ray")["dist"].idxmin()].copy()   # primeira edificação = oclusão
    r = hits["ray"].to_numpy()
    hits["dx"], hits["dy"], hits["ox"], hits["oy"] = d[r, 0], d[r, 1], o[r, 0], o[r, 1]
    hits["centro"] = (w[r] >= 0.99)

    if debug is not None:
        nomes_hit = a[["addr_key", "endereco"]].reset_index(drop=True)
        # geometria do hit escolhido: segmento do ponto de origem até a interseção
        ponto_hit = shapely.line_interpolate_point(
            raios[r], shapely.distance(shapely.points(o[r]), inter[np.asarray(hits.index)]))
        linha = shapely.linestrings(np.stack([o[r], shapely.get_coordinates(ponto_hit)], axis=1))
        debug.layer("hits_escolhidos", gpd.GeoDataFrame(
            hits.join(nomes_hit, on="addr").reset_index(drop=True),
            geometry=linha, crs=crs))
        debug.etapa("hits", raios=len(raios), intersecoes=len(todos),
                    escolhidos=len(hits), enderecos_com_hit=int(hits["addr"].nunique()))
    return hits, raios


# ---------------------------------------------------------------- atribuição
def atribuir(a, blds, debug: Debug | None = None):
    geoms = blds.geometry.to_numpy()
    hits, _ = lancar_raios(a, blds, debug=debug)

    a["bld"] = -1
    a["metodo"] = "sem_match"
    a["confianca"] = "nenhuma"
    rx = np.full(len(a), np.nan); ry = np.full(len(a), np.nan)

    if len(hits):
        g = hits.groupby(["addr", "bld"])
        tab = g.agg(score=("score", "sum"), dist=("dist", "min"), centro=("centro", "any")).reset_index()
        tot = tab.groupby("addr")["score"].transform("sum")
        tab["share"] = tab["score"] / tot
        # esquinas: uma edificação não pode ficar com endereços de faces diferentes;
        # guloso por score (mesma face pode compartilhar, a etapa de ordem resolve)
        tab["face"] = a["face_key"].to_numpy()[tab["addr"].to_numpy()]
        if debug is not None:
            nomes_tab = a[["addr_key", "endereco"]].reset_index(drop=True)
            debug.layer("candidatos_por_endereco", gpd.GeoDataFrame(
                tab.join(nomes_tab, on="addr").reset_index(drop=True),
                geometry=[geoms[i] for i in tab["bld"].to_numpy()],
                crs=blds.crs,
            ))
            debug.etapa("candidatos", pares=len(tab), enderecos=int(tab["addr"].nunique()))
        tab = tab.sort_values("score", ascending=False)
        dono, feito, sel = {}, set(), []
        for r in tab[["addr", "bld", "face"]].itertuples(index=True):
            if r.addr in feito:
                continue
            f = dono.get(r.bld)
            if f is None or f == r.face:
                dono[r.bld] = r.face; feito.add(r.addr); sel.append(r.Index)
        best = tab.loc[sel]
        # raio mais forte dentro da edificação vencedora -> ponto na fachada
        br = hits.merge(best[["addr", "bld"]], on=["addr", "bld"])
        br = br.loc[br.groupby("addr")["score"].idxmax()].set_index("addr")
        best = best.set_index("addr").join(br[["dx", "dy", "ox", "oy"]])
        idx = best.index.to_numpy()
        a.loc[idx, "bld"] = best["bld"].to_numpy()
        a.loc[idx, "metodo"] = "raio"
        alta = best["centro"] & (best["dist"] <= 25) & (best["share"] >= 0.6) & ~a.loc[idx, "ambiguo"].to_numpy()
        a.loc[idx, "confianca"] = np.where(alta, "alta", "media")
        prof = best["dist"] + CFG["profundidade"]
        rx[idx] = best["ox"] + best["dx"] * prof
        ry[idx] = best["oy"] + best["dy"] * prof

    # fallback: vizinho mais próximo no lado de dentro da quadra
    falta = np.flatnonzero(a["bld"].to_numpy() < 0)
    if len(falta):
        pts = shapely.points(a.loc[falta, ["x", "y"]].to_numpy())
        q_idx, b_idx = STRtree(geoms).query_nearest(pts, max_distance=CFG["fallback_raio"], all_matches=False)
        # -1 = nenhum vizinho dentro do raio máximo
        validos = b_idx >= 0
        q_idx, b_idx = q_idx[validos], b_idx[validos]
        if len(q_idx):
            cen = shapely.get_coordinates(shapely.centroid(geoms[b_idx]))
            ai = falta[q_idx]
            nv = a.loc[ai, ["nx", "ny"]].to_numpy()
            lado_ok = np.isnan(nv[:, 0]) | (((cen - a.loc[ai, ["x", "y"]].to_numpy()) * nv).sum(axis=1) > 0)
            ai, b_idx = ai[lado_ok], b_idx[lado_ok]
            a.loc[ai, "bld"] = b_idx
            a.loc[ai, "metodo"] = "vizinho"
            a.loc[ai, "confianca"] = "baixa"

    # id legível da edificação (o índice interno `bld` não serve para conferência)
    a["bld_id"] = ""
    if "id" in blds.columns:
        tem_bld = a["bld"].to_numpy() >= 0
        a.loc[tem_bld, "bld_id"] = (
            blds["id"].to_numpy()[a.loc[tem_bld, "bld"].to_numpy()].astype(str)
        )

    # ponto rooftop: na fachada + profundidade; se cair fora, ponto interno do polígono
    tem = a["bld"].to_numpy() >= 0
    poly = geoms[a.loc[tem, "bld"].to_numpy()]
    cand = shapely.points(np.column_stack([rx[tem], ry[tem]]))
    dentro = shapely.contains(poly, cand) & ~np.isnan(rx[tem])
    roof = np.where(dentro, cand, shapely.point_on_surface(poly))
    a["geometry"] = None
    a.loc[tem, "geometry"] = roof
    gdf = gpd.GeoDataFrame(a, geometry="geometry", crs=blds.crs)

    if debug is not None:
        debug.etapa(
            "atribuicao",
            enderecos=len(gdf),
            com_bld=int((gdf["bld"] >= 0).sum()),
            sem_bld=int((gdf["bld"] < 0).sum()),
            raio=int((gdf["metodo"] == "raio").sum()),
            vizinho=int((gdf["metodo"] == "vizinho").sum()),
            alta=int((gdf["confianca"] == "alta").sum()),
            media=int((gdf["confianca"] == "media").sum()),
            baixa=int((gdf["confianca"] == "baixa").sum()),
        )
    return gdf, hits


# ---------------------------------------------------------------- ordem da numeração
def _dp_monotonico(s, lo, hi, largo, cap, pen):
    """Atribui endereços (ordenados por número) a edificações (ordenadas ao longo
    da face) com índice não decrescente. Custo = distância de s ao intervalo
    [lo, hi] da edificação (limitado a cap); repetir edificação estreita custa pen."""
    n, m = len(s), len(lo)
    c = np.minimum(np.maximum(0, np.maximum(lo[None] - s[:, None], s[:, None] - hi[None])), cap)
    rep = np.where(largo, 0.0, pen)
    dp = np.full((n, m), np.inf); bk = np.zeros((n, m), int)
    dp[0] = c[0]
    for i in range(1, n):
        prev = dp[i - 1]
        pmin = np.minimum.accumulate(np.concatenate([[np.inf], prev[:-1]]))
        parg = np.concatenate([[0], np.array([np.argmin(prev[:k]) if k else 0 for k in range(1, m)])])
        stay = prev + rep
        usa_stay = stay <= pmin
        dp[i] = c[i] + np.where(usa_stay, stay, pmin)
        bk[i] = np.where(usa_stay, np.arange(m), parg)
    k = np.empty(n, int); k[-1] = int(np.argmin(dp[-1]))
    for i in range(n - 1, 0, -1):
        k[i - 1] = bk[i, k[i]]
    return k, c[np.arange(n), k]


def refinar_por_ordem(a, blds, hits, cap=8.0, pen=6.0, debug: Debug | None = None):
    """Corrige o ruído do GPS usando a ordem da numeração em cada face."""
    geoms = blds.geometry.to_numpy()
    a["dp"] = False
    if hits is None or len(hits) == 0:
        return a
    # candidatas: atingidas por qualquer raio de algum endereço da face
    fr = hits.merge(a[["face_key"]], left_on="addr", right_index=True)
    frente = fr.groupby("face_key")["bld"].unique()

    diag_faces, eventos = [], []
    for fk, idx in a.groupby("face_key").groups.items():
        g = a.loc[idx]
        g = g[~g["sn"] & g["tx"].notna()]
        if len(g) < 3 or fk not in frente.index:
            continue
        t = g[["tx", "ty"]].iloc[0].to_numpy(); n_ = g[["nx", "ny"]].iloc[0].to_numpy()
        s_obs = g["x"].to_numpy() * t[0] + g["y"].to_numpy() * t[1]
        num = g["num"].to_numpy().astype(float)
        rho = _spearman(s_obs, num)
        if not np.isfinite(rho) or abs(rho) < 0.5:
            continue
        # numeração métrica/regular: suaviza a posição com a reta número -> posição
        b1, b0 = np.polyfit(num, s_obs, 1)
        fit = b0 + b1 * num
        r2 = 1 - ((s_obs - fit) ** 2).sum() / max(((s_obs - s_obs.mean()) ** 2).sum(), 1e-9)
        w = np.clip((r2 - 0.8) / 0.2, 0, 1) * 0.5
        s = (1 - w) * s_obs + w * fit

        bl = np.array(frente[fk]); cs = shapely.get_coordinates(shapely.boundary(geoms[bl]), return_index=True)
        proj = cs[0] @ t
        p0 = g[["x", "y"]].to_numpy().mean(axis=0)
        prof = (cs[0] - p0) @ n_                       # profundidade a partir da linha da face
        lo = np.array([proj[cs[1] == k].min() for k in range(len(bl))])
        hi = np.array([proj[cs[1] == k].max() for k in range(len(bl))])
        dmin = np.array([prof[cs[1] == k].min() for k in range(len(bl))])
        # primeira fileira (sem edículas de fundo nem o outro lado da rua), dentro da extensão da face
        dmin_valido = np.where(dmin > -2, dmin, np.inf)
        keep = (dmin > -2) & (dmin <= np.nanmin(dmin_valido) + 6) \
            & (hi >= s_obs.min() - 10) & (lo <= s_obs.max() + 10)
        diag_faces.append({
            "face_key": fk, "enderecos_face": len(g), "rho": rho, "r2": r2,
            "peso_suavizacao": float(w.mean()), "candidatas": len(bl),
            "mantidas": int(keep.sum()), "aplicou": bool(keep.sum() >= 2),
        })
        if keep.sum() < 2:
            continue
        bl, lo, hi = bl[keep], lo[keep], hi[keep]
        ordem_b = np.argsort((lo + hi) / 2); bl, lo, hi = bl[ordem_b], lo[ordem_b], hi[ordem_b]
        larg = hi - lo; largo = larg > 1.8 * np.median(larg)

        ordem_a = np.argsort(num * np.sign(rho), kind="stable")
        k, custo = _dp_monotonico(s[ordem_a], lo, hi, largo, cap, pen)
        aid = g.index.to_numpy()[ordem_a]
        ok = custo < cap
        for ai, bi, si, l, h, cu in zip(aid[ok], bl[k[ok]], s[ordem_a][ok], lo[k[ok]], hi[k[ok]], custo[ok]):
            bld_antes = a.at[ai, "bld"]
            if bld_antes == bi:
                continue
            # ponto rooftop: na posição corrigida, entrando na edificação pela face
            sc = np.clip(si, l + 1, h - 1) if h - l > 2 else (l + h) / 2
            q = np.array([a.at[ai, "x"], a.at[ai, "y"]]) + t * (sc - (a.at[ai, "x"] * t[0] + a.at[ai, "y"] * t[1]))
            ray = shapely.linestrings([q - n_ * 10, q + n_ * CFG["raio_max"]])
            inter = shapely.intersection(ray, geoms[bi])
            if inter.is_empty:
                pt = shapely.point_on_surface(geoms[bi])
            else:
                d = shapely.distance(shapely.points(q - n_ * 10), inter)
                pt = shapely.points(q - n_ * 10 + n_ * (d + CFG["profundidade"]))
                if not shapely.contains(geoms[bi], pt):
                    pt = shapely.point_on_surface(geoms[bi])
            a.at[ai, "bld"] = bi
            a.at[ai, "geometry"] = pt
            a.at[ai, "metodo"] = "raio+ordem"
            a.at[ai, "confianca"] = "alta" if cu < 2 else "media"
            a.at[ai, "dp"] = True
            eventos.append({
                "addr_key": a.at[ai, "addr_key"], "endereco": a.at[ai, "endereco"],
                "face_key": fk, "bld_antes": int(bld_antes), "bld_depois": int(bi),
                "custo": float(cu), "posicao_corrigida": float(si),
            })
    if debug is not None:
        debug.table("faces_ordem", pd.DataFrame(diag_faces))
        debug.table("eventos_ordem", pd.DataFrame(eventos))
        debug.etapa(
            "ordem",
            faces_avaliadas=len(diag_faces),
            faces_aplicadas=int(sum(1 for d in diag_faces if d["aplicou"])),
            enderecos_movidos=len(eventos),
        )
    return a


# ---------------------------------------------------------------- checagem de ordem
def checar_ordem(a):
    """Numeração deve crescer ao longo da face; faces fora disso vão para revisão."""
    m = (a["bld"] >= 0) & ~a["sn"] & a["tx"].notna() & a.geometry.notna()
    s = a[m].copy()
    s["pos"] = s.geometry.x * s["tx"] + s.geometry.y * s["ty"]
    rho = s.groupby("face_key").apply(
        lambda g: abs(_spearman(g["pos"], g["num"])) if len(g) >= 4 else np.nan,
        include_groups=False)
    a["face_rho"] = a["face_key"].map(rho)
    a["revisar_face"] = a["face_rho"] < 0.7
    return a


# ---------------------------------------------------------------- antes/depois
def _snapshot(a):
    """Cópia leve com o ponto de cada endereço (street/candidato ou rooftop)."""
    geoms = a.geometry.to_numpy()
    px = shapely.get_x(geoms)
    py = shapely.get_y(geoms)
    df = a[["addr_key", "endereco", "bld", "metodo", "confianca", "x", "y"]].copy()
    df["px"] = np.where(np.isnan(px), a["x"].to_numpy(), px)
    df["py"] = np.where(np.isnan(py), a["y"].to_numpy(), py)
    df["tem_ponto"] = ~np.isnan(px)
    return df


def _gdf_snapshot(snap, crs, xcol="px", ycol="py"):
    return gpd.GeoDataFrame(
        snap.copy(),
        geometry=shapely.points(snap[[xcol, ycol]].to_numpy()),
        crs=crs,
    )


def comparar_antes_depois(antes, depois):
    """Diff endereço a endereço: prédio, método, confiança e deslocamento do ponto."""
    a = antes.rename(columns={c: f"{c}_antes" for c in antes.columns if c != "addr_key"})
    d = depois.rename(columns={c: f"{c}_depois" for c in depois.columns if c != "addr_key"})
    j = a.merge(d, on="addr_key", how="outer")
    j["mudou_bld"] = j["bld_antes"].fillna(-1) != j["bld_depois"].fillna(-1)
    j["mudou_metodo"] = j["metodo_antes"].fillna("") != j["metodo_depois"].fillna("")
    j["mudou_confianca"] = j["confianca_antes"].fillna("") != j["confianca_depois"].fillna("")
    j["deslocamento_m"] = np.hypot(
        j["px_depois"].to_numpy() - j["px_antes"].to_numpy(),
        j["py_depois"].to_numpy() - j["py_antes"].to_numpy(),
    )
    j["mudou_ponto"] = j["deslocamento_m"] > 0.01
    return j


def _registrar_debug_diff(debug: Debug, antes, depois, diff, crs):
    debug.layer("atribuicao_antes", _gdf_snapshot(antes, crs))
    debug.layer("atribuicao_depois", _gdf_snapshot(depois, crs))
    alterados = diff[diff["mudou_bld"] | diff["mudou_ponto"]].copy()
    if len(alterados):
        pts = np.stack([
            alterados[["px_antes", "py_antes"]].to_numpy(),
            alterados[["px_depois", "py_depois"]].to_numpy(),
        ], axis=1)
        debug.layer("alterados", gpd.GeoDataFrame(
            alterados.reset_index(drop=True), geometry=shapely.linestrings(pts), crs=crs))
    debug.etapa(
        "depois",
        enderecos=len(diff),
        mudou_bld=int(diff["mudou_bld"].sum()),
        mudou_ponto=int(diff["mudou_ponto"].sum()),
        deslocamento_medio_m=round(float(diff["deslocamento_m"].mean()), 2),
        deslocamento_max_m=round(float(diff["deslocamento_m"].max()), 2),
    )


# ---------------------------------------------------------------- saída por edificação
def resumir_edificacoes(a, blds):
    m = a[a["bld"] >= 0].sort_values(["bld", "unidades"], ascending=[True, False])
    r = m.groupby("bld").agg(
        endereco=("endereco", "first"), cep=("cep", "first"), especie=("especie", "first"),
        n_enderecos=("endereco", "size"), unidades=("unidades", "sum"),
        todos_enderecos=("endereco", lambda s: " | ".join(s)),
        confianca=("confianca", "first"))
    return blds.join(r, on="bld_idx")


# ---------------------------------------------------------------- web
def escrever_web_geojson(a, path):
    """GeoJSON 4326 só com os pontos finais, para o backend/frontend servir."""
    m = a[a["bld"] >= 0].copy()
    if len(m) == 0:
        doc = {"type": "FeatureCollection", "name": "cnefe_rooftop", "features": []}
    else:
        g = gpd.GeoDataFrame(m, geometry="geometry", crs=a.crs).to_crs(4326)
        feats = []
        for _, row in g.iterrows():
            # A camada web carrega só o necessário para o mapa; o antes/depois
            # completo fica no GPKG de debug e nos CSVs.
            props = {
                "addr_key": row["addr_key"],
                "endereco": row["endereco"],
                "cep": row["cep"],
                "especie": row["especie"],
                "unidades": int(row["unidades"]),
                "bld": int(row["bld"]),
                "bld_id": row.get("bld_id", ""),
                "metodo": row["metodo"],
                "confianca": row["confianca"],
                "revisar_face": bool(row["revisar_face"]),
                "mudou_bld": bool(row["mudou_bld"]) if pd.notna(row["mudou_bld"]) else False,
                "mudou_ponto": bool(row["mudou_ponto"]) if pd.notna(row["mudou_ponto"]) else False,
                "deslocamento_m": 0.0 if pd.isna(row["deslocamento_m"]) else round(float(row["deslocamento_m"]), 2),
            }
            feats.append({
                "type": "Feature",
                "id": row["addr_key"],
                "geometry": mapping(row.geometry),
                "properties": props,
            })
        doc = {"type": "FeatureCollection", "name": "cnefe_rooftop", "features": feats}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def escrever_web_poligonos(a, blds, path):
    """GeoJSON 4326 dos prédios que receberam pontos, com o resumo do cruzamento."""
    m = a[a["bld"] >= 0].copy()
    if len(m) == 0:
        doc = {"type": "FeatureCollection", "name": "cnefe_rooftop_poligonos", "features": []}
    else:
        def resumo(g):
            confs = set(g["confianca"].astype(str))
            conf = ("alta" if "alta" in confs else
                    "media" if "media" in confs else
                    "baixa" if "baixa" in confs else "nenhuma")
            return pd.Series({
                "n_enderecos": int(len(g)),
                "n_unidades": int(g["unidades"].sum()),
                "confianca": conf,
                "mudou": bool(g["mudou_bld"].fillna(False).any()
                              or g["mudou_ponto"].fillna(False).any()),
                "revisar": bool(g["revisar_face"].fillna(False).any()),
                "metodos": " | ".join(sorted(set(g["metodo"].astype(str)))),
                "enderecos": " | ".join(g["endereco"].astype(str).head(6)),
            })
        r = m.groupby("bld").apply(resumo, include_groups=False).reset_index()
        g = blds[["bld_idx", "id", "geometry"]].merge(r, left_on="bld_idx", right_on="bld")
        g = gpd.GeoDataFrame(g, geometry="geometry", crs=blds.crs).to_crs(4326)
        # A camada web não precisa do detalhe de 1 m do Overture.
        g["geometry"] = g.geometry.simplify(0.00002, preserve_topology=True)
        feats = []
        for _, row in g.iterrows():
            props = {
                "bld_id": str(row["id"]),
                "n_enderecos": int(row["n_enderecos"]),
                "n_unidades": int(row["n_unidades"]),
                "confianca": row["confianca"],
                "mudou": bool(row["mudou"]),
                "revisar": bool(row["revisar"]),
                "metodos": row["metodos"],
                "enderecos": row["enderecos"],
            }
            feats.append({
                "type": "Feature",
                "id": props["bld_id"],
                "geometry": mapping(row.geometry),
                "properties": props,
            })
        doc = {"type": "FeatureCollection", "name": "cnefe_rooftop_poligonos", "features": feats}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------- pipeline
def processar(cnefe_path, edif_path, saida, debug=False, debug_gpkg=None,
              debug_csv_dir=None, web_geojson=None, web_poligonos=None):
    blds = carregar_edificacoes(edif_path)
    bbox = blds.to_crs(4326).total_bounds
    crs = blds.to_crs(4326).estimate_utm_crs()
    blds = blds.to_crs(crs)

    df = carregar_cnefe(cnefe_path, bbox)
    if debug:
        gpkg = Path(debug_gpkg) if debug_gpkg else Path(saida).with_name(Path(saida).stem + ".debug.gpkg")
        csv_dir = Path(debug_csv_dir) if debug_csv_dir else Path(saida).with_name(Path(saida).stem + "_debug")
        dbg = Debug(gpkg=gpkg, csv_dir=csv_dir)
        original = gpd.GeoDataFrame(
            df.copy(),
            geometry=gpd.points_from_xy(df[CFG["col"]["lon"]], df[CFG["col"]["lat"]]),
            crs=4326,
        )
        dbg.layer("cnefe_original", original)
        dbg.etapa("entrada", registros_cnefe=len(df), edificacoes=len(blds), crs=str(crs))
    else:
        dbg = None

    a = agregar_enderecos(df, crs)
    if dbg is not None:
        agregados = gpd.GeoDataFrame(a.copy(), geometry=gpd.points_from_xy(a["x"], a["y"]), crs=crs)
        dbg.layer("enderecos_agregados", agregados)
        dbg.etapa("agregacao", enderecos_base=len(a), unidades=len(df))

    a = calcular_normais(a, crs=crs, debug=dbg)
    a, hits = atribuir(a, blds, debug=dbg)

    antes = _snapshot(a)
    a = refinar_por_ordem(a, blds, hits, debug=dbg)
    a = checar_ordem(a)
    depois = _snapshot(a)
    diff = comparar_antes_depois(antes, depois)

    if dbg is not None:
        _registrar_debug_diff(dbg, antes, depois, diff, crs)
        resumo = resumir_edificacoes(a, blds)
        dbg.layer("edificacoes", resumo)
        dbg.etapa(
            "checagem_ordem",
            faces_revisar=int(a.loc[a["revisar_face"].fillna(False), "face_key"].nunique()),
        )
        dbg.escrever()
        dbg.imprimir()

    # O resultado final carrega o antes/depois de cada endereço.
    extras = diff[["addr_key", "bld_antes", "bld_depois", "metodo_antes",
                   "confianca_antes", "mudou_bld", "mudou_ponto", "deslocamento_m"]]
    a = a.merge(extras, on="addr_key", how="left")
    a = gpd.GeoDataFrame(a, geometry="geometry", crs=blds.crs)
    a = a.set_geometry("geometry", crs=blds.crs)
    cols = ["addr_key", "endereco", "cep", "especie", "unidades", "bld", "bld_id", "bld_antes", "metodo",
            "metodo_antes", "confianca", "confianca_antes", "mudou_bld", "mudou_ponto",
            "deslocamento_m", "dp", "face_rho", "revisar_face", "geometry"]
    cols = [c for c in cols if c in a.columns]
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    if saida.exists():
        saida.unlink()
    a.loc[a.geometry.notna(), cols].to_file(saida, layer="enderecos_rooftop", driver="GPKG")
    resumir_edificacoes(a, blds).to_file(saida, layer="edificacoes", driver="GPKG")

    if web_geojson:
        escrever_web_geojson(a, web_geojson)
        print(f"GeoJSON web: {web_geojson}")
    if web_poligonos:
        escrever_web_poligonos(a, blds, web_poligonos)
        print(f"GeoJSON polígonos: {web_poligonos}")

    print(a["confianca"].value_counts().to_string())
    return a


# ---------------------------------------------------------------- CLI
def _cli():
    p = argparse.ArgumentParser(
        description="Atribui endereços CNEFE a edificações e gera pontos rooftop.")
    p.add_argument("cnefe", help="CSV do CNEFE 2022 ou GeoJSON do QG/IBGE")
    p.add_argument("edificacoes",
                   help="Parquet/GPKG/GeoJSON de polígonos, ou 'mysql' para a tabela buildings")
    p.add_argument("saida", help="GPKG de saída do resultado final")
    p.add_argument("--debug", action="store_true",
                   help="grava cada etapa (antes/depois) em GPKG multi-camada e CSVs")
    p.add_argument("--debug-gpkg", default=None,
                   help="caminho do GPKG de debug (padrão: <saida>.debug.gpkg)")
    p.add_argument("--debug-dir", default=None,
                   help="pasta dos CSVs de debug (padrão: <saida>_debug/)")
    p.add_argument("--web-geojson", default=None,
                   help="também grava os pontos finais em GeoJSON 4326 para o backend")
    p.add_argument("--web-poligonos", default=None,
                   help="também grava os polígonos dos prédios que receberam pontos")
    p.add_argument("--raio-max", type=float, default=None)
    p.add_argument("--profundidade", type=float, default=None)
    p.add_argument("--fallback-raio", type=float, default=None)
    p.add_argument("--ambiguo-m", type=float, default=None)
    p.add_argument("--decaimento", type=float, default=None)
    p.add_argument("--angulos", default=None,
                   help="lista separada por vírgula, ex.: -30,-15,0,15,30")
    args = p.parse_args()

    for chave in ("raio_max", "profundidade", "fallback_raio", "ambiguo_m", "decaimento"):
        valor = getattr(args, chave)
        if valor is not None:
            CFG[chave] = valor
    if args.angulos:
        CFG["angulos"] = [float(a.strip()) for a in args.angulos.split(",") if a.strip()]

    return processar(
        args.cnefe, args.edificacoes, args.saida,
        debug=args.debug,
        debug_gpkg=args.debug_gpkg,
        debug_csv_dir=args.debug_dir,
        web_geojson=args.web_geojson,
        web_poligonos=args.web_poligonos,
    )


if __name__ == "__main__":
    _cli()
