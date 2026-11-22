# Building-use classifier (AI checkpoint) — design

Date: 2026-11-22 · Source: "Checkpoint 1: Proposta do Recurso de IA" (team PDF), adjusted to what the code and data actually show.

## Intent

About 31 k of the 55 k buildings are `TIPO_USO = 'desconhecido'`: they have a footprint (Overture/Google/Microsoft/OSM) but no CNEFE address point inside, so the importer had nothing to classify them with (`importacao/registros.py:montar_edificacoes`). This undercounts every use on the map, the legend and the report, and gives the maintainers no way to prioritise field checks.

We train a supervised classifier on the buildings that *do* have a CNEFE label and use it to predict a use **plus a confidence** for **every** unknown building. Predictions are always shown as estimates ("previsto por IA"), never merged into the official `tipo`.

Users: the client reading the map and report; the maintainers, who get a list ordered by confidence.

This is also a graded course checkpoint: the evaluation (baselines, stratified CV, per-class F1, confusion matrix, error analysis, an improvement experiment, fixed seed) is a deliverable in its own right.

## What the data actually looks like (measured from `data/raw`, 2026-11-22)

| Origin of label | Count |
|---|---|
| `desconhecido` / `sem_dado` | 31 323 |
| `desconhecido` / `cnefe` (COD_ESPECIE 7, under construction) | 743 |
| `residencial` / cnefe | 21 368 |
| `comercial` / cnefe | 1 522 |
| `religioso` / cnefe | 79 |
| `institucional` / cnefe | 53 |
| `agropecuario` / cnefe | 41 |
| `servico` / cnefe | 37 |
| `hospedagem` / cnefe | 15 |
| any / `overture_subtype` | 69 |

Consequences:

- ~92 % of labels are `residencial`. Five classes have < 80 examples. The model will in practice be residential-vs-commercial with weak rare classes; that is reported honestly, not hidden.
- `BAIRRO_ID` is set only from CNEFE addresses, so every unknown building has no bairro. **Bairro cannot be a feature** (it would leak "has an address"). It is replaced by spatial-neighbourhood features.
- `NUM_PAVIMENTOS` is filled for 143 buildings. **Dropped** as a feature.
- The DB schema is not in the repo (it lives only on the remote MySQL); the local counts above are from the raw files and may differ slightly from the DB (the DB import may clip to the municipal boundary).

## Goals

- A reproducible training script that reads the system's own data, evaluates, and saves a model file.
- The API loads the model at startup, predicts every unknown building, and exposes predictions in tiles, building detail, stats and a dedicated endpoint.
- Frontend: predicted use + confidence on the building card with a "previsto por IA" badge; a panel switch to show/hide predicted uses on the map; predicted counts shown separately in the report; a "Sobre o modelo" section (data, metrics, limits, proper use).

## Non-goals

