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
import html
import json
import re
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
        "compl": "DSC_COMPLEMENTO", "especie": "COD_ESPECIE", "lat": "LATITUDE",
        "lon": "LONGITUDE", "nivel": "NV_GEO_COORD", "cep": "CEP",
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
    # Parâmetros de ordenação DP (Tarefa 1)
    "dp_cap": 8.0,             # custo máximo por endereço na DP
    "dp_pen": 6.0,             # penalidade por repetir edificação estreita
    "faixa_primeira_fileira": 6.0, # margem de profundidade na primeira fileira (m)
    # Parâmetros de anexos (Tarefa 3)
    "anexo_dist_max": 20.0,    # distância euclidiana máxima entre A e B (m)
    "anexo_min_overlap": 0.5,  # sobreposição mínima na projeção tangente (50%)
    # Parâmetros de condomínio (Tarefa 4)
    "condominio_min_unidades": 10, # mínimo de unidades para candidato a condomínio
    # Parâmetros do modelo preditivo de probabilidade (Tarefa 5)
    "corte_prob_revisar": 0.60, # corte de probabilidade abaixo do qual marcar revisar_face
}

# Nomes alternativos vistos no GeoJSON do QG/IBGE e em exports do CNEFE.
ALIASES = {
    "id": ("COD_UNICO_ENDERECO", "id"),
    "setor": ("COD_SETOR", "CD_SETOR", "setor"),
    "quadra": ("NUM_QUADRA", "CD_QUADRA", "quadra"),
    "face": ("NUM_FACE", "CD_FACE", "face"),
    "tipo": ("NOM_TIPO_SEGLOGR", "tipo"),
    "titulo": ("NOM_TITULO_SEGLOGR", "titulo"),
    "logr": ("NOM_SEGLOGR", "logradouro", "logr"),
    "num": ("NUM_ENDERECO", "num"),
    "mod": ("DSC_MODIFICADOR", "mod"),
    "compl": ("DSC_COMPLEMENTO", "COMPLEMENTO", "compl", "complemento"),
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


def _coluna_entrada(df: pd.DataFrame, chave: str) -> pd.Series:
    """Resolve uma coluna CNEFE sem alterar a entrada original."""
    candidatos = (CFG["col"].get(chave), *ALIASES.get(chave, ()))
    coluna = next((nome for nome in candidatos if nome and nome in df.columns), None)
    return df[coluna] if coluna else pd.Series("", index=df.index, dtype="object")


def _valor_vazio(serie: pd.Series) -> pd.Series:
    return serie.isna() | serie.astype(str).str.strip().str.lower().isin(("", "nan", "none", "null"))


def _amostras_complemento_seguras(serie: pd.Series, limite: int = 20) -> list[str]:
    """Mostra exemplos estruturais sem expor texto que pareça nome ou dado pessoal."""
    permitidos = {
        "andar", "ap", "apt", "apartamento", "bloco", "torre", "casa", "quadra",
        "conjunto", "fundos", "terreo", "térreo", "predio", "prédio", "sala", "loja",
        "entrada", "unidade", "lote", "piso", "cobertura", "subsolo", "garagem", "frente",
        "edificio", "edifício", "sobrado", "anexo", "galpao", "galpão", "acesso",
    }
    exemplos = []
    vistos = set()
    for valor in serie.dropna().astype(str).str.strip():
        if not valor or valor.lower() in ("nan", "none", "null"):
            continue
        if valor in vistos:
            continue
        vistos.add(valor)
        tokens = re.findall(r"[\wÀ-ÿ]+", valor.lower())
        seguro = bool(tokens) and all(token.isdigit() or token in permitidos for token in tokens)
        exemplos.append(valor if seguro else "[texto omitido por privacidade]")
        if len(exemplos) == limite:
            break
    return exemplos


def gerar_relatorio_entrada(cnefe_path: str | Path, edificios_path: str | Path,
                            saida_html: str | Path = "relatorio_entrada.html") -> dict:
    """Diagnostica a entrada CNEFE e grava relatório HTML sem dados identificáveis."""
    origem = str(cnefe_path).lower()
    if origem.endswith((".json", ".geojson")):
        df = _ler_cnefe_geojson(cnefe_path)
    else:
        df = pd.read_csv(cnefe_path, sep=CFG["csv_sep"], encoding=CFG["csv_encoding"], dtype=str)
    total = len(df)
    if not total:
        raise ValueError("A entrada CNEFE não contém registros.")

    dados = {chave: _coluna_entrada(df, chave) for chave in CFG["col"]}
    if origem.endswith((".json", ".geojson")):
        ausentes = [chave for chave in ("setor", "quadra", "face") if _valor_vazio(dados[chave]).all()]
        if ausentes:
            nomes = ", ".join(CFG["col"][chave] for chave in ausentes)
            raise ValueError(
                f"GeoJSON do Quadro Geográfico sem {nomes}; sem setor, quadra e face "
                "não é possível aplicar o algoritmo rooftop."
            )

    nivel = pd.to_numeric(dados["nivel"], errors="coerce")
    niveis = nivel.value_counts(dropna=False).sort_index(key=lambda idx: idx.astype(str))
    niveis_tabela = [("sem valor" if pd.isna(valor) else str(valor), int(contagem),
                      100 * int(contagem) / total) for valor, contagem in niveis.items()]
    niveis_ok = set(CFG["niveis_ok"])
    aceitos = nivel.isin(niveis_ok)
    descartados_nivel = int((~aceitos).sum())

    numero = dados["num"].astype(str).str.strip()
    lograd_num = df["LOGRAD_NUM"].astype(str).str.strip() if "LOGRAD_NUM" in df else pd.Series("", index=df.index)
    numero_extraido = lograd_num.str.extract(r",\s*([0-9]+[A-Za-z]?)\s*$", expand=False)
    numero_vazio = _valor_vazio(dados["num"]) & _valor_vazio(numero_extraido)

    faltantes = {
        "setor": int(_valor_vazio(dados["setor"]).sum()),
        "quadra": int(_valor_vazio(dados["quadra"]).sum()),
        "face": int(_valor_vazio(dados["face"]).sum()),
        "número": int(numero_vazio.sum()),
        "código único": int(_valor_vazio(dados["id"]).sum()),
    }

    for chave in ("lat", "lon"):
        dados[chave] = pd.to_numeric(dados[chave], errors="coerce")
    if "geometry" in df.columns:
        geometria = df["geometry"]
    else:
        geometria = pd.Series([None] * total, index=df.index)
    lat = dados["lat"].copy()
    lon = dados["lon"].copy()
    if lat.isna().all() and origem.endswith((".json", ".geojson")):
        # _ler_cnefe_geojson já preencheu LATITUDE/LONGITUDE pela geometria.
        lat = pd.to_numeric(_coluna_entrada(df, "lat"), errors="coerce")
        lon = pd.to_numeric(_coluna_entrada(df, "lon"), errors="coerce")

    coordenadas_ok = lat.notna() & lon.notna()
    if not coordenadas_ok.any():
        raise ValueError("A entrada não tem coordenadas válidas para medir direção e distância.")

    edificios = carregar_edificacoes(edificios_path)
    edif_4326 = edificios.to_crs(4326)
    crs_metrico = edif_4326.estimate_utm_crs()
    edificios_m = edif_4326.to_crs(crs_metrico)
    pontos_m = gpd.GeoSeries(
        gpd.points_from_xy(lon[coordenadas_ok], lat[coordenadas_ok]),
        index=df.index[coordenadas_ok], crs=4326,
    ).to_crs(crs_metrico)
    point_geoms = pontos_m.to_numpy()
    tree = STRtree(edificios_m.geometry.to_numpy())
    pontos_aceitos = point_geoms[aceitos.loc[coordenadas_ok].to_numpy()]
    _, distancias = tree.query_nearest(pontos_aceitos, all_matches=False, return_distance=True)
    acima_40m = int(np.count_nonzero(distancias > 40.0))

    face_key = (dados["setor"].astype(str) + "_" + dados["quadra"].astype(str)
                + "_" + dados["face"].astype(str))
    coords_por_indice = pd.DataFrame({"x": pontos_m.x, "y": pontos_m.y}, index=pontos_m.index)
    coordenadas_aceitas = coordenadas_ok & aceitos
    face_sizes = face_key.loc[coordenadas_aceitas].value_counts()
    faces_1_2 = int(face_sizes.between(1, 2).sum())
    faces_sem_reta = 0
    faces_aceitas = face_key.loc[coordenadas_aceitas]
    for indices in faces_aceitas.groupby(faces_aceitas).groups.values():
        pontos_face = coords_por_indice.loc[indices, ["x", "y"]].to_numpy()
        reta_valida = False
        if len(pontos_face) >= 3:
            singular = np.linalg.svd(pontos_face - pontos_face.mean(axis=0), compute_uv=False)
            reta_valida = singular[0] / np.sqrt(len(pontos_face)) >= 5.0 and singular[1] <= 0.5 * singular[0]
        if not reta_valida:
            faces_sem_reta += 1

    rua = _coluna_entrada(df, "logr").astype(str).str.strip()
    numero_endereco = dados["num"].where(~_valor_vazio(dados["num"]), numero_extraido).astype(str).str.strip()
    if "LOGRAD_NUM" in df.columns:
        rua_extraida = lograd_num.str.replace(r",\s*[0-9]+[A-Za-z]?\s*$", "", regex=True).str.strip()
        rua = rua.where(~_valor_vazio(rua), rua_extraida)
    duplicatas = pd.DataFrame({
        "rua": rua.str.upper().str.replace(r"\s+", " ", regex=True),
        "numero": numero_endereco.str.upper(),
        "face": face_key,
    })
    duplicatas = duplicatas[~_valor_vazio(duplicatas["numero"])]
    duplicatas = duplicatas.groupby(["rua", "numero"])["face"].nunique()
    duplicatas_entre_faces = int((duplicatas > 1).sum())

    colunas_complemento = [nome for nome in ("DSC_COMPLEMENTO", "COMPLEMENTO", "compl", "complemento") if nome in df.columns]
    exemplos_complemento: list[str] = []
    for nome in colunas_complemento:
        exemplos_complemento.extend(_amostras_complemento_seguras(df[nome], 20))
    exemplos_complemento = list(dict.fromkeys(exemplos_complemento))[:20]

    medidas = [
        ("Faces com 1–2 endereços", f"{faces_1_2:,}"),
        ("Faces sem reta válida (SVD)", f"{faces_sem_reta:,}"),
        ("Grupos logradouro+número repetidos em faces distintas", f"{duplicatas_entre_faces:,}"),
        ("Pontos a mais de 40 m de uma edificação", f"{acima_40m:,}"),
    ]
    html_niveis = "".join(
        f"<tr><td>{html.escape(valor)}</td><td>{contagem:,}</td><td>{pct:.2f}%</td></tr>"
        for valor, contagem, pct in niveis_tabela
    )
    html_faltantes = "".join(
        f"<tr><td>{html.escape(nome)}</td><td>{contagem:,}</td><td>{100 * contagem / total:.2f}%</td></tr>"
        for nome, contagem in faltantes.items()
    )
    html_medidas = "".join(
        f"<li>{html.escape(nome)}: <strong>{html.escape(valor)}</strong></li>" for nome, valor in medidas
    )
    html_complementos = "".join(f"<li>{html.escape(valor)}</li>" for valor in exemplos_complemento)
    if not html_complementos:
        html_complementos = "<li>Nenhum valor preenchido encontrado.</li>"

    relatorio = f"""<!doctype html>
<html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Diagnóstico da entrada CNEFE</title>
<style>body{{font:16px system-ui,sans-serif;max-width:900px;margin:36px auto;padding:0 18px;color:#172033}}h1,h2{{color:#123}}table{{border-collapse:collapse;width:100%;margin:12px 0 28px}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}li{{margin:5px 0}}.note{{color:#475569}}</style>
<body><h1>Diagnóstico da entrada CNEFE</h1>
<p>Arquivo: <strong>{html.escape(Path(cnefe_path).name)}</strong> · edificações: <strong>{len(edificios_m):,}</strong> · registros: <strong>{total:,}</strong></p>
<h2>Nível de geocodificação</h2><table><thead><tr><th>NV_GEO_COORD</th><th>Registros</th><th>Percentual</th></tr></thead><tbody>{html_niveis}</tbody></table>
<p>Filtro ativo: NV_GEO_COORD ∈ {html.escape(str(sorted(niveis_ok)))}. Descartados: <strong>{descartados_nivel:,} ({100 * descartados_nivel / total:.2f}%)</strong>.</p>
<h2>Campos ausentes</h2><table><thead><tr><th>Campo</th><th>Sem valor</th><th>Percentual</th></tr></thead><tbody>{html_faltantes}</tbody></table>
<h2>Geometria e duplicidades</h2><ul>{html_medidas}</ul>
<h2>Colunas de complemento presentes</h2><p>{html.escape(', '.join(colunas_complemento) if colunas_complemento else 'Nenhuma')}</p>
<p class="note">Exemplos são limitados a expressões estruturais de unidade/endereço; outros textos foram omitidos para evitar exposição de dados pessoais.</p><ol>{html_complementos}</ol>
</body></html>"""
    destino = Path(saida_html)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(relatorio, encoding="utf-8")
    return {
        "total_registros": total,
        "por_nivel": {valor: {"registros": contagem, "percentual": round(pct, 2)}
                      for valor, contagem, pct in niveis_tabela},
        "descartados_nivel": descartados_nivel,
        "faltantes": {nome: {"registros": contagem, "percentual": round(100 * contagem / total, 2)}
                      for nome, contagem in faltantes.items()},
        "faces_1_2_enderecos": faces_1_2,
        "faces_sem_reta_valida": faces_sem_reta,
        "duplicatas_entre_faces": duplicatas_entre_faces,
        "pontos_mais_40m": acima_40m,
        "colunas_complemento": colunas_complemento,
        "relatorio": str(destino),
    }


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
        print("AVISO: arquivo sem NV_GEO_COORD; todos os pontos serão tratados como nível 1 "
              "(coordenada de campo). Se houver medianas de rua no arquivo, a precisão cai.")
        df[c["nivel"]] = 1
    df[c["nivel"]] = pd.to_numeric(df[c["nivel"]], errors="coerce")

    minx, miny, maxx, maxy = bbox4326
    m = 0.001  # ~100 m de margem
    df = df[df[c["lon"]].between(minx - m, maxx + m) & df[c["lat"]].between(miny - m, maxy + m)]
    df = df[df[c["nivel"]].isin(CFG["niveis_ok"])]
    # sem setor/quadra/face o algoritmo degenera (tudo vira uma face só): aborta
    for k in ("setor", "quadra", "face"):
        vazios = df[c[k]].fillna("").astype(str).str.strip().isin(["", "nan", "None"]).mean() if len(df) else 0
        if vazios > 0.05:
            raise ValueError(
                f"{vazios:.0%} dos registros sem {c[k]}. O algoritmo depende de setor/quadra/face; "
                "use o CSV de microdados do CNEFE 2022 ou ajuste ALIASES.")
    vazios_id = df[c["id"]].fillna("").astype(str).str.strip().isin(["", "nan", "None"]).mean() if len(df) else 0
    if vazios_id > 0.05:
        raise ValueError(f"{vazios_id:.0%} dos registros sem {c['id']}: endereços S/N se fundiriam.")
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

    compl_col = c.get("compl", "DSC_COMPLEMENTO")
    compl_series = df[compl_col].astype(str) if compl_col in df.columns else pd.Series("", index=df.index)
    padrao_condo = re.compile(r"\b(?:BLOCO|TORRE|QUADRA|CASA)\s*[0-9]+", re.IGNORECASE)
    df["tem_compl_condo"] = compl_series.str.contains(padrao_condo, regex=True)

    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[c["lon"]], df[c["lat"]]), crs=4326).to_crs(crs)
    pts["x"], pts["y"] = pts.geometry.x, pts.geometry.y

    agg = pts.groupby("addr_key").agg(
        x=("x", "mean"), y=("y", "mean"), unidades=("x", "size"),
        setor_cnefe=(c["setor"], "first"), quadra_cnefe=(c["quadra"], "first"),
        face_cnefe=(c["face"], "first"),
        quadra_key=("quadra_key", "first"), face_key=("face_key", "first"),
        tipo=(c["tipo"], "first"), titulo=(c["titulo"], "first"), logr=(c["logr"], "first"),
        num=("num_i", "first"), mod=(c["mod"], "first"), cep=(c["cep"], "first"),
        especie=(c["especie"], lambda s: s.mode().iat[0] if len(s.mode()) else ""),
        sn=("sn", "first"),
        tem_compl_condo=("tem_compl_condo", "any"),
    ).reset_index()
    rua = (agg["tipo"] + " " + agg["titulo"] + " " + agg["logr"]).str.split().str.join(" ")
    agg["endereco"] = rua + ", " + np.where(agg["sn"], "S/N", agg["num"].astype(str) + agg["mod"].astype(str).str.strip())
    agg["candidato_condominio"] = (agg["unidades"] >= CFG["condominio_min_unidades"]) | agg["tem_compl_condo"]
    agg["tipo_vinculo"] = "principal"
    agg["endereco_ref"] = ""
    agg["condominio_sem_perimetro"] = False
    return agg


