# Building-use classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Predict a use type and a calibrated confidence for every `desconhecido` building, and show it in the API, the map, the card, the report and a "Sobre o modelo" section, always marked as an AI estimate.

**Architecture:** A shared, pure feature module (`backend/ia/features.py`) used by an offline training script (`scripts/treinar_uso.py`, writes a `.joblib` + metrics JSON + Portuguese evaluation report) and by the API. The API loads the model in the lifespan, predicts all unknowns once per data version (memoised like `_overview_for`), and annotates tiles, detail and stats from that in-memory table. No DB schema change.

**Tech Stack:** Python 3.14, FastAPI, shapely 2, numpy, scikit-learn (new), joblib; React + TypeScript + MapLibre; pytest; `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-22-classificador-uso-design.md` — read it before any task.

## Global Constraints

- No DB schema change and no importer change.
- Never use bairro, address count, `ORIGEM_TIPO` or `NUM_PAVIMENTOS` as features.
- Classes, in this order: `residencial, comercial, servico, religioso, institucional, hospedagem, agropecuario`.
- Training rows: `ORIGEM_TIPO == 'cnefe'` and `TIPO_USO != 'desconhecido'`. Prediction rows: every `TIPO_USO == 'desconhecido'`. Overture-subtype labelled rows: neither.
- Fixed seed `42` everywhere randomness exists.
- Official `tipo` is never overwritten; predictions travel in separate fields `tipo_previsto`, `confianca` (2 decimals in tiles), `probabilidades` (detail/endpoint only).
- `confianca` = highest class probability of the **calibrated** model (`CalibratedClassifierCV`, `method="sigmoid"`, grouped folds).
- New dependency: `scikit-learn` pinned to the exact installed version (`scikit-learn==X.Y.Z`) in `backend/requirements.txt`. No pandas, no matplotlib, no LightGBM, no pyproj.
- Model files: `backend/ia/modelo_uso.joblib`, `backend/ia/modelo_uso.json`. Evaluation report: `docs/ia/avaliacao_modelo.md` (Portuguese).
- All new routes require auth (`Depends(auth.get_current_user)`), like every existing data route.
- User-facing copy in Portuguese: badge "Previsto por IA", switch "Mostrar usos previstos por IA" (default on), section title "Sobre o modelo".
- Code comments and identifiers follow the repo's Portuguese style.

## Review Focus