- No change to the database schema or to the importer.
- No retraining from the UI, no online learning, no field-check workflow (only the ordered list).
- No new map style system; reuse the existing type colours.
- No LightGBM (sklearn's `HistGradientBoostingClassifier` covers gradient boosting without a new native dependency).

## Anti-goals (would be a failure even if it works)

- A predicted use shown or counted as if it were official CNEFE data.
- Using any feature that only labelled buildings have (bairro, address count, origin) — inflated metrics that collapse on the unknowns.
- Reporting only accuracy (92 % is reachable by always saying "residencial").
- The frontend loading or calling the model directly.

## Approaches considered

- **A. API predicts at startup, in memory (chosen by João).** Model file loaded in the FastAPI lifespan; predictions for all unknowns computed once per data version and kept in memory; tiles/detail/stats read from that. No DB change; swapping a model is replacing a file. Cost: a few seconds and a few MB per data version.
- **B. New columns in `EDIFICACOES` filled at import.** DB does filtering/counting natively, but needs a manual migration on a shared remote DB that isn't versioned in the repo, and creates a train-on-imported-data / predict-during-import cycle.
- *(Dropped)* Separate predictions table: building IDs are `position + 1` and are renumbered on every re-import, so stored predictions would silently point at the wrong buildings.

## Design

### 1. Features — `backend/ia/features.py` (shared by training and API)

One pure function: input = arrays of geometries (shapely, lon/lat), official `tipo`, `origem_tipo`, `fonte` for **all** buildings; output = feature matrix (one row per building) + the label vector. Same code path at train and predict time.

Per building:
- Shape: area m², perimeter m, compactness `4πA/P²`, minimum rotated rectangle length/width/elongation, rectangle fill ratio (`A / rect area`), vertex count. Metres via local equirectangular scaling with `cos(lat)` (same spirit as `importacao/config.METROS_POR_GRAU`; no pyproj).
- Source: one-hot of `fonte` (Google Open Buildings / Microsoft / OpenStreetMap / other).
- Neighbourhood (STRtree on representative points): number of buildings within 50 m and 150 m; mean/median neighbour area within 150 m; **share of each labelled class among labelled neighbours within 150 m, excluding the building itself**; distance to nearest labelled `comercial` building; share of neighbours that are labelled at all.

Training rows: `ORIGEM_TIPO = 'cnefe'` and `TIPO_USO != 'desconhecido'`. Prediction rows: every `TIPO_USO = 'desconhecido'` (including the 743 "under construction"). The 69 Overture-subtype rows keep their official type and are neither trained on nor predicted.

### 2. Training — `scripts/treinar_uso.py`

- Reads the data through a new `backend/db.py` function (e.g. `fetch_buildings_for_model()`: id, WKB geometry, tipo, origem, fonte) so it sees exactly what the API sees. Runs under `doppler run --` like the rest.
- Models: baselines `DummyClassifier(most_frequent)` and `LogisticRegression` (scaled); `RandomForestClassifier` and `HistGradientBoostingClassifier`, both with balanced class weights. Fixed seed `42`.
- Evaluation: stratified 5-fold CV **and** spatially grouped CV (group = ~500 m grid cell) to show how much spatial autocorrelation inflates the stratified numbers. Per-class precision/recall/F1, macro-F1, confusion matrix. *(Builder's call, 2026-11-22.)* In every fold, the labels of held-out buildings are **hidden** when computing neighbour-label features (`features.py` takes a "known labels" mask and is recomputed per fold), so a held-out cell looks like a real unknown area.
- Confidence *(builder's call, 2026-11-22)*: the deployed model is wrapped in `CalibratedClassifierCV` (grouped folds), so "72 %" means roughly "right 72 % of the time" and the predicted counts aren't inflated by the balanced class weights. The metrics file reports calibration (Brier score per class / reliability numbers) next to F1.
- Improvement experiment: compare the four models, then a small hyperparameter search on the best one (grouped CV, macro-F1).
- Error analysis: confusion matrix discussion + a sample of the most confident wrong predictions.
- Output: `backend/ia/modelo_uso.joblib` (pipeline + class list + feature names + model version string) and `backend/ia/modelo_uso.json` (metrics, class counts, training date, seed, data version). Both committed if the joblib is ≤ ~10 MB; otherwise gitignored and the README explains how to train.
- New dependency: `scikit-learn` in `backend/requirements.txt` (brings `joblib`).

### 3. API — `backend/ia/modelo.py` + routes in `backend/main.py`

- Lifespan loads the joblib once. Missing or unreadable model → API still runs; prediction fields are `null`, prediction routes answer `503`.
- Predictions are computed per data version, memoised like `_overview_for` (and kicked off by the same warm-up in `/api/map/meta`): `{building_id: (tipo_previsto, confianca)}` where confianca = max class probability.
- `MAP_FORMAT` is bumped and the model version string joins the map version, so cached tiles are invalidated when either data or model changes.
- **Tiles** (`fetch_building_tile` / `_dot_properties`): unknown buildings carry `tipo_previsto` and `confianca` (2 decimals). Official buildings carry neither.
- **Building detail** (`GET /api/buildings/{id}`): adds `tipo_previsto`, `confianca`, and the full per-class probabilities for unknown buildings.
- **Estimated bairro (decided by João, 2026-11-22):** per data version, each unknown building gets `bairro_estimado` = the bairro of its nearest building that has a bairro (STRtree on representative points). It is used **only for report/stats filtering**, never shown as the building's bairro on the card.
- **Stats** (`GET /api/stats`): `by_tipo` and `by_bairro` stay official. New `by_tipo_previsto` breaks down the unknowns matching the current filter by predicted class, where the bairro filter matches on `bairro_estimado` and the `tipo` filter matches on the predicted type (so `tipo=comercial` also counts unknowns predicted comercial, like the map's effective type). This is computed in memory from the per-version prediction table (id → predicted type, confidence, estimated bairro, representative point), so it also works for the unfiltered path that reads `AGG_BAIRRO_TIPO`; a bbox filter matches on the representative point.
- **`POST /api/predict-uso`**: body `{"id": "<building id>"}`, validated by Pydantic (digits only). 200 with `{id, tipo_oficial, tipo_previsto, confianca, probabilidades}`; 404 unknown id; 503 no model; auth like every other route. For an officially labelled building it still returns the model's opinion with `tipo_oficial` set and `em_treino: true`. That opinion is in-sample, since the building was in training, so it is not shown as "agreement" anywhere *(builder's call, 2026-11-22: out-of-fold opinions can't be stored because IDs are renumbered on re-import, and refitting the folds at startup isn't worth it)*.
- **`GET /api/predict-uso/modelo`**: returns `modelo_uso.json` for the "Sobre o modelo" section.
- **Ordered list for field checks**: `GET /api/predict-uso/revisao?limit=&offset=` — unknown buildings ordered by ascending confidence (least sure first), with id, predicted type, confidence, a point to centre on. Plain JSON; a frontend screen for it is out of scope.

### 4. Frontend

- `BuildingCard`: when `tipo` is `desconhecido` and a prediction exists, the pill shows the predicted type colour with a "Previsto por IA · 72 %" badge and the official status stays visible ("Uso oficial: desconhecido"). A "Sobre o modelo" link.
- `LayersPanel`: switch "Mostrar usos previstos por IA" (default **on**). Map layers use an effective type expression: switch on → `coalesce(tipo_previsto, tipo)`; off → `tipo`. Colour, height defaults, type filter and the on-screen counts all use the effective type. Predicted buildings are visually distinct (lower fill opacity or dashed outline — builder's choice, must work in both themes).
- `Report`: official counts unchanged; a separate "Previsto por IA" block with the predicted breakdown of the unknowns (respecting the bairro and tipo filters as above) and a one-line caveat that the bairro of these buildings is itself estimated.
- "Sobre o modelo": a section/modal reachable from the panel and the card, rendering `GET /api/predict-uso/modelo`: data used, metrics per class (including the weak rare classes), the selection-bias caveat, what the prediction should and should not be used for.
- Far-zoom overview grid stays official-only (it is built server-side per data version; following the switch would double that work for little value).

### 5. Tests

- `features.py`: pure unit tests on tiny synthetic geometries (compactness of a square, self excluded from neighbour shares, no bairro/origin leakage in the feature names).
- API: fake model monkeypatched in, like the existing `test_api.py` fakes — tiles/detail carry the fields only for unknowns, 404/503/422 cases of `POST /api/predict-uso`, stats `by_tipo_previsto`.
- Training script: smoke test on a small synthetic dataset (runs end to end, writes both files, seed gives identical metrics twice).

## Known risks / limits (go into "Sobre o modelo")

- Selection bias: labelled buildings are exactly those with an address; unknowns (often outbuildings, rural sheds, buildings outside the CNEFE coverage) may look different. Grouped CV only partly measures this.
- Rare classes (< 80 examples) will have low F1; the model will rarely predict them.
- Most predictions will be `residencial`, many with modest confidence; the confidence is shown everywhere a prediction is.

## Build order and evaluation output *(builder's call, 2026-11-22: the next checkpoint's rubric and deadline aren't known yet)*

Training and evaluation first (features → training script → evaluation report), then the API, then the frontend. The training script writes `modelo_uso.json` plus a Portuguese markdown evaluation report (`docs/ia/avaliacao_modelo.md`) with tables for per-class metrics and the confusion matrix. No plotted figures, since that would mean adding matplotlib; add them if the rubric asks for images.

## Left to the builder

- Exact neighbour radii and grid size (start from the values above, keep if CV doesn't argue otherwise).
- Hyperparameter grid.
- Visual treatment of predicted buildings and the exact place of "Sobre o modelo", within the existing Liquid Glass styles.