# ---------------------------------------------------------------- direção
def _chave_face(setor, quadra, face) -> str:
    def normalizar(valor):
        texto = str(valor).strip()
        return texto[:-2] if texto.endswith(".0") else texto
    return "|".join(normalizar(valor) for valor in (setor, quadra, face))


def _segmento_dominante(geometria):
    """Extrai o segmento mais longo de uma linha/multilinha como vetor unitário."""
    if geometria is None or geometria.is_empty:
        return None
    linhas = list(geometria.geoms) if geometria.geom_type == "MultiLineString" else [geometria]
    melhor = None
    melhor_comprimento = 0.0
    for linha in linhas:
        if linha.geom_type != "LineString":
            continue
        coords = np.asarray(linha.coords, dtype=float)[:, :2]
        if len(coords) < 2:
            continue
        vetores = np.diff(coords, axis=0)
        comprimentos = np.linalg.norm(vetores, axis=1)
        i = int(np.argmax(comprimentos))
        if comprimentos[i] > melhor_comprimento:
            melhor_comprimento = float(comprimentos[i])
            melhor = vetores[i] / comprimentos[i]
    return melhor


def carregar_faces_ibge(path: str | Path, crs) -> dict[str, np.ndarray]:
    """Carrega a tangente dominante das linhas da Base de Faces de Logradouros."""
    faces = gpd.read_file(path)
    aliases = {
        "setor": ("CD_SETOR", "COD_SETOR", "setor"),
        "quadra": ("CD_QUADRA", "NUM_QUADRA", "quadra"),
        "face": ("CD_FACE", "NUM_FACE", "face"),
    }
    colunas = {}
    for chave, nomes in aliases.items():
        coluna = next((nome for nome in nomes if nome in faces.columns), None)
        if coluna is None:
            raise ValueError(f"Base de Faces IBGE sem coluna de {chave}: esperada uma de {nomes}.")
        colunas[chave] = coluna
    if faces.crs is None:
        raise ValueError("Base de Faces IBGE precisa declarar o CRS das linhas.")
    faces = faces.to_crs(crs)
    resultado: dict[str, np.ndarray] = {}
    comprimentos: dict[str, float] = {}
    for _, row in faces.iterrows():
        vetor = _segmento_dominante(row.geometry)
        if vetor is None:
            continue
        chave = _chave_face(*(row[colunas[k]] for k in ("setor", "quadra", "face")))
        if not all(chave.split("|")):
            continue
        # Em linhas repetidas para a mesma face, mantém a geometria que fornece
        # o maior segmento utilizável; isso evita depender da ordem do arquivo.
        tamanho = float(row.geometry.length)
        if tamanho > comprimentos.get(chave, -1):
            resultado[chave] = vetor
            comprimentos[chave] = tamanho
    return resultado


