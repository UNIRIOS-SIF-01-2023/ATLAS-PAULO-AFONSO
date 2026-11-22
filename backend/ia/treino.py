"""Treino e avaliação do classificador de uso (chamado por scripts/treinar_uso.py)."""

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .dados import Base
from .features import (CELULA_GRUPO_M, CLASSES, RAIO_PERTO_M, RAIO_VIZINHANCA_M, grupos_espaciais,
                       mascara_previsao, mascara_treino, montar_features)

SEMENTE = 42
MODELOS = ("mais_frequente", "regressao_logistica", "random_forest", "gradient_boosting")
GRADES = {
    "random_forest": [{"n_estimators": n, "min_samples_leaf": m} for n in (200, 400) for m in (1, 3)],
    "gradient_boosting": [{"learning_rate": lr, "max_leaf_nodes": f} for lr in (0.05, 0.1) for f in (15, 31)],
}
N_ERROS = 20


def _estimador(nome, params, seed):
    p = {k: v for k, v in params.items() if k != "modelo"}
    if nome == "mais_frequente":
        return DummyClassifier(strategy="most_frequent")
    if nome == "regressao_logistica":
        return make_pipeline(StandardScaler(), LogisticRegression(
            class_weight="balanced", max_iter=2000, random_state=seed))
    if nome == "random_forest":
        return RandomForestClassifier(class_weight="balanced_subsample", n_jobs=-1, random_state=seed, **p)
    if nome == "gradient_boosting":
        return HistGradientBoostingClassifier(class_weight="balanced", random_state=seed, **p)
    raise ValueError(f"modelo desconhecido: {nome}")


def _calibrado(params, seed, X, y, grupos):
    """Modelo escolhido com probabilidades calibradas em folds espaciais."""
    cv = list(StratifiedGroupKFold(3, shuffle=True, random_state=seed).split(X, y, grupos))
    return CalibratedClassifierCV(_estimador(params["modelo"], params, seed),
                                  method="sigmoid", cv=cv).fit(X, y)


def _folds(base, seed):
    """Features recalculadas por fold, com os rótulos do teste escondidos da vizinhança."""
    treino = mascara_treino(base.tipos, base.origens)
    idx = np.flatnonzero(treino)
    y = np.array(base.tipos, dtype=object)[idx]
    grupos = grupos_espaciais(base.pontos[idx])
    esquemas = {
        "estratificada": StratifiedKFold(5, shuffle=True, random_state=seed).split(idx, y),
        "espacial": StratifiedGroupKFold(5, shuffle=True, random_state=seed).split(idx, y, grupos),
    }
    saida = {}
    for nome, splits in esquemas.items():
        folds = []
        for tr, te in splits:
            conhecidos = treino.copy()
            conhecidos[idx[te]] = False
            X, _ = montar_features(base.geoms, base.fontes, base.tipos, conhecidos)
            folds.append({"X_tr": X[idx[tr]], "y_tr": y[tr], "g_tr": grupos[tr],
                          "X_te": X[idx[te]], "y_te": y[te], "linhas_te": idx[te]})
        saida[nome] = folds
    return saida, [c for c in CLASSES if c in set(y)]


def _metricas(y, pred, classes):
    p, r, f, s = precision_recall_fscore_support(y, pred, labels=classes, zero_division=0)
    return {
        "f1_macro": float(f1_score(y, pred, labels=classes, average="macro", zero_division=0)),
        "acuracia": float(accuracy_score(y, pred)),
        "por_classe": {c: {"precisao": float(p[i]), "recall": float(r[i]), "f1": float(f[i]),
                           "suporte": int(s[i])} for i, c in enumerate(classes)},
        "matriz_confusao": confusion_matrix(y, pred, labels=classes).tolist(),
    }


def _oof(folds, fazer):
    """Junta as previsões de cada fold de teste."""
    ys, preds = [], []
    for f in folds:
        ys.append(f["y_te"])
        preds.append(fazer(f).predict(f["X_te"]))
    return np.concatenate(ys), np.concatenate(preds)


def _avaliar_um(folds, params, seed, classes):
    y, pred = _oof(folds, lambda f: _estimador(params["modelo"], params, seed).fit(f["X_tr"], f["y_tr"]))
    return _metricas(y, pred, classes)


