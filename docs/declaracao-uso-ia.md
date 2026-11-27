# Declaração de uso de IA generativa

Atende à seção 11 do enunciado ("Integridade acadêmica e uso de IA generativa") e entra no relatório técnico. A equipe usou IA generativa como apoio no código, na análise e na documentação. A responsabilidade pelo que foi entregue é da equipe, que revisou, testou e deve saber explicar cada trecho.

## Ferramentas

| Ferramenta | Modelos | Como foi usada |
| --- | --- | --- |
| Claude Code (Anthropic), assistente de programação no terminal e no app desktop | Claude Opus 5.5, Claude Sonnet 5.5, Claude Sonnet 5, Claude Haiku 5.5 | Gera e edita código e texto no repositório a partir de pedidos da equipe. Os commits feitos com ele trazem a linha `Co-Authored-By: Claude ...` no histórico do Git |

> **[A equipe completa]** Se algum integrante usou outra ferramenta (ChatGPT, Gemini, GitHub Copilot etc.), acrescente uma linha aqui e nas tabelas abaixo.

## O que foi feito com IA e como foi validado

### Antes da etapa de IA (sistema do PI)

Commits com `Co-Authored-By` de um modelo Claude:

| Data | Integrante | Commit | Para que foi usada | Como o resultado foi validado |
| --- | --- | --- | --- | --- |
| 15/10 | João Vítor Almeida | `b16949c`, `82d105a`, `0a2c677` | Reconstrução da renderização do mapa e da interface; texto do README | **[A equipe completa]** |
| 25/10 | Luís Gustavo | `d46626f` | Vetorizar a junção espacial da carga (`match_points`) | **[A equipe completa]** |
| 25/10 | Luís Gustavo | `080bfc6`, `90bfd9d` | Novas tentativas em 502/503/504 no `fetchJson`; empacotar o worker do MapLibre | **[A equipe completa]** |
| 02/11 | Caio Lúcio | `3569e05` | Redigir o diário de bordo a partir dos commits | **[A equipe completa]** |
| 21/11 | Luís Gustavo | `05979f8`, `5400946` | Otimizar consultas de estatísticas e do mapa | **[A equipe completa]** |

### Checkpoint 2: classificador de uso (22/11)

> **[A equipe completa]** Os commits de 22/11 (features, script de treino, modelo treinado e `docs/superpowers/`) não trazem a linha `Co-Authored-By`. Se houve uso de IA no design, no plano ou no código do classificador, declare aqui o que foi feito e como foi conferido.

### Integração e entrega (27/11), com Claude Code (Claude Opus 5.5)

| Para que foi usada | Arquivos / commits | Como o resultado foi validado |
| --- | --- | --- |
| Comparar o repositório com o enunciado e listar o que faltava para os requisitos mínimos e a rubrica | sem arquivo (análise) | Cada lacuna foi conferida no código e no histórico do Git |
| Carregar o modelo na API e prever os prédios sem uso oficial; rotas `POST /api/predict-uso` e `GET /api/predict-uso/modelo`; contratos Pydantic | `backend/ia/modelo.py`, `backend/main.py`, `backend/schemas.py` (`cc887e1`) | 13 testes automatizados com modelo falso (`tests/backend/test_ia_api.py`: 200, 401, 404, 422, 503, cálculo uma vez por versão dos dados) e testes manuais contra o modelo real (ver `tests/README.md`) |
| Bloco "Uso estimado por IA" no cartão do prédio, janela "Sobre o modelo" e estilos | `frontend/src/components/BuildingCard.tsx`, `SobreModelo.tsx`, `LayersPanel.tsx`, `App.tsx`, `index.css` (`166c76a`) | `npm run build` sem erros; conferido no navegador: previsão, prédio oficial sem bloco, janela pelo cartão e pelo painel, erro 503 com o modelo ausente, temas claro e escuro. Capturas em `docs/imagens/` |
| Correção da sessão apagada ao recarregar a página | `e636e8e` | O bug foi reproduzido no navegador. No merge, ficou a correção que já existia na branch `chore/mariadb-3320` (`revalidarSessao`, com teste) |
| Merge da branch `chore/mariadb-3320` e resolução do conflito | `4d154e5` | Testes do backend e do frontend depois do merge; login mantido ao recarregar |
| Reorganizar o repositório na estrutura da seção 9 (`ai-model/`, `tests/`), `requirements.txt`, `LICENSE` e `.gitignore` | `6165b28`, `c57ebe7` | 133 testes Python e 16 do frontend passando; API carregando o modelo do novo caminho |
| Notebook de EDA e avaliação: código e texto de interpretação | `ai-model/notebooks/eda_e_avaliacao.ipynb` | Executado contra o banco local; os números foram conferidos com `modelo_uso.json` e `docs/ia/avaliacao_modelo.md`. **A equipe revisa a interpretação antes da apresentação** |
| Documentação: seção de IA do README, `ai-model/README.md`, `tests/README.md`, `datasets/README.md` e esta declaração | `b60cc09`, `c013a9d`, `e2e229b` e commits seguintes | Caminhos, comandos e números conferidos no repositório e na execução local |

**Conferência humana:** **[A equipe completa]** quem revisou cada parte e quando. O enunciado pede que todo trecho gerado por IA seja revisado e testado pela equipe (seção 11, regra 3).

## O que a equipe precisa saber explicar

Pontos do código gerado em 27/11 que o professor pode perguntar:

- **Por que a API prevê a base inteira e não um prédio por pedido.** As features usam a vizinhança (vizinhos num raio de 150 m, uso dos vizinhos rotulados, distância ao comercial mais próximo). Calcular um prédio sozinho daria "zero vizinhos" e uma previsão errada sem nenhum erro aparente. Por isso a API monta as features como no treino, uma vez por versão dos dados, e guarda o resultado (`backend/ia/modelo.py`).
- **Por que a API recusa um modelo com outras features.** Se a lista de features do modelo salvo não for igual à do código, a previsão sairia errada sem aviso. A API não carrega esse modelo, e as rotas de IA respondem 503.
- **Por que o prédio com uso oficial não recebe previsão.** O modelo só estima os prédios desconhecidos. Nos rotulados, a opinião do modelo seria sobre um exemplo que ele viu no treino.
- **Por que F1 macro e validação espacial.** Residencial é 92,4% da base, então a acurácia engana. A validação espacial deixa áreas inteiras de 500 m fora do treino, que é o caso real dos prédios sem endereço.
- **Códigos de erro:** 422 (ID fora do padrão), 404 (prédio inexistente), 401 (sem login), 503 (modelo ausente).
