"""Transforma prédios, endereços e vínculos em linhas prontas para INSERT.

Tudo aqui é função pura (sem banco), por isso é a parte mais fácil de testar.
"""

import json
from collections import Counter
from dataclasses import dataclass

from shapely.geometry import mapping

from . import config
from .classificacao import classify


@dataclass
class Registros:
    fontes: dict        # nome -> (id, licença)
    localidades: dict   # nome -> id (também usado como id do bairro)
    edificacoes: list
    origens: list
    enderecos: list
    vinculos: list


def _num(v):
    return int(v) if v not in (None, "") else None


def _mais_comum(valores):
    return Counter(valores).most_common(1)[0][0]


def _nome_localidade(props):
    return props.get("DSC_LOCALIDADE") or config.NAO_INFORMADO


def indexar_localidades(qg_features):
    """Bairro = DSC_LOCALIDADE do CNEFE (1:1) até haver malha oficial de bairros."""
    localidades = {}
    for qf in qg_features:
        localidades.setdefault(_nome_localidade(qf["properties"]), len(localidades) + 1)
    return localidades


def indexar_fontes(predios):
    fontes = {}
    for p in predios:
        fontes.setdefault(p["fonte"], (len(fontes) + 1, p["licenca"]))
    return fontes


def agrupar_por_predio(qg_features, assoc, localidades):
    """({prédio: [tipos dos endereços]}, {prédio: [ids de localidade]})."""
    tipos, bairros = {}, {}
    for qi, (bi, _, _) in assoc.items():
        qp = qg_features[qi]["properties"]
        tipos.setdefault(bi, []).append(classify(qp, None)[0])
        bairros.setdefault(bi, []).append(localidades[_nome_localidade(qp)])
    return tipos, bairros


def montar_edificacoes(predios, fontes, tipos_do_predio, bairros_do_predio):
    """(linhas de EDIFICACOES, linhas de EDIFICACAO_ORIGEM); IDs = posição + 1."""
    edificacoes, origens = [], []
    for bi, p in enumerate(predios):
        tipos = tipos_do_predio.get(bi)
        if tipos:
            tipo, origem = _mais_comum(tipos), "cnefe"
            bairro_id = _mais_comum(bairros_do_predio[bi])
        else:
            (tipo, origem), bairro_id = classify(None, p["props"]), None
        edificacoes.append((
            bi + 1, fontes[p["fonte"]][0], bairro_id, tipo, origem,
            int(len(set(tipos or [])) > 1), len(tipos or []), _num(p["num_pav"]),
            p["confianca"], json.dumps(mapping(p["geom"])),
            json.dumps(mapping(p["geom"].representative_point())),
        ))
        origens.append((bi + 1, p["id_origem"],
                        json.dumps(p["fontes"], ensure_ascii=False, default=str),
                        json.dumps(p["props"], ensure_ascii=False, default=str)))
    return edificacoes, origens


def montar_enderecos(qg_features, localidades):
    enderecos = []
    for qi, qf in enumerate(qg_features):
        qp = qf["properties"]
        enderecos.append((
            qi + 1, int(qp["COD_UNICO_ENDERECO"]), int(qp["COD_ESPECIE"]),
            _num(qp.get("COD_TIPO_ESPECI")), qp["COD_SETOR"],
            _num(qp.get("NUM_QUADRA")), _num(qp.get("NUM_FACE")), qp.get("CEP") or None,
            localidades[_nome_localidade(qp)],
            qp.get("LOGRAD_NUM") or "", qp.get("COMPLEMENTO") or None,
            qp.get("DSC_ESTABELECIMENTO") or None, int(qp["NV_GEO_COORD"]),
            json.dumps(qf["geometry"]),
        ))
    return enderecos


def montar_vinculos(assoc):
    return [(qi + 1, bi + 1, metodo, dist) for qi, (bi, metodo, dist) in assoc.items()]


def construir_registros(predios, qg_features, assoc):
    """Converte o resultado de montar_predios() em linhas para todas as tabelas."""
    localidades = indexar_localidades(qg_features)
    fontes = indexar_fontes(predios)
    tipos, bairros = agrupar_por_predio(qg_features, assoc, localidades)
    edificacoes, origens = montar_edificacoes(predios, fontes, tipos, bairros)
    return Registros(
        fontes=fontes,
        localidades=localidades,
        edificacoes=edificacoes,
        origens=origens,
        enderecos=montar_enderecos(qg_features, localidades),
        vinculos=montar_vinculos(assoc),
    )