def _modelo_final(base, folds, params, seed, classes):
    """Métricas, calibração (Brier) e erros mais confiantes do modelo calibrado, CV espacial."""
    ys, probas, linhas = [], [], []
    for f in folds:
        m = _calibrado(params, seed, f["X_tr"], f["y_tr"], f["g_tr"])
        p = np.zeros((len(f["y_te"]), len(classes)))
        for k, c in enumerate(m.classes_):
            p[:, classes.index(c)] = m.predict_proba(f["X_te"])[:, k]
        ys.append(f["y_te"]); probas.append(p); linhas.append(f["linhas_te"])
    y, proba, linhas = np.concatenate(ys), np.vstack(probas), np.concatenate(linhas)
    pred = np.array(classes, dtype=object)[proba.argmax(axis=1)]
    conf = proba.max(axis=1)
    brier = {c: float(np.mean((proba[:, k] - (y == c)) ** 2)) for k, c in enumerate(classes)}
    errados = np.flatnonzero(pred != y)
    errados = errados[np.argsort(-conf[errados], kind="stable")][:N_ERROS]
    return {
        **_metricas(y, pred, classes),
        "brier_por_classe": brier,
        "confianca_media": float(conf.mean()),
        "erros_mais_confiantes": [
            {"id": int(base.ids[linhas[i]]), "oficial": str(y[i]), "previsto": str(pred[i]),
             "confianca": round(float(conf[i]), 3)} for i in errados],
    }


def avaliar(base: Base, seed: int = SEMENTE) -> dict:
    """Compara os modelos em CV estratificada e espacial, ajusta o melhor e o calibra."""
    folds, classes = _folds(base, seed)
    modelos = {nome: {cv: _avaliar_um(folds[cv], {"modelo": nome}, seed, classes) for cv in folds}
               for nome in MODELOS}
    candidato = max(GRADES, key=lambda n: modelos[n]["espacial"]["f1_macro"])
    grade = []
    for p in GRADES[candidato]:
        params = {"modelo": candidato, **p}
        grade.append({"params": params,
                      "f1_macro": _avaliar_um(folds["espacial"], params, seed, classes)["f1_macro"]})
    melhor = max(grade, key=lambda g: g["f1_macro"])
    treino = mascara_treino(base.tipos, base.origens)
    return {
        "classes": classes,
        "dados": {
            "n_total": len(base.tipos),
            "n_treino": int(treino.sum()),
            "n_previsao": int(mascara_previsao(base.tipos).sum()),
            "contagem_por_classe": dict(Counter(t for t, ok in zip(base.tipos, treino) if ok)),
        },
        "modelos": modelos,
        "grade": grade,
        "melhor": {"modelo": candidato, "params": melhor["params"], "f1_macro_espacial": melhor["f1_macro"]},
        "modelo_final": _modelo_final(base, folds["espacial"], melhor["params"], seed, classes),
    }


def treinar_final(base: Base, params: dict, seed: int = SEMENTE) -> dict:
    """Modelo calibrado com todos os rotulados; é o que a API carrega."""
    treino = mascara_treino(base.tipos, base.origens)
    X, nomes = montar_features(base.geoms, base.fontes, base.tipos, treino)
    idx = np.flatnonzero(treino)
    y = np.array(base.tipos, dtype=object)[idx]
    modelo = _calibrado(params, seed, X[idx], y, grupos_espaciais(base.pontos[idx]))
    assinatura = json.dumps({"params": params, "nomes": nomes}, sort_keys=True).encode()
    return {
        "modelo": modelo,
        "classes": [str(c) for c in modelo.classes_],
        "nomes_features": nomes,
        "params": params,
        "versao": f"uso-{datetime.now(timezone.utc):%Y%m%d}-{hashlib.sha1(assinatura).hexdigest()[:8]}",
        "raios": {"perto_m": RAIO_PERTO_M, "vizinhanca_m": RAIO_VIZINHANCA_M, "celula_grupo_m": CELULA_GRUPO_M},
    }


ROTULO_MODELO = {"mais_frequente": "Classe mais frequente (baseline)",
                 "regressao_logistica": "Regressão logística (baseline)",
                 "random_forest": "Random Forest", "gradient_boosting": "Gradient Boosting"}


