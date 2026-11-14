from importacao.classificacao import best_type, classify


def test_cnefe_prevalece_sobre_overture():
    assert classify({"COD_ESPECIE": "6"}, {"subtype": "residential"}) == ("comercial", "cnefe")


def test_usa_subtype_do_overture_sem_cnefe():
    assert classify(None, {"subtype": "religious"}) == ("religioso", "overture_subtype")


def test_usa_class_do_overture_quando_subtype_desconhecido():
    assert classify({}, {"subtype": "x", "class": "hotel"}) == ("hospedagem", "overture_class")


def test_sem_dado():
    assert classify(None, None) == ("desconhecido", "sem_dado")


def test_especie_fora_da_tabela_cai_para_overture():
    assert classify({"COD_ESPECIE": "99"}, {"class": "house"}) == ("residencial", "overture_class")


def test_em_construcao_vira_desconhecido_mas_com_origem_cnefe():
    assert classify({"COD_ESPECIE": "7"}, None) == ("desconhecido", "cnefe")


def test_best_type_devolve_so_o_tipo():
    assert best_type({"COD_ESPECIE": "8"}, None) == "religioso"
