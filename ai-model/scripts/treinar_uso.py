"""Treina o classificador de uso com os dados do banco e grava modelo, métricas e relatório.

Uso (na raiz do repositório):
    ./.venv/Scripts/python ai-model/scripts/treinar_uso.py
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from backend import db  # noqa: E402
from backend.ia.dados import de_linhas  # noqa: E402
from backend.ia.treino import avaliar, escrever_saidas, treinar_final  # noqa: E402


def main():
    print("Lendo prédios do banco...")
    base = de_linhas(db.fetch_buildings_for_model())
    print(f"  {len(base.tipos)} prédios")
    print("Avaliando modelos (CV estratificada e espacial)...")
    metricas = avaliar(base)
    for nome, r in metricas["modelos"].items():
        print(f"  {nome:20s} F1 macro: estratificada {r['estratificada']['f1_macro']:.3f}, "
              f"espacial {r['espacial']['f1_macro']:.3f}")
    print(f"  escolhido: {metricas['melhor']['params']}")
    print("Treinando o modelo final (calibrado)...")
    payload = treinar_final(base, metricas["melhor"]["params"])
    escrever_saidas(payload, metricas, RAIZ / "ai-model" / "models", RAIZ / "docs" / "ia" / "avaliacao_modelo.md")
    print(f"Pronto: modelo {payload['versao']}.")


if __name__ == "__main__":
    main()
