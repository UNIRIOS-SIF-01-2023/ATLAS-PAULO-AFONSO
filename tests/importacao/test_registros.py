import json

from shapely.geometry import box

from importacao.registros import _num, construir_registros


def endereco(cod, especie, localidade=None, x=0.0, y=0.0):
    props = {"COD_UNICO_ENDERECO": str(cod), "COD_ESPECIE": especie, "COD_SETOR": "S1",
             "NV_GEO_COORD": "1", "LOGRAD_NUM": "10"}
    if localidade:
        props["DSC_LOCALIDADE"] = localidade
    return {"properties": props, "geometry": {"type": "Point", "coordinates": [x, y]}}


def predio(props, num_pav=None, fonte="Overture"):
    return {"geom": box(0, 0, 1, 1), "id_origem": "id", "fonte": fonte, "licenca": "L",
            "confianca": 0.5, "fontes": [{"dataset": fonte}], "props": props,
            "num_pav": num_pav}


def test_num():
    assert _num("3") == 3 and _num(2.0) == 2 and _num(None) is None and _num("") is None


def test_registros_completos():
    qg = [endereco(10, "1", "Centro"), endereco(11, "1", "Centro"),
          endereco(12, "6"), endereco(13, "1", "Centro")]  # o último não tem prédio
    predios = [predio({}, num_pav=3), predio({"subtype": "religious"}, fonte="OpenStreetMap")]
    assoc = {0: (0, "contido", 0.0), 1: (0, "contido", 0.0), 2: (0, "tolerancia", 4.2)}

    r = construir_registros(predios, qg, assoc)

    assert r.localidades == {"Centro": 1, "Não informado": 2}
    assert r.fontes == {"Overture": (1, "L"), "OpenStreetMap": (2, "L")}

    # Prédio 1: 2 residenciais + 1 comercial -> residencial, uso misto, bairro Centro.
    e1 = r.edificacoes[0]
    assert e1[:9] == (1, 1, 1, "residencial", "cnefe", 1, 3, 3, 0.5)
    assert json.loads(e1[9])["type"] == "Polygon"
    assert json.loads(e1[10])["type"] == "Point"

    # Prédio 2: sem endereços -> classificado pelo Overture/OSM, sem bairro.
    e2 = r.edificacoes[1]
    assert e2[:9] == (2, 2, None, "religioso", "overture_subtype", 0, 0, None, 0.5)

    assert [o[:2] for o in r.origens] == [(1, "id"), (2, "id")]
    assert len(r.enderecos) == 4
    assert r.enderecos[0][:3] == (1, 10, 1)
    assert r.enderecos[2][8] == 2  # localidade "Não informado"
    assert r.vinculos == [(1, 1, "contido", 0.0), (2, 1, "contido", 0.0),
                          (3, 1, "tolerancia", 4.2)]
