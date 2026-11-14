"""Compara o algoritmo clássico com o algoritmo_raycast sobre a mesma entrada.

Roda as duas implementações com os mesmos parâmetros de edificações e resume
as diferenças: contagem por método/confiança, quantos endereços mudaram de
edificação e onde. Útil para validar o motor novo antes de aposentar o antigo.

Uso:
    ./.venv/bin/python scripts/comparar_algoritmos.py \
        data/raw/qg/qg_810_endereco_Munic2924009.json mysql
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cnefe_rooftop_classico as classico  # noqa: E402
import algoritmo_raycast as raycast  # noqa: E402


def _carregar_pontos(gpkg: Path):
    import geopandas as gpd

    return gpd.read_file(gpkg, layer="enderecos_rooftop")


def _resumo(pontos, rotulo: str) -> dict:
    col_metodo = "metodo" if "metodo" in pontos.columns else "tipo_vinculo"
    col_conf = "confianca" if "confianca" in pontos.columns else None
    por_metodo = pontos[col_metodo].value_counts().to_dict() if col_metodo in pontos else {}
    por_conf = pontos[col_conf].value_counts().to_dict() if col_conf else {}
    print(f"\n[{rotulo}] {len(pontos)} pontos | por método: {por_metodo} | confiança: {por_conf}")
    return {"n": len(pontos), "metodo": por_metodo, "conf": por_conf}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    cnefe, edif = sys.argv[1], sys.argv[2]

    base = Path("data/processed/comparacao")
    base.mkdir(parents=True, exist_ok=True)
    gp_classico = base / "pontos_classico.gpkg"
    gp_novo = base / "pontos_raycast.gpkg"

    print("== algoritmo clássico ==")
    classico.processar(cnefe, edif, str(gp_classico))
    print("\n== algoritmo_raycast (ativo) ==")
    raycast.processar(cnefe, edif, str(gp_novo))

    pc = _carregar_pontos(gp_classico)
    pn = _carregar_pontos(gp_novo)
    _resumo(pc, "clássico")
    _resumo(pn, "raycast")

    chave_c = "addr_key" if "addr_key" in pc.columns else None
    chave_n = "addr_key" if "addr_key" in pn.columns else None
    if not (chave_c and chave_n):
        print("AVISO: colunas addr_key ausentes; comparação por chave impossível.")
        return 1
    set_c = set(pc[chave_c].astype(str))
    set_n = set(pn[chave_n].astype(str))
    so_novo = set_n - set_c
    so_classico = set_c - set_n
    print(f"\nendereços só no raycast: {len(so_novo)}")
    print(f"endereços só no clássico: {len(so_classico)}")
    if len(so_novo) <= 40:
        print("exemplos só no raycast:", sorted(so_novo)[:40])
    else:
        print("exemplos só no raycast:", sorted(so_novo)[:20], "…")

    # onde os dois concordam, o prédio escolhido é o mesmo?
    col_cl = "bld_id" if "bld_id" in pc.columns and pc["bld_id"].astype(str).str.strip().replace("", pd.NA).notna().any() else "bld"
    col_nov = "bld_id" if "bld_id" in pn.columns and pn["bld_id"].astype(str).str.strip().replace("", pd.NA).notna().any() else "bld"
    p_c = pc.set_index(chave_c)[[col_cl, "geometry"]].rename(columns={col_cl: "bld_cl"})
    p_n = pn.set_index(chave_n)[[col_nov, "geometry"]].rename(columns={col_nov: "bld_nov"})
    j = p_c.join(p_n, how="inner", lsuffix="_c", rsuffix="_n")
    if "bld_cl" in j and "bld_nov" in j:
        mesmos = (j["bld_cl"].astype(str) == j["bld_nov"].astype(str)).sum()
        print(f"concordam no endereço: {len(j)} | mesma edificação ({col_cl}): {mesmos} | divergem: {len(j) - mesmos}")
    dist_m = j["geometry_c"].to_crs(32424).distance(j["geometry_n"].to_crs(32424))
    print(f"distância entre pontos (m): mediana {dist_m.median():.1f} | máx {dist_m.max():.1f}")
    if len(j):
        grandes = j.loc[dist_m > 15, ["bld_cl", "bld_nov"]].head(10)
        if len(grandes):
            print("endereços com pontos afastados >15m:", grandes.to_dict("index"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
