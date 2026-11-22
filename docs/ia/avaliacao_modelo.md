# Avaliação do classificador de uso das edificações

Gerado por `scripts/treinar_uso.py` em 2026-11-22T20:46:01+00:00. Modelo `uso-20261122-a82af781`, semente 42.

## Dados

- Prédios: 55255; rotulados pelo CNEFE (treino): 23115; sem uso conhecido (recebem previsão): 32071.

| Classe | Prédios rotulados |
|---|---|
| residencial | 21368 |
| comercial | 1522 |
| servico | 37 |
| religioso | 79 |
| institucional | 53 |
| hospedagem | 15 |
| agropecuario | 41 |

## Método

Features só do que todo prédio tem: forma do polígono (área, perímetro, compacidade, retângulo mínimo, vértices), fonte do footprint e vizinhança (densidade, área dos vizinhos, uso dos vizinhos rotulados num raio de 150 m, distância ao comercial mais próximo). Bairro, endereço e pavimentos ficam de fora (só os rotulados têm).

Validação cruzada em 5 folds, de dois jeitos: estratificada (sorteio de prédios) e espacial (células de 500 m inteiras no teste). Em cada fold os rótulos do teste ficam escondidos das features de vizinhança. A espacial é a mais honesta para prédios sem endereço.

## Comparação de modelos (F1 macro)

| Modelo | Estratificada | Espacial | Acurácia (espacial) |
|---|---|---|---|
| Classe mais frequente (baseline) | 0.137 | 0.137 | 0.924 |
| Regressão logística (baseline) | 0.139 | 0.121 | 0.403 |
| Random Forest | 0.167 | 0.143 | 0.925 |
| Gradient Boosting | 0.194 | 0.135 | 0.541 |

## Ajuste de hiperparâmetros (Random Forest, CV espacial)

| Parâmetros | F1 macro |
|---|---|
| n_estimators=200, min_samples_leaf=1 | 0.143 |
| n_estimators=200, min_samples_leaf=3 | 0.167 |
| n_estimators=400, min_samples_leaf=1 | 0.143 |
| n_estimators=400, min_samples_leaf=3 | 0.166 |

Escolhido: {'modelo': 'random_forest', 'n_estimators': 200, 'min_samples_leaf': 3}.

## Modelo final (calibrado, CV espacial)

F1 macro 0.155, acurácia 0.926, confiança média 0.92.

| Classe | Precisão | Recall | F1 | Suporte | Brier |
|---|---|---|---|---|---|
| residencial | 0.93 | 1.00 | 0.96 | 21368 | 0.063 |
| comercial | 0.62 | 0.07 | 0.13 | 1522 | 0.056 |
| servico | 0.00 | 0.00 | 0.00 | 37 | 0.002 |
| religioso | 0.00 | 0.00 | 0.00 | 79 | 0.003 |
| institucional | 0.00 | 0.00 | 0.00 | 53 | 0.002 |
| hospedagem | 0.00 | 0.00 | 0.00 | 15 | 0.001 |
| agropecuario | 0.00 | 0.00 | 0.00 | 41 | 0.002 |

### Matriz de confusão (linhas = oficial, colunas = previsto)

| | residencial | comercial | servico | religioso | institucional | hospedagem | agropecuario |
|---|---|---|---|---|---|---|---|
| residencial | 21308 | 59 | 0 | 0 | 0 | 0 | 1 |
| comercial | 1416 | 106 | 0 | 0 | 0 | 0 | 0 |
| servico | 35 | 2 | 0 | 0 | 0 | 0 | 0 |
| religioso | 79 | 0 | 0 | 0 | 0 | 0 | 0 |
| institucional | 49 | 4 | 0 | 0 | 0 | 0 | 0 |
| hospedagem | 14 | 1 | 0 | 0 | 0 | 0 | 0 |
| agropecuario | 41 | 0 | 0 | 0 | 0 | 0 | 0 |

### Erros mais confiantes

| Prédio | Oficial | Previsto | Confiança |
|---|---|---|---|
| 26223 | comercial | residencial | 0.97 |
| 20733 | comercial | residencial | 0.97 |
| 25646 | comercial | residencial | 0.97 |
| 21444 | comercial | residencial | 0.97 |
| 24425 | comercial | residencial | 0.97 |
| 40714 | comercial | residencial | 0.97 |
| 14184 | comercial | residencial | 0.97 |
| 21568 | servico | residencial | 0.97 |
| 43804 | comercial | residencial | 0.97 |
| 28684 | religioso | residencial | 0.97 |
| 41775 | comercial | residencial | 0.97 |
| 44115 | comercial | residencial | 0.97 |
| 26217 | comercial | residencial | 0.97 |
| 25424 | comercial | residencial | 0.97 |
| 14174 | comercial | residencial | 0.97 |
| 39778 | comercial | residencial | 0.97 |
| 19293 | comercial | residencial | 0.97 |
| 23088 | comercial | residencial | 0.97 |
| 44264 | comercial | residencial | 0.97 |
| 10583 | agropecuario | residencial | 0.97 |

## Interpretação

- A base é desbalanceada: residencial é 92,4% dos prédios rotulados. Por isso a acurácia engana: um modelo que sempre responde residencial já acerta 92,4%, e o modelo final acerta 92,6%. O F1 macro, que pesa todas as classes igualmente, é a métrica principal.
- F1 macro: 0,155 contra 0,137 do baseline (+0,018). O modelo aprende algo além da classe majoritária, mas o ganho é pequeno.
- Classes reconhecidas: residencial (F1 0,962). Classes que o modelo quase não reconhece (F1 < 0,2): comercial, servico, religioso, institucional, hospedagem, agropecuario; na matriz de confusão, a maior parte delas vira residencial.
- Para o uso proposto (estimar o uso dos prédios sem endereço e priorizar conferência): o resultado ainda não basta para apontar comercial, servico, religioso, institucional, hospedagem, agropecuario com segurança; uma previsão diferente de residencial é um sinal útil, mas a ausência dela não diz muito, e a confiança exibida deve ser lida junto com isso.

## Limites

- Viés de seleção: os prédios rotulados são justamente os que têm endereço no CNEFE; os sem endereço podem ter outro perfil (anexos, galpões, área rural). A CV espacial mede isso só em parte.
- Classes raras (poucas dezenas de exemplos) têm F1 baixo e quase nunca são previstas.
- A previsão é uma estimativa, nunca dado oficial. Serve para priorizar conferência de campo e ler tendências, não para decidir sobre um prédio específico.
