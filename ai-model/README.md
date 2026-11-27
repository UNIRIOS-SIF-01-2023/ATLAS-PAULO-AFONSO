# ai-model: classificador de uso das edificações

Treino, avaliação e modelo salvo do recurso de IA (eixo Machine Learning). A visão geral está na seção [Classificador de uso (IA)](../README.md#classificador-de-uso-ia) do README principal.

```
ai-model/
  notebooks/eda_e_avaliacao.ipynb   EDA da base (classes, área, fonte) e leitura das métricas, já executado
  scripts/treinar_uso.py            treino reprodutível (semente 42): compara modelos, ajusta, calibra e grava o modelo
  models/modelo_uso.joblib          modelo treinado (Random Forest calibrado, scikit-learn 1.9.1, 30 MB)
  models/modelo_uso.json            versão, parâmetros, contagens e todas as métricas da avaliação
```

O código Python do pipeline fica em [`backend/ia/`](../backend/ia), porque a API usa as mesmas funções na inferência. Assim as features do treino e as da previsão são sempre iguais:

| Arquivo | Etapa |
| --- | --- |
| `backend/ia/dados.py` | linhas do banco → arrays (coleta) |
| `backend/ia/features.py` | features de forma, fonte e vizinhança (transformação) |
| `backend/ia/treino.py` | baselines, Random Forest e Gradient Boosting, CV estratificada e espacial, ajuste de hiperparâmetros, calibração, relatório |
| `backend/ia/modelo.py` | carrega `models/modelo_uso.joblib` na API e prevê os prédios desconhecidos (inferência) |

## Treinar de novo

Na raiz do repositório, com o MariaDB no ar e os dados importados (ver README principal). Leva cerca de 3 minutos:

```bash
./.venv/Scripts/python ai-model/scripts/treinar_uso.py   # Linux/macOS: ./.venv/bin/python
```

O script grava `models/modelo_uso.joblib`, `models/modelo_uso.json` e o relatório [`docs/ia/avaliacao_modelo.md`](../docs/ia/avaliacao_modelo.md). Reinicie a API para carregar o modelo novo.

## Notebook

O notebook lê a mesma base do banco e o `modelo_uso.json`; ele não treina. Para reabrir e rodar de novo: `pip install notebook` e `jupyter notebook ai-model/notebooks/eda_e_avaliacao.ipynb`, a partir da raiz do repositório.
