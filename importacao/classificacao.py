"""Tabelas de/para e regra de classificação do tipo de uso da edificação."""

# COD_ESPECIE do QG (CNEFE/IBGE) -> tipo de uso. Códigos oficiais:
# 1=domicílio particular, 2=domicílio coletivo, 3=agropecuário,
# 4=ensino, 5=saúde, 6=outras finalidades, 7=em construção,
# 8=religioso. Sem categoria para "em construção": desconhecido.
ESPECIE_TO_TIPO = {
    "1": "residencial",
    "2": "hospedagem",
    "3": "agropecuario",
    "4": "institucional",
    "5": "servico",
    "6": "comercial",
    "7": "desconhecido",
    "8": "religioso",
}

# Fallback para prédios sem ponto QG associado.
OVERTURE_SUBTYPE_TO_TIPO = {
    "residential": "residencial",
    "commercial": "comercial",
    "religious": "religioso",
    "civic": "institucional",
    "medical": "servico",
    "education": "institucional",
    "transportation": "servico",
    "entertainment": "servico",
    "outbuilding": "desconhecido",
}
OVERTURE_CLASS_TO_TIPO = {
    "house": "residencial",
    "retail": "comercial",
    "church": "religioso",
    "school": "institucional",
    "library": "institucional",
    "post_office": "institucional",
    "hotel": "hospedagem",
    "roof": "desconhecido",
}


def classify(qg_props, overture_props):
    """(tipo, ORIGEM_TIPO): CNEFE prevalece, depois subtype, depois class."""
    especie = (qg_props or {}).get("COD_ESPECIE")
    if especie in ESPECIE_TO_TIPO:
        return ESPECIE_TO_TIPO[especie], "cnefe"
    subtype = (overture_props or {}).get("subtype")
    if subtype in OVERTURE_SUBTYPE_TO_TIPO:
        return OVERTURE_SUBTYPE_TO_TIPO[subtype], "overture_subtype"
    cls = (overture_props or {}).get("class")
    if cls in OVERTURE_CLASS_TO_TIPO:
        return OVERTURE_CLASS_TO_TIPO[cls], "overture_class"
    return "desconhecido", "sem_dado"


def best_type(qg_props, overture_props):
    return classify(qg_props, overture_props)[0]