def calcular_normais(a, crs=None, debug: Debug | None = None,
                     faces_ibge: dict[str, np.ndarray] | None = None):
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
    t = a[["face_key"]].join(tg, on="face_key")[["tx", "ty"]].to_numpy(copy=True)
    fonte = np.where(np.isfinite(t[:, 0]), "svd", "centro_quadra").astype(object)
    if faces_ibge:
        for i, row in enumerate(a.itertuples()):
            chave = _chave_face(row.setor_cnefe, row.quadra_cnefe, row.face_cnefe)
            vetor = faces_ibge.get(chave)
            if vetor is not None:
                t[i] = vetor
                fonte[i] = "faces_ibge"
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
    a["fonte_direcao"] = fonte

    if debug is not None:
        ok = ~np.isnan(n[:, 0])
        pp = p[ok]
        nn = n[ok]
        comprimento = min(CFG["raio_max"], 20.0)
        linhas = shapely.linestrings(np.stack([pp, pp + nn * comprimento], axis=1))
        gdf = gpd.GeoDataFrame(
            a.loc[ok, ["addr_key", "endereco", "face_key", "x", "y", "nx", "ny", "ambiguo", "fonte_direcao"]].reset_index(drop=True),
            geometry=linhas,
            crs=crs,
        )
        debug.layer("normais", gdf)
        debug.etapa(
            "normais",
            enderecos=len(a), com_normal=int(ok.sum()),
            sem_normal=int((~ok).sum()), ambiguos=int(np.nansum(ambiguo)),
            face_sem_reta=int(sem_t.sum()),
            direcao_faces_ibge=int((fonte == "faces_ibge").sum()),
            direcao_svd=int((fonte == "svd").sum()),
            direcao_centro_quadra=int((fonte == "centro_quadra").sum()),
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
class RegistroDonos:
    """Rastreia bld -> face_key e contagem de endereços por edificação."""

    def __init__(self):
        self.dono: dict[int, str] = {}
        self.contagem: dict[int, int] = {}

    def pode_atribuir(self, bld: int, face_key: str) -> bool:
        if bld < 0:
            return True
        f = self.dono.get(bld)
        return f is None or f == face_key

    def vincular(self, bld: int, face_key: str):
        if bld < 0:
            return
        f = self.dono.get(bld)
        if f is not None and f != face_key:
            raise ValueError(f"Conflito de dono: bld {bld} pertence a {f}, não {face_key}")
        self.dono[bld] = face_key
        self.contagem[bld] = self.contagem.get(bld, 0) + 1

    def adicionar(self, bld: int, face_key: str):
        """Alias para vincular."""
        self.vincular(bld, face_key)

    def desvincular(self, bld: int):
        if bld < 0 or bld not in self.contagem:
            return
        self.contagem[bld] -= 1
        if self.contagem[bld] <= 0:
            self.contagem.pop(bld, None)
            self.dono.pop(bld, None)


def atribuir(a, blds, debug: Debug | None = None):
    geoms = blds.geometry.to_numpy()
    hits, _ = lancar_raios(a, blds, debug=debug)

    a["bld"] = -1
    a["metodo"] = "sem_match"
    a["confianca"] = "nenhuma"
    a["compartilhado"] = False
    a["motivo_confianca"] = ""
    a["share"] = np.nan
    a["dmin"] = np.nan
    a["acerto_raio_central"] = False
    rx = np.full(len(a), np.nan); ry = np.full(len(a), np.nan)
    donos = RegistroDonos()

    if len(hits):
        g = hits.groupby(["addr", "bld"])
        tab = g.agg(score=("score", "sum"), dist=("dist", "min"), centro=("centro", "any")).reset_index()
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
        feito, sel = set(), []
        for r in tab[["addr", "bld", "face"]].itertuples(index=True):
            if r.addr in feito:
                continue
            if donos.pode_atribuir(r.bld, r.face):
                donos.vincular(r.bld, r.face)
                feito.add(r.addr)
                sel.append(r.Index)
        best = tab.loc[sel].copy()

        # Recalcula o share com base no score da edificação escolhida sobre o score total do endereço
        tot_scores = tab.groupby("addr")["score"].sum()
        best["share"] = best["score"] / best["addr"].map(tot_scores)

        # raio mais forte dentro da edificação vencedora -> ponto na fachada
        br = hits.merge(best[["addr", "bld"]], on=["addr", "bld"])
        br = br.loc[br.groupby("addr")["score"].idxmax()].set_index("addr")
        best = best.set_index("addr").join(br[["dx", "dy", "ox", "oy"]])
        idx = best.index.to_numpy()
        a.loc[idx, "bld"] = best["bld"].to_numpy()
        a.loc[idx, "metodo"] = "raio"
        a.loc[idx, "share"] = best["share"].to_numpy()
        a.loc[idx, "dmin"] = best["dist"].to_numpy()
        a.loc[idx, "acerto_raio_central"] = best["centro"].to_numpy().astype(bool)

        dist_val = best["dist"].to_numpy()
        share_val = best["share"].to_numpy()
        centro_val = best["centro"].to_numpy()
        amb_val = a.loc[idx, "ambiguo"].to_numpy()

        motivos = np.full(len(idx), "alta", dtype=object)
        for i in range(len(idx)):
            if amb_val[i]:
                motivos[i] = "ambiguo"
            elif not centro_val[i]:
                motivos[i] = "sem_raio_central"
            elif dist_val[i] > 25:
                motivos[i] = "longe"
            elif share_val[i] < 0.6:
                motivos[i] = "share_baixo"

        a.loc[idx, "confianca"] = np.where(motivos == "alta", "alta", "media")
        a.loc[idx, "motivo_confianca"] = motivos

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
            for addr_idx, candidate_bld in zip(ai, b_idx):
                fk = a.at[addr_idx, "face_key"]
                if donos.pode_atribuir(candidate_bld, fk):
                    donos.vincular(candidate_bld, fk)
                    a.at[addr_idx, "bld"] = candidate_bld
                    a.at[addr_idx, "metodo"] = "vizinho"
                    a.at[addr_idx, "confianca"] = "baixa"
                    a.at[addr_idx, "motivo_confianca"] = "fallback"

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
    return gdf, hits, donos


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


def refinar_por_ordem(a, blds, hits, donos: RegistroDonos | None = None, cap=None, pen=None, debug: Debug | None = None):
    """Corrige o ruído do GPS usando a ordem da numeração em cada face."""
    geoms = blds.geometry.to_numpy()
    cap = CFG["dp_cap"] if cap is None else cap
    pen = CFG["dp_pen"] if pen is None else pen
    faixa = CFG["faixa_primeira_fileira"]
    a["dp"] = False
    a["dp_custo"] = 0.0
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

        bl = np.array(frente[fk])
        if donos is not None:
            bl = np.array([b for b in bl if donos.pode_atribuir(b, fk)])
        if len(bl) == 0:
            continue

        cs = shapely.get_coordinates(shapely.boundary(geoms[bl]), return_index=True)
        proj = cs[0] @ t
        p0 = g[["x", "y"]].to_numpy().mean(axis=0)
        prof = (cs[0] - p0) @ n_                       # profundidade a partir da linha da face
        lo = np.array([proj[cs[1] == k].min() for k in range(len(bl))])
        hi = np.array([proj[cs[1] == k].max() for k in range(len(bl))])
        dmin = np.array([prof[cs[1] == k].min() for k in range(len(bl))])
        # primeira fileira (sem edículas de fundo nem o outro lado da rua), dentro da extensão da face
        dmin_valido = np.where(dmin > -2, dmin, np.inf)
        keep = (dmin > -2) & (dmin <= np.nanmin(dmin_valido) + faixa) \
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
            if donos is not None:
                donos.desvincular(bld_antes)
                donos.vincular(bi, fk)
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
            a.at[ai, "motivo_confianca"] = "alta" if cu < 2 else "dp_custo"
            a.at[ai, "dp"] = True
            a.at[ai, "dp_custo"] = float(cu)
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


def avaliar_conflitos(a, blds):
    """Rebaixa para média edificações compartilhadas, exceto mesmo número ou geminadas."""
    a["compartilhado"] = False
    if "motivo_confianca" not in a.columns:
        a["motivo_confianca"] = ""

    m = a[a["bld"] >= 0]
    if len(m) == 0:
        return a

    geoms = blds.geometry.to_numpy()

    # Mediana de largura de edificação por face
    mediana_por_face = {}
    for fk, g in m.groupby("face_key"):
        t = g[["tx", "ty"]].iloc[0].to_numpy()
        if not np.isnan(t[0]):
            bl_unicos = g["bld"].unique()
            largs = []
            for bi in bl_unicos:
                cs = shapely.get_coordinates(shapely.boundary(geoms[bi]))
                proj = cs @ t
                largs.append(proj.max() - proj.min())
            if largs:
                mediana_por_face[fk] = float(np.median(largs))

    for bi, group in m.groupby("bld"):
        if len(group) < 2:
            continue
        idxs = group.index.to_numpy()
        a.loc[idxs, "compartilhado"] = True

        # Exceção a: mesmo número e só diferem no modificador
        nums = group["num"].to_numpy() if "num" in group.columns else np.array([])
        sn = group["sn"].to_numpy() if "sn" in group.columns else np.zeros(len(group), dtype=bool)
        mesmo_num = (not sn.any()) and (len(np.unique(nums)) == 1)
        if mesmo_num:
            for i in idxs:
                if a.at[i, "confianca"] == "alta":
                    a.at[i, "motivo_confianca"] = "mesmo_numero"
            continue

        # Exceção b: edificação larga (> 1.8 * mediana da face) e pontos rooftop >= 4m na tangente
        fk = group["face_key"].iloc[0]
        mediana_face = mediana_por_face.get(fk, 0.0)
        t = group[["tx", "ty"]].iloc[0].to_numpy()
        geminada_ok = False
        if not np.isnan(t[0]):
            cs = shapely.get_coordinates(shapely.boundary(geoms[bi]))
            proj = cs @ t
            larg_bld = proj.max() - proj.min()
            # Se só tem 1 prédio na face, compara com a distância entre os pontos rooftop (> 1.8 * 4m) ou mediana se houver
            limite_larg = 1.8 * mediana_face if (mediana_face > 0 and len(mediana_por_face) > 0 and len(group["bld"].unique()) > 1) else 10.0
            if larg_bld >= limite_larg:
                pts_geoms = group.geometry.to_numpy()
                if all(p is not None and not shapely.is_empty(p) for p in pts_geoms):
                    pts_coords = shapely.get_coordinates(pts_geoms)
                    projs = pts_coords @ t
                    diffs = np.abs(projs[:, None] - projs[None, :])
                    np.fill_diagonal(diffs, np.inf)
                    if diffs.min() >= 4.0:
                        geminada_ok = True

        if geminada_ok:
            for i in idxs:
                if a.at[i, "confianca"] == "alta":
                    a.at[i, "motivo_confianca"] = "geminada"
            continue

        # Caso geral: rebaixa para média por conflito de compartilhamento
        for i in idxs:
            a.at[i, "confianca"] = "media"
            a.at[i, "motivo_confianca"] = "compartilhado"

    return a


def vincular_anexos(a, blds):
    """Identifica edificações B sem endereço no fundo do lote de A (Tarefa 3)."""
    if "tipo_vinculo" not in blds.columns:
        blds["tipo_vinculo"] = "principal"
        blds["endereco_ref"] = ""
        blds["bld_principal"] = blds["id"] if "id" in blds else blds["bld_idx"].astype(str)

    geoms = blds.geometry.to_numpy()
    tree = STRtree(geoms)

    com_endereco = a[a["bld"] >= 0].drop_duplicates("bld")
    if len(com_endereco) == 0:
        return a, blds, pd.DataFrame()

    blds_com_end = set(com_endereco["bld"].to_numpy())
    blds_sem_end = [i for i in range(len(blds)) if i not in blds_com_end]
    if not blds_sem_end:
        return a, blds, pd.DataFrame()

    # Agrupa pontos por quadra e face para checagem de proximidade da quadra
    quadra_faces_pts: dict[str, dict[str, list]] = {}
    for _, r_pts in a[a.geometry.notna()].iterrows():
        qk = str(r_pts.get("quadra_key", ""))
        fk = str(r_pts.get("face_key", ""))
        if not qk or not fk:
            continue
        quadra_faces_pts.setdefault(qk, {}).setdefault(fk, []).append(r_pts.geometry)

    info_A = {}
    for _, row in com_endereco.iterrows():
        bi = int(row["bld"])
        tx, ty = row["tx"], row["ty"]
        nx, ny = row["nx"], row["ny"]
        if np.isnan(tx) or np.isnan(nx):
            continue
        t = np.array([tx, ty])
        n_ = np.array([nx, ny])
        cs = shapely.get_coordinates(shapely.boundary(geoms[bi]))
        proj_t = cs @ t
        proj_n = cs @ n_
        info_A[bi] = {
            "t": t, "n": n_, "face_key": row["face_key"], "quadra_key": row.get("quadra_key", ""),
            "lo": proj_t.min(), "hi": proj_t.max(),
            "prof_max": proj_n.max(), "prof_min": proj_n.min(),
            "poly": geoms[bi], "addr_row": row,
        }

    anexos_encontrados = []
    dist_max = CFG.get("anexo_dist_max", 20.0)
    min_overlap = CFG.get("anexo_min_overlap", 0.5)

    for bi_b in blds_sem_end:
        poly_B = geoms[bi_b]
        cen_B = shapely.centroid(poly_B)
        cands_idx = tree.query(shapely.buffer(poly_B, dist_max), predicate="intersects")
        cands_A = [ci for ci in cands_idx if ci in info_A and ci != bi_b]
        if not cands_A:
            continue

        cs_B = shapely.get_coordinates(shapely.boundary(poly_B))
        candidatos_validos = []

        for bi_a in cands_A:
            inf = info_A[bi_a]
            t, n_ = inf["t"], inf["n"]
            poly_A = inf["poly"]

            # 3. Distância de no máximo 20 m
            if shapely.distance(poly_A, poly_B) > dist_max:
                continue

            proj_t_B = cs_B @ t
            lo_B, hi_B = proj_t_B.min(), proj_t_B.max()
            larg_B = hi_B - lo_B
            if larg_B <= 0.1:
                continue

            # 2. Projeção na tangente cobre pelo menos 50% de B dentro de [lo, hi] de A
            overlap = max(0.0, min(inf["hi"], hi_B) - max(inf["lo"], lo_B))
            if (overlap / larg_B) < min_overlap:
                continue

            # 1. B está do lado de dentro da face de A
            proj_n_B = cs_B @ n_
            prof_B_min = proj_n_B.min()
            if prof_B_min <= inf["prof_min"]:
                continue

            # 3b. Sem outra edificação com endereço entre elas ao longo da normal
            cen_A = shapely.centroid(poly_A)
            coords_seg = [(shapely.get_x(cen_A), shapely.get_y(cen_A)), (shapely.get_x(cen_B), shapely.get_y(cen_B))]
            raio_corredor = shapely.linestrings(coords_seg)
            hits_corredor = tree.query(raio_corredor, predicate="intersects")
            bloqueado = any(h in blds_com_end and h != bi_a and h != bi_b for h in hits_corredor)
            if bloqueado:
                continue

            # 4. B está mais perto da face de A do que de qualquer outra face da mesma quadra
            qk = inf["quadra_key"]
            fk_A = inf["face_key"]
            if qk in quadra_faces_pts and len(quadra_faces_pts[qk]) > 1:
                pts_A = quadra_faces_pts[qk].get(fk_A, [])
                if pts_A:
                    dist_A = min(shapely.distance(cen_B, p) for p in pts_A)
                    mais_perto_de_outra = False
                    for fk_outra, pts_outra in quadra_faces_pts[qk].items():
                        if fk_outra != fk_A and pts_outra:
                            dist_outra = min(shapely.distance(cen_B, p) for p in pts_outra)
                            if dist_outra < dist_A:
                                mais_perto_de_outra = True
                                break
                    if mais_perto_de_outra:
                        continue

            candidatos_validos.append({
                "bi_a": bi_a,
                "overlap": overlap / larg_B,
                "info": inf,
            })

        if not candidatos_validos:
            continue

        # Se houver mais de uma A possível, escolha a de maior sobreposição e marque "anexo_ambiguo"
        candidatos_validos.sort(key=lambda c: c["overlap"], reverse=True)
        vencedor = candidatos_validos[0]
        bi_vencedor = vencedor["bi_a"]
        inf_vencedor = vencedor["info"]
        id_princ = blds.at[bi_vencedor, "id"] if "id" in blds else str(bi_vencedor)
        id_anexo = blds.at[bi_b, "id"] if "id" in blds else str(bi_b)
        motivo = "anexo_ambiguo" if len(candidatos_validos) > 1 else "anexo"

        blds.at[bi_b, "tipo_vinculo"] = "anexo"
        blds.at[bi_b, "endereco_ref"] = id_princ
        blds.at[bi_b, "bld_principal"] = id_princ

        # Atribui a B o mesmo endereço com tipo_vinculo = "anexo", endereco_ref = id de A e confiança "media"
        row_ref = inf_vencedor["addr_row"]
        nova_linha = {
            "addr_key": f"{id_anexo}_anexo",
            "endereco": row_ref.get("endereco", ""),
            "cep": row_ref.get("cep", ""),
            "especie": row_ref.get("especie", ""),
            "unidades": 1,
            "bld": bi_b,
            "bld_id": id_anexo,
            "tipo_vinculo": "anexo",
            "endereco_ref": id_princ,
            "confianca": "media",
            "motivo_confianca": motivo,
            "metodo": "anexo",
            "geometry": shapely.point_on_surface(poly_B),
            "face_key": inf_vencedor["face_key"],
            "quadra_key": inf_vencedor["quadra_key"],
            "compartilhado": False,
            "mudou_bld": True,
            "mudou_ponto": True,
            "deslocamento_m": 0.0,
            "revisar_face": False,
            "condominio_sem_perimetro": False,
        }
        anexos_encontrados.append(nova_linha)

    df_anexos = pd.DataFrame(anexos_encontrados)
    if len(df_anexos):
        a = pd.concat([a, df_anexos], ignore_index=True)

    return a, blds, df_anexos


def vincular_condominios(a, blds, perimetros_path: str | Path | None = None):
    """Atribui blocos internos de condomínio via perímetros (Tarefa 4)."""
    if "condominio_sem_perimetro" not in a.columns:
        a["condominio_sem_perimetro"] = False

    is_condo = a["candidato_condominio"].fillna(False) if "candidato_condominio" in a.columns else pd.Series(False, index=a.index)

    if not perimetros_path or not Path(perimetros_path).exists():
        a.loc[is_condo, "condominio_sem_perimetro"] = True
        return a, blds, pd.DataFrame()

    try:
        perimetros = gpd.read_file(perimetros_path)
        if perimetros.crs != blds.crs and blds.crs is not None:
            perimetros = perimetros.to_crs(blds.crs)
    except Exception:
        a.loc[is_condo, "condominio_sem_perimetro"] = True
        return a, blds, pd.DataFrame()

    geoms = blds.geometry.to_numpy()
    tree = STRtree(geoms)
    if "tipo_vinculo" not in blds.columns:
        blds["tipo_vinculo"] = "principal"
        blds["endereco_ref"] = ""
        blds["bld_principal"] = blds["id"] if "id" in blds else blds["bld_idx"].astype(str)
    blds_com_end = set(a[a["bld"] >= 0]["bld"].to_numpy())
    condo_atribuidos = []

    for idx, row in a[is_condo].iterrows():
        pt = row["geometry"] if row.get("geometry") is not None else shapely.points(row["x"], row["y"])
        if pt is None or shapely.is_empty(pt):
            continue

        hits = perimetros[perimetros.contains(pt)]
        if len(hits) == 0:
            a.at[idx, "condominio_sem_perimetro"] = True
            continue

        a.at[idx, "condominio_sem_perimetro"] = False
        poly_perim = hits.geometry.iloc[0]
        id_portao = str(row.get("bld_id") or row.get("bld"))

        for bi in tree.query(poly_perim, predicate="intersects"):
            if bi in blds_com_end:
                continue
            poly_bld = geoms[bi]
            if shapely.area(shapely.intersection(poly_perim, poly_bld)) >= 0.5 * shapely.area(poly_bld):
                blds_com_end.add(bi)
                id_bld = blds.at[bi, "id"] if "id" in blds else str(bi)
                blds.at[bi, "tipo_vinculo"] = "condominio"
                blds.at[bi, "endereco_ref"] = id_portao

                nova_linha = {
                    "addr_key": f"{id_bld}_condo",
                    "endereco": row["endereco"],
                    "cep": row["cep"],
                    "especie": row["especie"],
                    "unidades": 1,
                    "bld": bi,
                    "bld_id": id_bld,
                    "tipo_vinculo": "condominio",
                    "endereco_ref": id_portao,
                    "confianca": "media",
                    "motivo_confianca": "condominio",
                    "metodo": "condominio",
                    "geometry": shapely.point_on_surface(poly_bld),
                    "face_key": row["face_key"],
                    "quadra_key": row.get("quadra_key", ""),
                    "compartilhado": False,
                    "mudou_bld": True,
                    "mudou_ponto": True,
                    "deslocamento_m": 0.0,
                    "revisar_face": False,
                    "condominio_sem_perimetro": False,
                }
                condo_atribuidos.append(nova_linha)

    df_condo = pd.DataFrame(condo_atribuidos)
    if len(df_condo):
        a = pd.concat([a, df_condo], ignore_index=True)

    return a, blds, df_condo


# ---------------------------------------------------------------- modelo probabilístico (Tarefa 5)
def extrair_features_modelo(a: pd.DataFrame) -> pd.DataFrame:
    """Extrai matriz de features para treino/predição de prob_correto."""
    feats = pd.DataFrame(index=a.index)
    feats["share"] = a["share"].fillna(0.0).astype(float) if "share" in a.columns else 0.0
    feats["dmin"] = a["dmin"].fillna(999.0).astype(float) if "dmin" in a.columns else 999.0
    feats["acerto_raio_central"] = a["acerto_raio_central"].fillna(False).astype(int) if "acerto_raio_central" in a.columns else 0
    feats["dp_custo"] = a["dp_custo"].fillna(0.0).astype(float) if "dp_custo" in a.columns else 0.0
    feats["face_rho"] = a["face_rho"].fillna(0.0).astype(float) if "face_rho" in a.columns else 0.0
    feats["ambiguo"] = a["ambiguo"].fillna(False).astype(int) if "ambiguo" in a.columns else 0
    principal = a["tipo_vinculo"].fillna("principal") == "principal" if "tipo_vinculo" in a.columns else pd.Series(True, index=a.index)
    feats["n_enderecos_face"] = a["face_key"].map(a.loc[principal, "face_key"].value_counts()).fillna(1).astype(float) if "face_key" in a.columns else 1.0
    feats["compartilhado"] = a["compartilhado"].fillna(False).astype(int) if "compartilhado" in a.columns else 0

    # One-hot / dummies categóricos com colunas esperadas
    metodos = ["raio", "vizinho", "raio+ordem", "anexo", "condominio"]
    metodo_series = a["metodo"].astype(str) if "metodo" in a.columns else pd.Series("", index=a.index)
    for m in metodos:
        feats[f"metodo_{m}"] = (metodo_series == m).astype(int)

    tipos_vinculo = ["principal", "anexo", "condominio"]
    vinculo_series = a["tipo_vinculo"].astype(str) if "tipo_vinculo" in a.columns else pd.Series("principal", index=a.index)
    for tv in tipos_vinculo:
        feats[f"tipo_vinculo_{tv}"] = (vinculo_series == tv).astype(int)

    return feats


def aplicar_modelo_probabilidade(a: pd.DataFrame, modelo_path: str | Path | None = None, corte: float | None = None) -> pd.DataFrame:
    """Adiciona prob_correto e atualiza revisar_face se o modelo estiver disponível."""
    if corte is None:
        corte = CFG.get("corte_prob_revisar", 0.60)

    if not modelo_path or not Path(modelo_path).exists():
        a["prob_correto"] = np.nan
        return a

    try:
        import joblib
        modelo = joblib.load(modelo_path)
        X = extrair_features_modelo(a)
        if hasattr(modelo, "predict_proba"):
            probs = modelo.predict_proba(X)[:, 1]
        else:
            probs = modelo.predict(X)
        a["prob_correto"] = probs
        # Atualiza corte de revisão: revisar se probabilidade for menor que o corte
        revisar_antigo = a["revisar_face"].fillna(False) if "revisar_face" in a.columns else pd.Series(False, index=a.index)
        a["revisar_face"] = revisar_antigo | (a["prob_correto"] < corte)
    except Exception as err:
        print(f"Aviso: Não foi possível aplicar modelo {modelo_path}: {err}")
        a["prob_correto"] = np.nan

    return a


# ---------------------------------------------------------------- checagem de ordem
def checar_ordem(a):
    """Numeração deve crescer ao longo da face; faces fora disso vão para revisão."""
    sn_mask = a["sn"].fillna(False).astype(bool) if "sn" in a.columns else pd.Series(False, index=a.index)
    m = (a["bld"] >= 0) & ~sn_mask & a["tx"].notna() & a.geometry.notna()
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
                "compartilhado": bool(row.get("compartilhado", False)),
                "motivo_confianca": str(row.get("motivo_confianca") or ""),
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
                "compartilhado": bool(g["compartilhado"].any()) if "compartilhado" in g else False,
                "motivos_confianca": " | ".join(sorted(set(g["motivo_confianca"].dropna().astype(str)))) if "motivo_confianca" in g else "",
                "mudou": bool(g["mudou_bld"].fillna(False).any()
                              or g["mudou_ponto"].fillna(False).any()),
                "revisar": bool(g["revisar_face"].fillna(False).any()),
                "metodos": " | ".join(sorted(set(g["metodo"].astype(str)))),
                "enderecos": " | ".join(g["endereco"].astype(str).head(6)),
            })
        r = m.groupby("bld").apply(resumo, include_groups=False).reset_index()
        cols_bld = ["bld_idx", "id", "geometry"]
        if "tipo_vinculo" in blds.columns:
            cols_bld.append("tipo_vinculo")
        if "bld_principal" in blds.columns:
            cols_bld.append("bld_principal")
        g = blds[cols_bld].merge(r, left_on="bld_idx", right_on="bld")
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
                "tipo_vinculo": str(row.get("tipo_vinculo") or "principal"),
                "bld_principal": str(row.get("bld_principal") or row["id"]),
                "compartilhado": bool(row.get("compartilhado", False)),
                "motivos_confianca": str(row.get("motivos_confianca", "")),
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
              debug_csv_dir=None, web_geojson=None, web_poligonos=None,
              perimetros=None, modelo=None, params=None, corte_prob=None,
              faces_ibge_path=None, medir_direcao=False):
    if params:
        p_dict = json.loads(Path(params).read_text(encoding="utf-8")) if isinstance(params, (str, Path)) and Path(params).exists() else params
        if isinstance(p_dict, dict):
            CFG.update(p_dict)
    if corte_prob is not None:
        CFG["corte_prob_revisar"] = float(corte_prob)

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
    faces_ibge = carregar_faces_ibge(faces_ibge_path, crs) if faces_ibge_path else None
    if medir_direcao and faces_ibge is None:
        raise ValueError("--medir-direcao requer --faces-ibge.")
    if dbg is not None:
        agregados = gpd.GeoDataFrame(a.copy(), geometry=gpd.points_from_xy(a["x"], a["y"]), crs=crs)
        dbg.layer("enderecos_agregados", agregados)
        dbg.etapa("agregacao", enderecos_base=len(a), unidades=len(df))

    bld_svd = None
    if medir_direcao:
        baseline = calcular_normais(a.copy(), crs=crs)
        baseline, hits_svd, donos_svd = atribuir(baseline, blds)
        baseline = refinar_por_ordem(baseline, blds, hits_svd, donos=donos_svd)
        bld_svd = baseline["bld"].to_numpy(copy=True)

    a = calcular_normais(a, crs=crs, debug=dbg, faces_ibge=faces_ibge)
    if dbg is not None and faces_ibge is not None:
        dbg.etapa("faces_ibge", faces_disponiveis=len(faces_ibge),
                  enderecos_com_direcao_ibge=int((a["fonte_direcao"] == "faces_ibge").sum()))
    a, hits, donos = atribuir(a, blds, debug=dbg)

    antes = _snapshot(a)
    a = refinar_por_ordem(a, blds, hits, donos=donos, debug=dbg)
    if bld_svd is not None:
        bld_ibge = a["bld"].to_numpy()
        mudaram = int(np.count_nonzero(bld_svd != bld_ibge))
        if dbg is not None:
            dbg.etapa("impacto_faces_ibge", enderecos_com_edificacao_alterada=mudaram,
                      enderecos_comparados=len(bld_ibge))
        print(f"Impacto Base de Faces IBGE: {mudaram:,}/{len(bld_ibge):,} endereços "
              "mudaram de edificação após atribuição e ordem.")
    a = avaliar_conflitos(a, blds)
    a, blds, df_condo = vincular_condominios(a, blds, perimetros_path=perimetros)
    a, blds, df_anexos = vincular_anexos(a, blds)
    a = checar_ordem(a)
    a = aplicar_modelo_probabilidade(a, modelo_path=modelo, corte=corte_prob)
    depois = _snapshot(a)
    diff = comparar_antes_depois(antes, depois)

    # Verificação obrigatória: nenhuma edificação pode ter endereços de mais de um face_key
    # (exceto vínculos do tipo anexo ou condomínio criados especificamente)
    principais = a[(a["bld"] >= 0) & (~a["tipo_vinculo"].isin(["anexo", "condominio"]))]
    conflitos = {bi: f for bi, f in principais.groupby("bld")["face_key"].unique().items() if len(f) > 1}
    if conflitos:
        raise RuntimeError(f"{len(conflitos)} edificações com endereços de múltiplas faces, ex.: {list(conflitos.items())[:3]}")

    if dbg is not None:
        _registrar_debug_diff(dbg, antes, depois, diff, crs)
        if len(df_anexos):
            dbg.table("anexos", df_anexos)
        if len(df_condo):
            dbg.table("condominios", df_condo)
        resumo = resumir_edificacoes(a, blds)
        dbg.layer("edificacoes", resumo)
        dbg.etapa(
            "checagem_ordem",
            faces_revisar=int(a.loc[a["revisar_face"].fillna(False), "face_key"].nunique()),
            anexos_identificados=len(df_anexos),
            condominios_identificados=len(df_condo),
        )
        dbg.escrever()
        dbg.imprimir()

    # O resultado final carrega o antes/depois de cada endereço.
    colunas_extras = ["bld_antes", "bld_depois", "metodo_antes",
                      "confianca_antes", "mudou_bld", "mudou_ponto", "deslocamento_m"]
    a = a.drop(columns=[c for c in colunas_extras if c in a.columns])
    extras = diff[["addr_key"] + colunas_extras]
    a = a.merge(extras, on="addr_key", how="left")
    a = gpd.GeoDataFrame(a, geometry="geometry", crs=blds.crs)
    a = a.set_geometry("geometry", crs=blds.crs)
    cols = ["addr_key", "endereco", "cep", "especie", "unidades", "bld", "bld_id", "bld_antes", "metodo",
            "metodo_antes", "confianca", "confianca_antes", "compartilhado", "motivo_confianca",
            "tipo_vinculo", "endereco_ref", "condominio_sem_perimetro", "prob_correto",
            "mudou_bld", "mudou_ponto", "deslocamento_m", "dp", "dp_custo", "share", "dmin",
            "acerto_raio_central", "ambiguo", "face_key", "face_rho", "fonte_direcao",
            "revisar_face", "geometry"]
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
    p.add_argument("saida", nargs="?", help="GPKG de saída do resultado final")
    p.add_argument("--relatorio-entrada", metavar="HTML",
                   help="gera o diagnóstico da entrada e encerra sem executar o algoritmo")
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
    p.add_argument("--perimetros", default=None,
                   help="GeoPackage/GeoJSON opcional com perímetros de condomínios/lotes")
    p.add_argument("--faces-ibge", default=None,
                   help="Base de Faces de Logradouros IBGE com CD_SETOR/CD_QUADRA/CD_FACE e linhas")
    p.add_argument("--medir-direcao", action="store_true",
                   help="compara com SVD e mede endereços que mudaram de edificação; requer --faces-ibge")
    p.add_argument("--modelo", default=None,
                   help="caminho do modelo scikit-learn (.joblib) treinado para prever acerto")
    p.add_argument("--params", default=None,
                   help="caminho de arquivo JSON com parâmetros calibrados para sobrescrever CFG")
    p.add_argument("--corte-prob", type=float, default=None,
                   help="corte de probabilidade para revisar (padrão: 0.60)")
    p.add_argument("--raio-max", type=float, default=None)
    p.add_argument("--profundidade", type=float, default=None)
    p.add_argument("--fallback-raio", type=float, default=None)
    p.add_argument("--ambiguo-m", type=float, default=None)
    p.add_argument("--decaimento", type=float, default=None)
    p.add_argument("--angulos", default=None,
                   help="lista separada por vírgula, ex.: -30,-15,0,15,30")
    args = p.parse_args()

    if args.relatorio_entrada:
        resultado = gerar_relatorio_entrada(args.cnefe, args.edificacoes, args.relatorio_entrada)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return resultado
    if not args.saida:
        p.error("informe saida ou use --relatorio-entrada HTML")

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
        perimetros=args.perimetros,
        modelo=args.modelo,
        params=args.params,
        corte_prob=args.corte_prob,
        faces_ibge_path=args.faces_ibge,
        medir_direcao=args.medir_direcao,
    )


if __name__ == "__main__":
    _cli()