def _relatorio(payload, metricas, treinado_em):
    m, final, classes = metricas, metricas["modelo_final"], metricas["classes"]
    d = m["dados"]
    linhas = [
        "# Avaliação do classificador de uso das edificações", "",
        f"Gerado por `scripts/treinar_uso.py` em {treinado_em}. Modelo `{payload['versao']}`, semente {SEMENTE}.", "",
        "## Dados", "",
        f"- Prédios: {d['n_total']}; rotulados pelo CNEFE (treino): {d['n_treino']}; "
        f"sem uso conhecido (recebem previsão): {d['n_previsao']}.", "",
        "| Classe | Prédios rotulados |", "|---|---|",
        *[f"| {c} | {d['contagem_por_classe'].get(c, 0)} |" for c in classes], "",
        "## Método", "",
        "Features só do que todo prédio tem: forma do polígono (área, perímetro, compacidade, retângulo "
        "mínimo, vértices), fonte do footprint e vizinhança (densidade, área dos vizinhos, uso dos vizinhos "
        f"rotulados num raio de {RAIO_VIZINHANCA_M} m, distância ao comercial mais próximo). Bairro, "
        "endereço e pavimentos ficam de fora (só os rotulados têm).", "",
        "Validação cruzada em 5 folds, de dois jeitos: estratificada (sorteio de prédios) e espacial "
        f"(células de {CELULA_GRUPO_M} m inteiras no teste). Em cada fold os rótulos do teste ficam "
        "escondidos das features de vizinhança. A espacial é a mais honesta para prédios sem endereço.", "",
        "## Comparação de modelos (F1 macro)", "",
        "| Modelo | Estratificada | Espacial | Acurácia (espacial) |", "|---|---|---|---|",
        *[f"| {ROTULO_MODELO[n]} | {r['estratificada']['f1_macro']:.3f} | {r['espacial']['f1_macro']:.3f} "
          f"| {r['espacial']['acuracia']:.3f} |" for n, r in m["modelos"].items()], "",
        f"## Ajuste de hiperparâmetros ({ROTULO_MODELO[m['melhor']['modelo']]}, CV espacial)", "",
        "| Parâmetros | F1 macro |", "|---|---|",
        *[f"| {', '.join(f'{k}={v}' for k, v in g['params'].items() if k != 'modelo')} | {g['f1_macro']:.3f} |"
          for g in m["grade"]], "",
        f"Escolhido: {m['melhor']['params']}.", "",
        "## Modelo final (calibrado, CV espacial)", "",
        f"F1 macro {final['f1_macro']:.3f}, acurácia {final['acuracia']:.3f}, "
        f"confiança média {final['confianca_media']:.2f}.", "",
        "| Classe | Precisão | Recall | F1 | Suporte | Brier |", "|---|---|---|---|---|---|",
        *[f"| {c} | {v['precisao']:.2f} | {v['recall']:.2f} | {v['f1']:.2f} | {v['suporte']} "
          f"| {final['brier_por_classe'][c]:.3f} |" for c, v in final["por_classe"].items()], "",
        "### Matriz de confusão (linhas = oficial, colunas = previsto)", "",
        "| | " + " | ".join(classes) + " |", "|---" * (len(classes) + 1) + "|",
        *[f"| {c} | " + " | ".join(str(n) for n in linha) + " |"
          for c, linha in zip(classes, final["matriz_confusao"])], "",
        "### Erros mais confiantes", "",
        "| Prédio | Oficial | Previsto | Confiança |", "|---|---|---|---|",
        *[f"| {e['id']} | {e['oficial']} | {e['previsto']} | {e['confianca']:.2f} |"
          for e in final["erros_mais_confiantes"]], "",
        "## Limites", "",
        "- Viés de seleção: os prédios rotulados são justamente os que têm endereço no CNEFE; os sem "
        "endereço podem ter outro perfil (anexos, galpões, área rural). A CV espacial mede isso só em parte.",
        "- Classes raras (poucas dezenas de exemplos) têm F1 baixo e quase nunca são previstas.",
        "- A previsão é uma estimativa, nunca dado oficial. Serve para priorizar conferência de campo e "
        "ler tendências, não para decidir sobre um prédio específico.", "",
    ]
    return "\n".join(linhas)


def escrever_saidas(payload: dict, metricas: dict, pasta_modelo: Path, relatorio: Path) -> None:
    pasta_modelo, relatorio = Path(pasta_modelo), Path(relatorio)
    pasta_modelo.mkdir(parents=True, exist_ok=True)
    relatorio.parent.mkdir(parents=True, exist_ok=True)
    caminho = pasta_modelo / "modelo_uso.joblib"
    joblib.dump(payload, caminho, compress=3)
    treinado_em = datetime.now(timezone.utc).isoformat(timespec="seconds")
    info = {
        "versao": payload["versao"], "treinado_em": treinado_em, "semente": SEMENTE,
        "params": payload["params"], "raios": payload["raios"],
        "contagem_por_classe": metricas["dados"]["contagem_por_classe"], "metricas": metricas,
    }
    (pasta_modelo / "modelo_uso.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    relatorio.write_text(_relatorio(payload, metricas, treinado_em), encoding="utf-8")
    mb = caminho.stat().st_size / 1e6
    if mb > 10:
        print(f"AVISO: {caminho} tem {mb:.1f} MB (> 10 MB): não versionar, ver README.")
