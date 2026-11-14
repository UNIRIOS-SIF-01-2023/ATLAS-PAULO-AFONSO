"""Ponto de entrada do algoritmo rooftop ativo (raycast por faces do CNEFE).

O motor vive em `algoritmo_raycast.py`; o clássico (versão original,
pré-direção-IBGE/evidências) fica preservado em `cnefe_rooftop_classico.py`
para comparação. Use `scripts/comparar_algoritmos.py` para rodar os dois
sobre a mesma entrada e divergir os resultados.
"""

from algoritmo_raycast import *  # noqa: F401,F403
import algoritmo_raycast as _implementation


if __name__ == "__main__":
    _implementation._cli()
