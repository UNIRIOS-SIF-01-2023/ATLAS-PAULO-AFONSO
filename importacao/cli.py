"""Importa edificações (Overture/OSM) e endereços (QG/CNEFE) para o banco
de rascunho (DB_NAME_STAGE). Com --promover, copia o resultado para o banco
de produção (DB_NAME) numa única transação.

Rode com: python importacao/import_data.py [--promover] (lê o .env da raiz)
"""

import argparse

from . import config
from .predios import montar_predios
from .producao import promover
from .stage import gravar_stage


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--promover", action="store_true",
                    help="após carregar o rascunho, substitui os dados de produção")
    args = ap.parse_args(argv)
    if not (config.DB_NAME and config.DB_NAME_STAGE) or config.DB_NAME == config.DB_NAME_STAGE:
        raise SystemExit("DB_NAME e DB_NAME_STAGE ausentes ou iguais: confira o .env (veja .env.example)")
    gravar_stage(*montar_predios())
    if args.promover:
        promover()
    print("Done.")
