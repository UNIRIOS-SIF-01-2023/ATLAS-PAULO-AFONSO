"""Constantes e caminhos da importação (nenhuma lógica aqui)."""

# Único ponto de contato com backend.config: o resto do pacote lê daqui
# (config.DB_NAME etc.), o que também permite trocar valores nos testes.
from backend.config import DB_NAME, DB_NAME_STAGE, SRID  # noqa: F401

DATA_DIR = "data/raw"
OVERTURE_FILES = [
    f"{DATA_DIR}/overture/edificacoes_overture.geojson",
    f"{DATA_DIR}/overture/overture-2026-08-19.0-building--38.236,-9.410,-38.212,-9.395.geojson",
    f"{DATA_DIR}/overture/overture-2026-08-19.0-building-municipio.geojson",
]
# 2ª fonte (API aberta Overpass/OSM, sem login): fallback onde o Overture
# não tem polígono. Ausência do arquivo só desliga o fallback.
OSM_BUILDINGS = f"{DATA_DIR}/osm/osm_buildings_paulo_afonso.geojson"
MUNICIPIO_BOUNDARY = f"{DATA_DIR}/municipio_boundary.geojson"
QG_ENDERECOS = f"{DATA_DIR}/qg/qg_810_endereco_Munic2924009.json"
MUNICIPIO = "2924009"  # Paulo Afonso/BA

# Fonte atribuída aos polígonos que vêm do arquivo OSM.
FONTE_OSM = "OpenStreetMap"
LICENCA_OSM = "ODbL-1.0"

# ~15 m de tolerância na borda (ex.: porta na fachada, erro de GPS).
POINT_TOLERANCE_DEG = 0.000135
# shortcut: 1° ≈ 111.320 m sem corrigir pela latitude (erro < 2% em -9°),
# troque por distância geodésica se DISTANCIA_M virar critério de método.
METROS_POR_GRAU = 111320.0

NAO_INFORMADO = "Não informado"

# Pais antes de filhos: ordem de carga (e inversa para apagar).
TABELAS = [
    "FONTES", "BAIRROS", "LOCALIDADES", "EDIFICACOES", "EDIFICACAO_ORIGEM",
    "ENDERECOS", "ENDERECO_EDIFICACAO", "AGG_BAIRRO_TIPO",
]

# Linhas por INSERT multi-valores (ver stage.inserir_em_lotes).
TAMANHO_LOTE = 500
