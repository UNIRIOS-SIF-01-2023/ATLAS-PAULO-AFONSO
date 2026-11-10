"""
avaliar.py
Avaliação, calibração via busca em grade e treinamento de modelo probabilístico
para atribuição de endereços CNEFE a edificações (Tarefa 5).
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.model_selection import StratifiedKFold, cross_val_predict

import cnefe_rooftop


def carregar_ground_truth(gt_path: str | Path) -> pd.DataFrame:
    """Lê planilha/arquivo de verdade (CSV, Parquet ou GPKG)."""
    p = Path(gt_path)
    if p.suffix.lower() == ".csv":
        df = pd.read_csv(p, sep=None, engine="python")
    elif p.suffix.lower() in (".parquet", ".pq"):
        df = pd.read_parquet(p)
    else:
        import geopandas as gpd
        df = gpd.read_file(p)

    cols_lower = {c: c.lower() for c in df.columns}
    df = df.rename(columns=cols_lower)

    # Identifica colunas esperadas
    id_col = next((c for c in ("bld_id", "gers_id", "id", "bld") if c in df.columns), None)
    end_col = next((c for c in ("endereco_esperado", "endereco", "addr", "logr_num") if c in df.columns), None)
    area_col = next((c for c in ("tipo_area", "area_tipo", "tipo", "zona") if c in df.columns), None)

    if not id_col or not end_col:
        raise ValueError(f"Ground truth deve conter identificador da edificação e endereço esperado. Colunas: {list(df.columns)}")

    df["bld_id_gt"] = df[id_col].astype(str).str.strip()
    df["endereco_esperado"] = df[end_col].astype(str).str.strip().str.upper()
    df["tipo_area"] = df[area_col].astype(str).str.strip() if area_col else "geral"

    return df


def avaliar_atribuicao(saida_atribuida: pd.DataFrame, df_gt: pd.DataFrame) -> dict:
    """Compara resultado gerado com ground truth e computa acertos."""
    df_gt = df_gt.copy()
    if "bld_id_gt" not in df_gt.columns:
        id_col = next((c for c in ("bld_id", "gers_id", "id", "bld") if c in df_gt.columns), None)
        df_gt["bld_id_gt"] = df_gt[id_col].astype(str).str.strip() if id_col else ""
    if "endereco_esperado" not in df_gt.columns:
        end_col = next((c for c in ("endereco_esperado", "endereco", "addr", "logr_num") if c in df_gt.columns), None)
        df_gt["endereco_esperado"] = df_gt[end_col].astype(str).str.strip().str.upper() if end_col else ""
    if "tipo_area" not in df_gt.columns:
        df_gt["tipo_area"] = "geral"

    df_pred = saida_atribuida.copy()
    if "bld_id" not in df_pred.columns and "bld" in df_pred.columns:
        df_pred["bld_id"] = df_pred["bld"].astype(str)
    df_pred["bld_id_str"] = df_pred["bld_id"].astype(str).str.strip()
    df_pred["endereco_pred"] = df_pred["endereco"].astype(str).str.strip().str.upper()

    m = df_gt.merge(df_pred, left_on="bld_id_gt", right_on="bld_id_str", how="inner")
    if len(m) == 0:
        return {"acerto_global": 0.0, "total": 0, "relatorio": {}}

    m["correto"] = m["endereco_esperado"] == m["endereco_pred"]

    def taxa(sub):
        return round(float(sub["correto"].mean()), 4) if len(sub) else 0.0

    res = {
        "acerto_global": taxa(m),
        "total_avaliado": len(m),
        "por_confianca": m.groupby("confianca", observed=False).apply(taxa).to_dict() if "confianca" in m else {},
        "por_metodo": m.groupby("metodo", observed=False).apply(taxa).to_dict() if "metodo" in m else {},
        "por_motivo": m.groupby("motivo_confianca", observed=False).apply(taxa).to_dict() if "motivo_confianca" in m else {},
        "por_tipo_area": m.groupby("tipo_area", observed=False).apply(taxa).to_dict(),
    }
    return res


def busca_em_grade(cnefe_path, edif_path, df_gt: pd.DataFrame, grade: dict | None = None) -> dict:
    """Busca em grade nos parâmetros principais salvando o melhor resultado."""
    if grade is None:
        grade = {
            "raio_max": [30.0, 40.0, 50.0],
            "decaimento": [10.0, 15.0, 20.0],
            "profundidade": [3.0, 4.0, 6.0],
            "fallback_raio": [20.0, 25.0, 30.0],
            "dp_cap": [6.0, 8.0, 10.0],
            "dp_pen": [4.0, 6.0, 8.0],
            "faixa_primeira_fileira": [4.0, 6.0, 8.0],
        }

    chaves = list(grade.keys())
    combinacoes = list(itertools.product(*[grade[k] for k in chaves]))
    melhor_score = -1.0
    melhores_params = {}

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_gpkg = Path(tmpdir) / "teste.gpkg"
        for comb in combinacoes:
            teste_cfg = dict(zip(chaves, comb))
            cnefe_rooftop.CFG.update(teste_cfg)
            try:
                res = cnefe_rooftop.processar(cnefe_path, edif_path, str(tmp_gpkg))
                metr = avaliar_atribuicao(res, df_gt)
                score = metr["acerto_global"]
                if score > melhor_score:
                    melhor_score = score
                    melhores_params = teste_cfg
            except Exception:
                continue

    return {"melhores_params": melhores_params, "acerto": melhor_score}


def treinar_modelo_calibracao(saida_atribuida: pd.DataFrame, df_gt: pd.DataFrame, output_model: str | Path):
    """Treina LogisticRegression para prever acerto, avalia calibração e persiste modelo."""
    df_pred = saida_atribuida.copy()
    if "bld_id" not in df_pred.columns and "bld" in df_pred.columns:
        df_pred["bld_id"] = df_pred["bld"].astype(str)
    df_pred["bld_id_str"] = df_pred["bld_id"].astype(str).str.strip()
    df_pred["endereco_pred"] = df_pred["endereco"].astype(str).str.strip().str.upper()

    m = df_gt.merge(df_pred, left_on="bld_id_gt", right_on="bld_id_str", how="inner")
    if len(m) < 10:
        raise ValueError(f"Amostra insuficiente ({len(m)} itens) para treinar modelo de calibração.")

    m["correto"] = (m["endereco_esperado"] == m["endereco_pred"]).astype(int)

    X = cnefe_rooftop.extrair_features_modelo(m)
    y = m["correto"].to_numpy()

    # Validação cruzada estratificada
    n_splits = min(5, max(2, int(np.bincount(y).min())))
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

    modelo = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)

    probs_cv = cross_val_predict(modelo, X, y, cv=skf, method="predict_proba")[:, 1]
    brier = float(brier_score_loss(y, probs_cv))
    loss = float(log_loss(y, probs_cv))
    acc = float(accuracy_score(y, (probs_cv >= 0.5).astype(int)))

    # Treinamento final em todos os dados
    modelo.fit(X, y)

    # Salva o modelo
    out_p = Path(output_model)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, out_p)

    prob_true, prob_pred = calibration_curve(y, probs_cv, n_bins=5, strategy="uniform")

    info = {
        "acuracia_cv": round(acc, 4),
        "brier_score": round(brier, 4),
        "log_loss": round(loss, 4),
        "calibracao": {
            "prob_true": [round(float(v), 4) for v in prob_true],
            "prob_pred": [round(float(v), 4) for v in prob_pred],
        },
        "modelo_salvo": str(out_p),
    }
    return modelo, info


def _cli():
    p = argparse.ArgumentParser(description="Avaliação e calibração do CNEFE Rooftop.")
    p.add_argument("ground_truth", help="Planilha CSV/Parquet de verdade (bld_id, endereco_esperado)")
    p.add_argument("--saida-gpkg", default=None, help="GPKG já processado para avaliar")
    p.add_argument("--cnefe", default=None, help="CNEFE para rodar busca em grade")
    p.add_argument("--edificacoes", default=None, help="Edificações para rodar busca em grade")
    p.add_argument("--grid-search", action="store_true", help="Executa busca em grade")
    p.add_argument("--params-out", default="params_calibrados.json", help="Arquivo JSON de saída dos parâmetros")
    p.add_argument("--treinar-modelo", default=None, help="Caminho .joblib para treinar e salvar o modelo")

    args = p.parse_args()
    gt = carregar_ground_truth(args.ground_truth)

    if args.grid_search:
        if not args.cnefe or not args.edificacoes:
            raise ValueError("--cnefe e --edificacoes são obrigatórios para --grid-search")
        res_grid = busca_em_grade(args.cnefe, args.edificacoes, gt)
        Path(args.params_out).write_text(json.dumps(res_grid["melhores_params"], indent=2), encoding="utf-8")
        print(f"Melhores parâmetros salvos em {args.params_out} (Score: {res_grid['acerto']:.4f})")

    if args.saida_gpkg:
        import geopandas as gpd
        pred = gpd.read_file(args.saida_gpkg, layer="enderecos_rooftop")
        res = avaliar_atribuicao(pred, gt)
        print("Métricas de Avaliação:")
        print(json.dumps(res, indent=2, ensure_ascii=False))

        if args.treinar_modelo:
            _, info = treinar_modelo_calibracao(pred, gt, args.treinar_modelo)
            print("Treinamento do modelo concluído:")
            print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
