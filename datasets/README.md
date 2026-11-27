# Dados do Atlas Urbano

Os dados brutos ficam em `data/raw/`, fora do GitHub (pasta listada no `.gitignore`; juntos somam cerca de 61 MB). Esta página diz de onde vem cada arquivo, a licença, o tamanho e como obtê-lo. Os caminhos que a carga lê estão em `importacao/config.py`.

Itens marcados com **[a confirmar]** dependem de informação que só a equipe tem (como o arquivo foi baixado). Preencham antes da entrega final.

## Resumo

| Arquivo (em `data/raw/`) | Fonte | Licença | Tamanho | Registros | Obrigatório |
| --- | --- | --- | --- | --- | --- |
| `overture/overture-2026-08-19.0-building-municipio.geojson` | Overture Maps, tema *buildings*, release `2026-08-19.0` | ODbL-1.0 | 31,5 MB | 55.255 polígonos | Sim |
| `overture/overture-2026-08-19.0-building--38.236,-9.410,-38.212,-9.395.geojson` | Overture Maps, mesma release, janela do centro | ODbL-1.0 | 4,5 MB | 8.382 polígonos | Não (já contido no municipal) |
| `qg/qg_810_endereco_Munic2924009.json` | IBGE, CNEFE do Censo 2022, município 2924009 (Paulo Afonso/BA) | Dados públicos do IBGE (citar a fonte) | 25,0 MB | 61.965 pontos | Sim |
| `osm/osm_buildings_paulo_afonso.geojson` | OpenStreetMap via API Overpass | ODbL-1.0 | — | — | Não (fallback) |
| `municipio_boundary.geojson` | Contorno do município | **[a confirmar]** | — | — | Não (sem ele a carga não recorta) |

## Overture Maps: polígonos das edificações

- **O que é:** a forma (polígono) de cada prédio. A Overture junta várias bases. Neste recorte, as fontes de cada polígono (campo `sources`) são Google Open Buildings (44.620), Microsoft ML Buildings (8.802) e OpenStreetMap (1.833).
- **Licença:** ODbL-1.0, informada em `sources[].license` de cada feição. Exige atribuição ("© Overture Maps Foundation, OpenStreetMap contributors") e que bases derivadas mantenham a mesma licença.
- **Formato:** GeoJSON em lon/lat (WGS 84). Campos usados: `sources`, `subtype`, `class`, `num_floors`, `level`, `names`, `has_parts`, `is_underground`, `version`.
- **Extensão do arquivo municipal:** lon -38,35 a -38,05 e lat -9,55 a -9,30.
- **Como obter [a confirmar]:** a ferramenta e o recorte usados no download. Uma forma reproduzível é o CLI oficial (`pip install overturemaps`):

  ```bash
  overturemaps download --bbox=-38.35,-9.55,-38.05,-9.30 -f geojson --type=building \
    -o data/raw/overture/overture-2026-08-19.0-building-municipio.geojson
  ```

  O CLI baixa a release mais recente. Com outra release, as contagens mudam e os testes de integração que dependem delas precisam de ajuste.

## CNEFE (IBGE): endereços e espécie

- **O que é:** o Cadastro Nacional de Endereços para Fins Estatísticos do Censo Demográfico 2022, com um ponto por endereço. É de onde vem o tipo de uso (o rótulo que o classificador aprende).
- **Licença:** dados públicos do IBGE, de uso livre com citação da fonte ("Fonte: IBGE, Censo Demográfico 2022, CNEFE").
- **LGPD:** não há nomes de moradores nem dados pessoais. O campo `DSC_ESTABELECIMENTO` traz só o nome de estabelecimentos (comércios, escolas, igrejas).
- **Formato:** GeoJSON (FeatureCollection) em SIRGAS 2000 (`EPSG:4674`), com 14 atributos por ponto: `COD_UNICO_ENDERECO`, `COD_UF`, `COD_MUNICIPIO`, `COD_SETOR`, `NUM_QUADRA`, `NUM_FACE`, `CEP`, `DSC_LOCALIDADE`, `LOGRAD_NUM`, `COMPLEMENTO`, `NV_GEO_COORD`, `COD_TIPO_ESPECI`, `DSC_ESTABELECIMENTO` e `COD_ESPECIE`.
- **Espécies no arquivo (`COD_ESPECIE`) e como a carga as trata:**

  | Código | Espécie (IBGE) | Pontos | Uso no Atlas |
  | --- | --- | --- | --- |
  | 1 | Domicílio particular | 49.940 | residencial |
  | 2 | Domicílio coletivo | 35 | hospedagem |
  | 3 | Estabelecimento agropecuário | 1.219 | agropecuario |
  | 4 | Estabelecimento de ensino | 140 | institucional |
  | 5 | Estabelecimento de saúde | 150 | servico |
  | 6 | Estabelecimento de outras finalidades | 7.274 | comercial |
  | 7 | Edificação em construção | 2.902 | desconhecido |
  | 8 | Estabelecimento religioso | 305 | religioso |

- **Como obter [a confirmar]:** a página do IBGE de onde o arquivo foi baixado e o que significa o prefixo `qg_810` (o arquivo parece exportado de um SIG, pois traz `crs` e `name`). O IBGE publica as coordenadas do CNEFE 2022 por município no portal de downloads de Geociências, na área do Censo 2022. O código do município é `2924009`.

## OpenStreetMap: fallback opcional

- **Como obter:** `./.venv/bin/python scripts/fetch_osm_buildings.py` consulta a API Overpass em 6 × 6 células e grava `data/raw/osm/osm_buildings_paulo_afonso.geojson`. Demora vários minutos e não pede login.
- **Licença:** ODbL-1.0 ("© OpenStreetMap contributors").
- **Uso:** só para prédios que o Overture não cobre. Na carga atual o arquivo não está presente. Os 1.833 polígonos com fonte OpenStreetMap vêm do próprio Overture.

## Contorno do município: opcional

`data/raw/municipio_boundary.geojson` (Feature ou FeatureCollection com o polígono de Paulo Afonso). Se existir, a carga descarta os prédios fora do município. Sem ele, a carga avisa e segue sem recorte. **[a confirmar]** a origem, se for usado. A malha municipal do IBGE é a opção natural.

## Preparo dos dados

A carga (`python importacao/import_data.py --promover`) faz o seguinte:

1. une os arquivos do Overture sem repetir IDs;
2. recorta no município, se houver contorno;
3. associa cada endereço do CNEFE ao polígono que o contém, com tolerância de cerca de 15 m na borda;
4. define o tipo de uso de cada prédio pelo endereço mais frequente, e sem endereço pelo subtipo ou classe do Overture;
5. grava tudo no MariaDB.

A carga atual (`CARGAS`) resultou em 55.255 edificações, 61.965 endereços e 53.284 vínculos endereço–prédio. Dessas edificações, 32.071 ficaram sem uso conhecido, e são elas que o classificador de IA estima.
