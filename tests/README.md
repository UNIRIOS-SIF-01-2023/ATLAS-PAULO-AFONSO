# Testes e evidências

## Automatizados

| Pasta | O que cobre | Como rodar (na raiz) |
| --- | --- | --- |
| `tests/backend/` | API: filtros, tiles, busca, login/JWT, estatísticas e as rotas de IA (`test_ia_api.py`, com modelo falso: 200, 401, 404, 422, 503 e cache por versão); features e treino do classificador (`test_ia_features.py`, `test_ia_treino.py`: sem vazamento de rótulo, baselines, reprodutibilidade). Os testes de integração rodam contra o MariaDB local e são pulados se ele estiver fora do ar | `python -m pytest tests/backend -q` |
| `tests/importacao/` | Carga (ETL): classificação, junção espacial, leitura e recorte, registros, banco de rascunho e promoção, com banco simulado | `python -m pytest tests/importacao -q` |
| `frontend/tests/` | Lógica do frontend sem navegador: níveis de detalhe e contagens do mapa, altura dos prédios, formatação, revalidação da sessão (`node:test`) | `cd frontend && npm test` |

Tudo de uma vez: `python -m pytest tests -q` e `cd frontend && npm test`.

Última execução (27/11/2026, MariaDB local no ar com a carga completa): **133 testes Python passando** (90 do backend e 43 da carga) e **16 do frontend**; `npm run build` sem erros.

## Testes manuais do recurso de IA (27/11/2026)

Feitos com a API e o frontend locais, o modelo `uso-20261122-a82af781` e a carga completa (55.255 prédios).

| Caso | Entrada | Resultado esperado | Resultado obtido |
| --- | --- | --- | --- |
| Prédio sem uso oficial | `POST /api/predict-uso {"id": "1"}` | 200 com classe, confiança e probabilidades | 200, `residencial`, confiança 0,95, probabilidades somam 1 |
| ID inválido | `{"id": "abc"}` | 422 | 422, padrão `^\d{1,12}$` |
| Prédio inexistente | `{"id": "99999999"}` | 404 | 404, "Prédio não encontrado." |
| Sem login | sem `Authorization` | 401 | 401 |
| Métricas do modelo | `GET /api/predict-uso/modelo` | 200 com o `modelo_uso.json` | 200 |
| Tempo de resposta | previsão já calculada ao subir a API | resposta imediata | cerca de 30 ms |
| Cartão no mapa | prédio 172, sem endereço, 1.130 m² | bloco "Uso estimado por IA" | "Comercial · Previsto por IA · 63%" ([captura](../docs/imagens/ia-cartao-previsao.jpg)) |
| Prédio com uso oficial | prédio 171 (comercial do CNEFE) | sem bloco de IA | sem bloco de IA |
| "Sobre o modelo" | link no cartão e no painel lateral | janela com dados, métricas e limites | ok, fecha com Esc ([captura](../docs/imagens/ia-sobre-o-modelo.jpg)) |
| Erro tratado: sem modelo | API iniciada sem `ai-model/models/modelo_uso.joblib` | mensagem clara, resto do sistema funcionando | "Modelo de uso indisponível no servidor" no cartão, mapa normal ([captura](../docs/imagens/ia-erro-sem-modelo.jpg)) |
| Recarregar a página logado | F5 com sessão salva | continua logado | continua logado |
| Temas | claro e escuro | legível nos dois | ok |