- Model file missing, corrupt or pickled with another sklearn version → API starts, logs a warning, tiles/detail have no prediction fields, prediction routes answer 503. (Task 4 test `test_modelo_ausente_ou_corrompido_api_segue_sem_previsao`.)
- Isolated building (no neighbour within the radii, no labelled neighbour, unseen `fonte` name) → finite features (no NaN/inf) and a prediction is still produced. (Task 1 test `test_predio_isolado_e_fonte_nova_geram_features_finitas`.)
- Tile with a mix of predicted and official buildings → encodes; official ones carry no `tipo_previsto` key at all (MVT can't encode `None`). (Task 4 test `test_tile_so_desconhecidos_levam_previsao`.)
- Re-import changes the data version → predictions recomputed for the new version; concurrent first requests compute once. (Task 4 test `test_previsoes_por_versao_calcula_uma_vez`.)
- `POST /api/predict-uso` with `"abc"`, `""`, `"-1"`, missing body → 422; non-existent id → 404. (Task 5 test `test_predict_uso_valida_entrada`.)

---

### Task 1: Shared feature module

**Files:**
- Create: `backend/ia/__init__.py` (empty), `backend/ia/features.py`
- Test: `backend/tests/test_ia_features.py`

**Interfaces:**
- Produces:
  - `CLASSES: tuple[str, ...]` (the 7 classes, order from Global Constraints)
  - `FONTES_CONHECIDAS = ("Google Open Buildings", "Microsoft ML Buildings", "OpenStreetMap")` (+ an "outra" column)
  - `RAIO_PERTO_M = 50`, `RAIO_VIZINHANCA_M = 150`, `CELULA_GRUPO_M = 500`
  - `mascara_treino(tipos: Sequence[str], origens: Sequence[str]) -> np.ndarray[bool]`
  - `mascara_previsao(tipos: Sequence[str]) -> np.ndarray[bool]`
  - `montar_features(geoms: np.ndarray, fontes: Sequence[str], tipos: Sequence[str], conhecidos: np.ndarray[bool]) -> tuple[np.ndarray, list[str]]` — geoms are shapely polygons in **lon/lat**; `conhecidos[i]` says whether building i's label may be used as neighbour context; returns `(X float64 [n, k], nomes)`.
  - `grupos_espaciais(pontos: np.ndarray) -> np.ndarray[int]` — grid-cell id of `CELULA_GRUPO_M` for each representative point.
  - `bairro_mais_proximo(pontos: np.ndarray, bairros: Sequence[str | None]) -> list[str | None]` — for buildings with `None`, the bairro of the nearest building that has one; others keep theirs.

- [ ] **Step 1: Write the failing tests**

```python
def test_quadrado_tem_compacidade_de_quadrado():
    # 20 m x 20 m square near Paulo Afonso (lat -9.4)
    X, nomes = montar_features(np.array([quadrado(-38.22, -9.40, 20)]), ["OpenStreetMap"], ["residencial"], np.array([True]))
    f = dict(zip(nomes, X[0]))
    assert f["area_m2"] == pytest.approx(400, rel=0.03)
    assert f["perimetro_m"] == pytest.approx(80, rel=0.03)
    assert f["compacidade"] == pytest.approx(math.pi / 4, rel=0.03)

def test_proprio_rotulo_nao_entra_na_vizinhanca():
    # building 0 is comercial and alone with one residencial neighbour 30 m away
    X, nomes = montar_features(dois_predios(), ["OpenStreetMap"] * 2, ["comercial", "residencial"], np.array([True, True]))
    f0 = dict(zip(nomes, X[0]))
    assert f0["viz_frac_comercial"] == 0 and f0["viz_frac_residencial"] == 1

def test_rotulo_escondido_nao_vaza():
    X, nomes = montar_features(dois_predios(), ["OpenStreetMap"] * 2, ["comercial", "residencial"], np.array([True, False]))
    f0 = dict(zip(nomes, X[0]))
    assert f0["viz_frac_residencial"] == 0 and f0["viz_frac_rotulados"] == 0

def test_nenhuma_feature_proibida():
    _, nomes = montar_features(dois_predios(), ["OpenStreetMap"] * 2, ["comercial", "residencial"], np.array([True, True]))
    assert not any(p in n for n in nomes for p in ("bairro", "endereco", "origem", "pavimento"))

def test_predio_isolado_e_fonte_nova_geram_features_finitas():
    X, nomes = montar_features(np.array([quadrado(-38.0, -9.0, 10)]), ["Fonte Nova"], ["desconhecido"], np.array([False]))
    assert np.isfinite(X).all()
    assert dict(zip(nomes, X[0]))["fonte_outra"] == 1

def test_mascaras():
    tipos = ["residencial", "desconhecido", "comercial", "desconhecido"]
    origens = ["cnefe", "sem_dado", "overture_subtype", "cnefe"]
    assert mascara_treino(tipos, origens).tolist() == [True, False, False, False]
    assert mascara_previsao(tipos).tolist() == [False, True, False, True]

def test_bairro_mais_proximo():
    pontos = np.array([[-38.22, -9.40], [-38.2201, -9.40], [-38.30, -9.40]])
    assert bairro_mais_proximo(pontos, ["CENTRO", None, "BTN"]) == ["CENTRO", "CENTRO", "BTN"]

def test_grupos_espaciais_separam_celulas_de_500m():
    g = grupos_espaciais(np.array([[-38.22, -9.40], [-38.2201, -9.40], [-38.21, -9.40]]))
    assert g[0] == g[1] and g[0] != g[2]
```

(`quadrado(lng, lat, lado_m)` and `dois_predios()` are small helpers in the test file.)

- [ ] **Step 2: Run** `./.venv/Scripts/python -m pytest backend/tests/test_ia_features.py -q` — expected: FAIL (module not found).
- [ ] **Step 3: Implement `backend/ia/features.py`.** Metres via local scaling: x·111320·cos(lat), y·111320 around each building's representative point (vectorised with `shapely.transform`). Feature list (names are the contract the tests use): `area_m2, perimetro_m, compacidade, ret_comprimento_m, ret_largura_m, alongamento, ocupacao_ret, n_vertices, fonte_google, fonte_microsoft, fonte_osm, fonte_outra, n_perto_50m, n_viz_150m, viz_area_media_m2, viz_area_mediana_m2, viz_frac_rotulados, viz_frac_<classe> ×7, dist_comercial_m`. Neighbours via one `shapely.STRtree` on representative points projected to metres, `query(..., predicate="dwithin", distance=r)`, self removed. Empty neighbourhood → 0 for counts/shares, 0 for area stats; no labelled comercial → `dist_comercial_m = 5000` (cap; comment the cap with the `shortcut:` form).
- [ ] **Step 4: Run** the same command — expected: all pass.
- [ ] **Step 5: Commit** `feat(ia): features geométricas e de vizinhança para o classificador de uso`

---

### Task 2: Training script with evaluation

**Files:**
- Create: `backend/ia/dados.py`, `scripts/treinar_uso.py`
- Modify: `backend/db.py` (add `fetch_buildings_for_model`), `backend/requirements.txt`, `.gitignore` (nothing to add unless the model ends up > 10 MB)
- Test: `backend/tests/test_ia_treino.py`

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces:
  - `db.fetch_buildings_for_model() -> list[dict]` — keys `id:int, wkb:bytes, tipo:str, origem:str, fonte:str, bairro:str|None`; one query: `SELECT E.ID, ST_AsBinary(E.GEOM), E.TIPO_USO, E.ORIGEM_TIPO, F.NOME, B.NOME FROM EDIFICACOES E JOIN FONTES F … LEFT JOIN BAIRROS B …`.
  - `dados.Base` dataclass: `ids: np.ndarray[int], geoms: np.ndarray (lon/lat), pontos: np.ndarray [n,2] lon/lat, tipos: list[str], origens: list[str], fontes: list[str], bairros: list[str|None]`.
  - `dados.de_linhas(rows: list[dict]) -> Base` — WKB decode + axis flip exactly like `db.fetch_building_tile` (SRID 4674 stores lat/lon).
  - In `scripts/treinar_uso.py`: `avaliar(base: Base, seed: int = 42) -> dict`, `treinar_final(base: Base, params: dict, seed: int = 42) -> dict` (the joblib payload), `escrever_saidas(payload: dict, metricas: dict, pasta_modelo: Path, relatorio: Path) -> None`, `main()`.
  - Joblib payload keys: `modelo` (fitted calibrated pipeline), `classes` (list), `nomes_features` (list), `versao` (str: `"uso-" + training date + "-" + first 8 hex of sha1 of the joblib params/feature names`), `raios` (dict of the Task 1 constants).

- [ ] **Step 1: Install and pin.** `./.venv/Scripts/python -m pip install scikit-learn`, then add the exact installed version line to `backend/requirements.txt`.
- [ ] **Step 2: Write the failing tests** on a synthetic `Base` (~300 buildings on a grid: big squares labelled `comercial` clustered in one corner, small ones `residencial`, a few `desconhecido`):

```python
def test_avaliar_tem_baselines_modelos_e_metricas_por_classe(base_sintetica):
    m = avaliar(base_sintetica)
    assert set(m["modelos"]) >= {"mais_frequente", "regressao_logistica", "random_forest", "gradient_boosting"}
    for nome in m["modelos"]:
        for cv in ("estratificada", "espacial"):
            r = m["modelos"][nome][cv]
            assert {"f1_macro", "acuracia", "por_classe", "matriz_confusao"} <= set(r)
    assert m["melhor"]["modelo"] in m["modelos"] and "params" in m["melhor"]

def test_avaliar_e_reprodutivel(base_sintetica):
    assert avaliar(base_sintetica) == avaliar(base_sintetica)

def test_treino_final_preve_probabilidades_somando_1(base_sintetica):
    p = treinar_final(base_sintetica, {"modelo": "random_forest"})
    X, _ = montar_features(base_sintetica.geoms, base_sintetica.fontes, base_sintetica.tipos, mascara_treino(base_sintetica.tipos, base_sintetica.origens))
    proba = p["modelo"].predict_proba(X[:5])
    assert np.allclose(proba.sum(axis=1), 1)
    assert set(p["classes"]) == {"comercial", "residencial"}  # only classes present in the synthetic labels

def test_escrever_saidas_gera_joblib_json_e_relatorio(tmp_path, base_sintetica):
    ...  # files exist; JSON has "metricas", "contagem_por_classe", "versao", "semente": 42, "treinado_em"; report contains "Matriz de confusão" and "F1"
```

- [ ] **Step 3: Run** `./.venv/Scripts/python -m pytest backend/tests/test_ia_treino.py -q` — expected: FAIL.
- [ ] **Step 4: Implement.**
  - `avaliar`: two CV schemes, `StratifiedKFold(5, shuffle=True, random_state=42)` and `StratifiedGroupKFold(5, shuffle=True, random_state=42)` with groups from `grupos_espaciais`. **Per fold**, recompute features with `conhecidos = mascara_treino & ~test_fold` (labels of held-out rows hidden). Models: `DummyClassifier("most_frequent")`, `make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000))`, `RandomForestClassifier(class_weight="balanced_subsample", n_jobs=-1)`, `HistGradientBoostingClassifier(class_weight="balanced")`. Metrics with `zero_division=0`. Then a small grid on the better of RF/HGB by spatial-CV macro-F1 (RF: `n_estimators ∈ {200, 400}`, `min_samples_leaf ∈ {1, 3}`; HGB: `learning_rate ∈ {0.05, 0.1}`, `max_leaf_nodes ∈ {15, 31}`). Also record the 20 most confident wrong predictions of the best model (id, true, predicted, confidence) for error analysis, plus Brier score per class of the calibrated best model.
  - `treinar_final`: features with `conhecidos = mascara_treino`, fit best estimator wrapped in `CalibratedClassifierCV(method="sigmoid", cv=<list of StratifiedGroupKFold(3) splits>)` on training rows.
  - `escrever_saidas`: `joblib.dump(payload, compress=3)`; JSON (UTF-8, `ensure_ascii=False`); markdown report in Portuguese: data summary (counts per class, unknowns), method, table of macro-F1 per model × CV scheme, per-class P/R/F1 of the chosen model, confusion matrix as a table, hyperparameter results, most confident errors, calibration, limits (selection bias, rare classes). Warn on stdout if the joblib is > 10 MB.
  - `main()`: `rows = db.fetch_buildings_for_model()` → `de_linhas` → `avaliar` → `treinar_final` → `escrever_saidas`. Run as `doppler run -- ./.venv/Scripts/python -m scripts.treinar_uso` (add `scripts/__init__.py` only if needed for `-m`; otherwise run as a file with the repo root on `sys.path`, like the importer).
- [ ] **Step 5: Run** the tests — expected: pass. Also run the whole suite: `JWT_SECRET=x ./.venv/Scripts/python -m pytest backend/tests importacao -q` — expected: no new failures.
- [ ] **Step 6: Commit** `feat(ia): script de treino e avaliação do classificador de uso`

---

### Task 3: Train on the real data (needs Doppler)

**Files:** produces `backend/ia/modelo_uso.joblib`, `backend/ia/modelo_uso.json`, `docs/ia/avaliacao_modelo.md`; Modify: `README.md` (one short "Classificador de uso (IA)" section: how to train, where files go).

- [ ] **Step 1:** Check Doppler is configured for this repo: `doppler configure get project --plain` prints a project. If not, **stop and ask the user** to run `doppler setup` (never handle the secrets yourself).
- [ ] **Step 2:** `doppler run -- ./.venv/Scripts/python scripts/treinar_uso.py`. Expected: prints class counts close to the spec's table, the four models' macro-F1 for both CV schemes, and writes the three files.
- [ ] **Step 3:** Sanity-check the report: spatial-CV macro-F1 ≤ stratified; best model beats `mais_frequente` on macro-F1; rare classes listed even if F1 = 0. If the joblib is > 10 MB, add it to `.gitignore` and say so in the README section instead of committing it.
- [ ] **Step 4: Commit** `feat(ia): modelo treinado, métricas e relatório de avaliação`

---

### Task 4: API predictions in memory, tiles and detail

**Files:**
- Create: `backend/ia/modelo.py`
- Modify: `backend/main.py` (lifespan, `current_version`, `MAP_FORMAT = 3`, `_building_tile_bytes`, `_dot_properties`, `get_building`, `map_meta` warm-up)
- Test: `backend/tests/test_ia_api.py`

**Interfaces:**
- Consumes: Task 1 functions, `dados.de_linhas`, `db.fetch_buildings_for_model`, the joblib payload shape from Task 2.
- Produces (in `backend/ia/modelo.py`):
  - `carregar(caminho: Path = CAMINHO_PADRAO) -> dict | None` — returns the payload, or `None` (logs a warning) on missing file or any load error.
  - `@dataclass Previsao: tipo: str, confianca: float, probabilidades: dict[str, float], bairro_estimado: str | None, lng: float, lat: float, fonte: str`
  - `prever_base(payload: dict, base: Base) -> dict[int, Previsao]` — features with `conhecidos = mascara_treino`, predict only rows in `mascara_previsao`; `bairro_estimado` from `bairro_mais_proximo`.
  - `class Estado:` holds `payload` and a lock-protected memo; `versao_modelo() -> str` (`"sem-modelo"` when none); `previsoes(versao_dados: str) -> dict[int, Previsao]` (empty dict when no model; computed once per version under a lock).
  - module-level `estado = Estado()`; `main.lifespan` sets `estado.payload = carregar()`.
- `current_version()` becomes `f"{db.fetch_data_version()}-{MAP_FORMAT}-{ia_modelo.estado.versao_modelo()}"`.

- [ ] **Step 1: Write the failing tests** (fake payload: a tiny `DummyClassifier`-like object with `predict_proba`; monkeypatch `db.fetch_buildings_for_model`, `db.fetch_building_tile`, `db.fetch_building`, `db.fetch_data_version`; auth override as in `backend/tests/test_api.py:784`):

```python
def test_modelo_ausente_ou_corrompido_api_segue_sem_previsao(tmp_path):
    assert carregar(tmp_path / "nao_existe.joblib") is None
    ruim = tmp_path / "ruim.joblib"; ruim.write_bytes(b"lixo")
    assert carregar(ruim) is None

def test_previsoes_so_para_desconhecidos(estado_fake):
    p = estado_fake.previsoes("v1")
    assert set(p) == IDS_DESCONHECIDOS and all(0 <= x.confianca <= 1 for x in p.values())

def test_previsoes_por_versao_calcula_uma_vez(estado_fake, contador_fetch):
    estado_fake.previsoes("v1"); estado_fake.previsoes("v1")
    assert contador_fetch.n == 1
    estado_fake.previsoes("v2")
    assert contador_fetch.n == 2

def test_tile_so_desconhecidos_levam_previsao(client_ia):
    feats = decodificar(client_ia.get("/api/tiles/14/x/y.pbf"))  # pick z/x/y covering the fake buildings
    for f in feats:
        if f["properties"]["tipo"] == "desconhecido":
            assert "tipo_previsto" in f["properties"] and "confianca" in f["properties"]
        else:
            assert "tipo_previsto" not in f["properties"]

def test_detalhe_traz_previsao_e_probabilidades(client_ia):
    p = client_ia.get(f"/api/buildings/{ID_DESCONHECIDO}").json()["properties"]
    assert p["tipo"] == "desconhecido" and p["tipo_previsto"] in CLASSES
    assert sum(p["probabilidades"].values()) == pytest.approx(1)

def test_versao_do_mapa_inclui_modelo(client_ia):
    assert client_ia.get("/api/map/meta").json()["version"].endswith(estado.versao_modelo())
```

- [ ] **Step 2: Run** `JWT_SECRET=x ./.venv/Scripts/python -m pytest backend/tests/test_ia_api.py -q` — expected: FAIL.
- [ ] **Step 3: Implement.** In `_building_tile_bytes`, right after `db.fetch_building_tile`, add `tipo_previsto` and `confianca` (rounded to 2) to each row's `properties` only when the id has a prediction; `_dot_properties` copies those two keys when present. `get_building` adds `tipo_previsto`, `confianca`, `probabilidades` when present. `map_meta` warm-up also submits `estado.previsoes(version)` before the tile warm-up so tiles don't wait on a cold start.
- [ ] **Step 4: Run** the new tests and the whole backend suite — expected: pass, existing tests unchanged.
- [ ] **Step 5: Commit** `feat(ia): previsões em memória nos tiles e no detalhe do prédio`

---

### Task 5: Prediction routes and stats breakdown

**Files:**
- Modify: `backend/main.py`, `backend/schemas.py`, `backend/ia/modelo.py`
- Test: `backend/tests/test_ia_api.py`

**Interfaces:**
- Consumes: Task 4 `estado`, `Previsao`.
- Produces:
  - `schemas.PredictUsoIn(id: str = Field(pattern=r"^\d{1,12}$"))`; `schemas.PredictUsoOut(id: str, tipo_oficial: str, tipo_previsto: str, confianca: float, probabilidades: dict[str, float], em_treino: bool)`.
  - `modelo.contar_previstos(previsoes: dict[int, Previsao], bbox=None, bairros=None, tipo=None, fonte=None) -> dict[str, int]` — bairro matches `bairro_estimado` (with `"Não informado"` meaning `None`), tipo matches the predicted type (`tipo == "desconhecido"` → all), bbox matches `(lng, lat)`, fonte matches `fonte`.
  - `modelo.prever_um(estado, versao, id: int) -> PredictUsoOut-shaped dict | None` — for an official building computes its in-sample opinion and sets `em_treino=True`.
  - Routes: `POST /api/predict-uso` (200 / 404 / 422 / 503), `GET /api/predict-uso/modelo` (contents of `modelo_uso.json`, 503 without it), `GET /api/predict-uso/revisao?limit=50&offset=0` (`limit` 1–500; unknowns by **ascending** confidence: `[{id, tipo_previsto, confianca, lng, lat}]`).
  - `/api/stats` response gains `by_tipo_previsto: dict[str, int]` (empty dict without model); add the field to `StatsResponse` with default `{}`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("body", [{"id": "abc"}, {"id": ""}, {"id": "-1"}, {}])
def test_predict_uso_valida_entrada(client_ia, body):
    assert client_ia.post("/api/predict-uso", json=body).status_code == 422

def test_predict_uso_404_e_200(client_ia):
    assert client_ia.post("/api/predict-uso", json={"id": "999999"}).status_code == 404
    r = client_ia.post("/api/predict-uso", json={"id": str(ID_DESCONHECIDO)}).json()
    assert r["tipo_oficial"] == "desconhecido" and r["em_treino"] is False

def test_predict_uso_em_predio_oficial_marca_em_treino(client_ia):
    assert client_ia.post("/api/predict-uso", json={"id": str(ID_RESIDENCIAL)}).json()["em_treino"] is True

def test_rotas_503_sem_modelo(client_sem_modelo):
    assert client_sem_modelo.post("/api/predict-uso", json={"id": "1"}).status_code == 503
    assert client_sem_modelo.get("/api/predict-uso/modelo").status_code == 503

def test_revisao_ordena_menos_confiante_primeiro(client_ia):
    itens = client_ia.get("/api/predict-uso/revisao", params={"limit": 10}).json()
    assert [i["confianca"] for i in itens] == sorted(i["confianca"] for i in itens)

def test_contar_previstos_filtra_por_bairro_estimado_e_tipo():
    prev = {1: Previsao("comercial", .8, {}, "CENTRO", -38.22, -9.40, "OpenStreetMap"),
            2: Previsao("residencial", .6, {}, "BTN", -38.21, -9.40, "OpenStreetMap")}
    assert contar_previstos(prev, bairros=["CENTRO"]) == {"comercial": 1}
    assert contar_previstos(prev, tipo="residencial") == {"residencial": 1}
    assert contar_previstos(prev, tipo="desconhecido") == {"comercial": 1, "residencial": 1}

def test_stats_tem_by_tipo_previsto(client_ia):
    assert "by_tipo_previsto" in client_ia.get("/api/stats").json()
```

- [ ] **Step 2: Run** — expected: FAIL.
- [ ] **Step 3: Implement** the schemas, `contar_previstos`, `prever_um`, the three routes and the stats field. `/api/stats` keeps calling `db.fetch_stats` unchanged and adds `by_tipo_previsto` from `contar_previstos(estado.previsoes(current_version()), …same filters…)`.
- [ ] **Step 4: Run** the new tests + whole backend suite — expected: pass.
- [ ] **Step 5: Commit** `feat(ia): rota predict-uso, lista de revisão e contagem prevista nas estatísticas`

---

### Task 6: Map switch and effective type

**Files:**
- Modify: `frontend/src/mapLayers.ts`, `frontend/src/mapTiles.ts`, `frontend/src/App.tsx`, `frontend/src/components/LayersPanel.tsx`, `frontend/src/index.css` (only if a style is needed)
- Test: `frontend/tests/mapTiles.test.mjs`, new `frontend/tests/mapLayers.test.mjs` if `tipoEfetivo` is testable without a map

**Interfaces:**
- Produces:
  - `mapLayers.tipoEfetivo(mostrarPrevistos: boolean): ExpressionSpecification` — `['coalesce', ['get', 'tipo_previsto'], ['get', 'tipo']]` when true, `['get', 'tipo']` when false.
  - `DataFilters` gains `mostrarPrevistos: boolean`; `applyDataFilters` uses `tipoEfetivo` for the type filter clause and re-sets the type colour paint property of the dots, fill and extrusion layers (and the height default) with `colorByTipo(tipoEfetivo(...), theme)`. Predicted features get a visibly different fill (e.g. lower `fill-opacity` via `['has', 'tipo_previsto']`), readable in both themes.
  - `countFootprints(features, mostrarPrevistos = false)` counts by `tipo_previsto ?? tipo` when true.
  - `LayersPanel` props gain `mostrarPrevistos: boolean; onMostrarPrevistos: (v: boolean) => void`; switch labelled "Mostrar usos previstos por IA" with sub-text "Estimativa do modelo para prédios sem uso oficial", inside an `option-card` like "Mostrar endereços". `App` state defaults to `true`.
  - The far-zoom overview grid is unchanged (official only).

- [ ] **Step 1: Write the failing test**

```js
test('with predictions on, unknown buildings count under their predicted use', () => {
  const f = [
    { properties: { id: '1', tipo: 'desconhecido', tipo_previsto: 'comercial' } },
    { properties: { id: '2', tipo: 'residencial' } },
  ];
  assert.deepEqual(countFootprints(f, true).counts, { comercial: 1, residencial: 1 });
  assert.deepEqual(countFootprints(f, false).counts, { desconhecido: 1, residencial: 1 });
});
```

- [ ] **Step 2: Run** `npm test` in `frontend/` — expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** `npm test` and `npm run build` — expected: pass, no type errors.
- [ ] **Step 5: Verify in the browser** (dev server + backend with a model): switch on → unknown buildings take predicted colours and the panel counts move; switch off → grey "Não identificado" again; both themes.
- [ ] **Step 6: Commit** `feat(ia): camada de usos previstos no mapa com chave no painel`

---

### Task 7: Building card, report block and "Sobre o modelo"

**Files:**
- Create: `frontend/src/components/SobreModelo.tsx`
- Modify: `frontend/src/components/BuildingCard.tsx`, `frontend/src/Report.tsx`, `frontend/src/App.tsx`, `frontend/src/components/LayersPanel.tsx`, `frontend/src/index.css`

**Interfaces:**
- Consumes: `GET /api/buildings/{id}` fields `tipo_previsto`, `confianca`; `/api/stats` `by_tipo_previsto`; `GET /api/predict-uso/modelo`.
- Produces:
  - `BuildingProps` gains `tipo_previsto?: string | null; confianca?: number | null`.
  - `BuildingCard` prop `onSobreModelo: () => void`. When `tipo === 'desconhecido'` and `tipo_previsto` exists: pill uses the predicted type's colour and label, followed by a badge "Previsto por IA · 72 %" (confidence rounded to whole %), and a muted line "Uso oficial: não identificado" plus a link-button "Sobre o modelo".
  - `SobreModelo({ apiUrl, onClose })`: fetches the model JSON with `authHeaders()`; renders data used (counts per class), per-class precision/recall/F1 of the chosen model (rare classes included), spatial-CV macro-F1 next to the baseline's, and the fixed Portuguese text on limits: estimate not official data; labelled buildings are the ones with an address (selection bias); rare classes rarely predicted; proper use = prioritise field checks and read trends, not decide about a single building. Shows a clear message on 503 ("Modelo indisponível no servidor").
  - `Report`: below the official breakdown, a "Previsto por IA" block built from `by_tipo_previsto` with the same chip colours, plus the caveat "Estimativas do modelo para prédios sem uso oficial; o bairro desses prédios também é estimado (vizinho mais próximo)." Hidden when `by_tipo_previsto` is empty.
  - "Sobre o modelo" also reachable from `LayersPanel` (link under the new switch).

- [ ] **Step 1:** Implement the card change; `npm run build` — expected: no type errors.
- [ ] **Step 2:** Implement `SobreModelo` and its two entry points; `npm run build`.
- [ ] **Step 3:** Implement the report block; `npm run build` and `npm test`.
- [ ] **Step 4: Verify in the browser:** click an unknown building → badge with % and official status; click an official one → no badge; open "Sobre o modelo" from card and panel; report filtered by a bairro shows a non-empty "Previsto por IA" block; stop the backend's model (rename the file, restart) → card shows no badge, "Sobre o modelo" shows the unavailable message, report hides the block. Both themes, phone width.
- [ ] **Step 5: Commit** `feat(ia): cartão, relatório e seção Sobre o modelo`

---

### Task 8: Final check

- [ ] `JWT_SECRET=x ./.venv/Scripts/python -m pytest backend/tests importacao -q` — all pass (DB tests may skip).
- [ ] `npm test` and `npm run build` in `frontend/` — pass.
- [ ] Re-read the spec's Goals and Anti-goals against the running app; list anything not met.
