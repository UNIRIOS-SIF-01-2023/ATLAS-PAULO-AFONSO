import type { ExpressionSpecification, GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl';
import {
  ALTURA_PADRAO_POR_TIPO,
  ALTURA_PAVIMENTO_M,
  coresPorTema,
  FONTE_PONTO,
  TIPOS_ORDEM,
  type Theme,
} from './constants';
import { corDaConfianca, ROOFTOP_CONFIDENCES, type RooftopOptions } from './rooftop';
import {
  buildingTileUrl,
  DOT_MIN_ZOOM,
  dotRadiusExpression,
  fadeRamp,
  FOOTPRINT_MAX_ZOOM,
  FOOTPRINT_MIN_ZOOM,
  navigableBounds,
  overviewColorExpression,
  overviewCountExpression,
  OVERVIEW_LEVELS,
  overviewUrl,
  type MapMeta,
} from './mapTiles';

// Mapa base vetorial, gratuito e sem chave (openfreemap.org).
// Positron para o tema claro e Dark Matter para o escuro.
export const BASEMAP_URL_DARK = 'https://tiles.openfreemap.org/styles/dark';
export const BASEMAP_URL_BRIGHT = 'https://tiles.openfreemap.org/styles/positron';
export const BASEMAP_URL = BASEMAP_URL_DARK;

export function basemapUrl(theme: Theme = 'dark'): string {
  return theme === 'light' ? BASEMAP_URL_BRIGHT : BASEMAP_URL_DARK;
}

export const SELECTED_LAYER = 'selected-building-3d';
export const SELECTED_OUTLINE = 'selected-outline';
export const FILL_LAYER = 'buildings-fill';
export const OUTLINE_LAYER = 'buildings-outline';
export const DOT_LAYER = 'buildings-dots';
export const EXTRUSION_LAYER = 'buildings-3d';
export const overviewId = (level: number) => `overview-${level}`;
export const OVERVIEW_LAYERS = OVERVIEW_LEVELS.map(({ level }) => overviewId(level));
export const CLICKABLE_LAYERS = [SELECTED_LAYER, EXTRUSION_LAYER, FILL_LAYER, DOT_LAYER];

export const ROOFTOP_SOURCE = 'rooftop-vila-nobre';
export const ROOFTOP_POLYGON_SOURCE = 'rooftop-poligonos';
export const ROOFTOP_LAYER = 'rooftop-points';
export const ROOFTOP_LABEL_LAYER = 'rooftop-address-labels';
export const ROOFTOP_POLYGON_LAYER = 'rooftop-polygon-fill';
export const ROOFTOP_POLYGON_LINE_LAYER = 'rooftop-polygon-line';
export const ROOFTOP_CLICKABLE_LAYERS = [ROOFTOP_LAYER, ROOFTOP_POLYGON_LAYER];

export const CNEFE_SOURCE = 'cnefe-original';
export const CNEFE_LAYER = 'cnefe-original-points';

const confiancaColor = (theme: Theme): any => ['match', ['get', 'confianca'],
  'alta', corDaConfianca('alta', theme),
  'media', corDaConfianca('media', theme),
  'baixa', corDaConfianca('baixa', theme),
  corDaConfianca('nenhuma', theme)];

/** Camada local do CNEFE rooftop (sem API externa), para conferência antes/depois.
 *
 * Com `dynamic`, as fontes nascem vazias e o App preenche por viewport
 * (`setRooftopData`): o modo debug carrega só a área visível, não os 38MB.
 */
export function addRooftopLayer(map: MapLibreMap, apiUrl: string, beforeId?: string, theme: Theme = 'dark', opts: { dynamic?: boolean } = {}) {
  const fonte = (url: string): any => (opts.dynamic
    ? { type: 'FeatureCollection', features: [] }
    : url);
  // Polígonos primeiro: o preenchimento do prédio fica abaixo dos pontos.
  map.addSource(ROOFTOP_POLYGON_SOURCE, {
    type: 'geojson',
    data: fonte(`${apiUrl}/api/rooftop/vila-nobre/poligonos`),
  });
  map.addLayer({
    id: ROOFTOP_POLYGON_LAYER,
    type: 'fill',
    source: ROOFTOP_POLYGON_SOURCE,
    paint: {
      'fill-color': confiancaColor(theme),
      'fill-opacity': ['case', ['==', ['get', 'mudou'], true], 0.38, 0.22],
    },
  }, beforeId);
  map.addLayer({
    id: ROOFTOP_POLYGON_LINE_LAYER,
    type: 'line',
    source: ROOFTOP_POLYGON_SOURCE,
    paint: {
      'line-color': confiancaColor(theme),
      'line-width': ['case', ['==', ['get', 'revisar'], true], 2.2, 1.1],
      'line-opacity': 0.9,
    },
  }, beforeId);

  map.addSource(ROOFTOP_SOURCE, {
    type: 'geojson',
    data: fonte(`${apiUrl}/api/rooftop/vila-nobre`),
  });
  map.addLayer({
    id: ROOFTOP_LAYER,
    type: 'circle',
    source: ROOFTOP_SOURCE,
    minzoom: 14,
    paint: {
      'circle-radius': [
        'interpolate', ['linear'], ['zoom'],
        14, 2.5,
        16, 3.8,
        18, 5.2,
      ],
      'circle-color': confiancaColor(theme),
      'circle-stroke-width': 0.8,
      'circle-stroke-color': theme === 'light' ? '#ffffff' : '#0f172a',
      'circle-opacity': 0.88,
    },
  }, beforeId);
  map.addLayer({
    id: ROOFTOP_LABEL_LAYER,
    type: 'symbol',
    source: ROOFTOP_SOURCE,
    minzoom: 16,
    layout: {
      'text-field': ['get', 'endereco'],
      'text-size': 11,
      'text-max-width': 12,
      'text-offset': [0, 1.2],
      'text-allow-overlap': false,
    },
    paint: {
      'text-color': theme === 'light' ? '#172554' : '#eff6ff',
      'text-halo-color': theme === 'light' ? '#ffffff' : '#0f172a',
      'text-halo-width': 1.5,
    },
  }, beforeId);
}

/** Preenche as fontes dinâmicas com a área visível carregada do backend.
 * `pontos` nulo mantém o que já está lá (abaixo do zoom 14 os pontos nem desenham). */
export function setRooftopData(map: MapLibreMap, pontos: unknown, poligonos: unknown) {
  const fontePontos = map.getSource(ROOFTOP_SOURCE) as GeoJSONSource | undefined;
  if (fontePontos && pontos) fontePontos.setData(pontos as any);
  const fontePoligonos = map.getSource(ROOFTOP_POLYGON_SOURCE) as GeoJSONSource | undefined;
  if (fontePoligonos && poligonos) fontePoligonos.setData(poligonos as any);
}

function combinarFiltros(a: any, b: any): any {
  if (a && b) return ['all', a, b];
  return a ?? b;
}

/** Liga/desliga pontos e polígonos e aplica confiança/alterados sem recarregar o GeoJSON.
 *
 * No modo Final os polígonos ficam pintados pelo TIPO do prédio (mesma paleta da
 * visão normal do aplicativo), sem marcação de confiança e sem pontos — é como o
 * app ficaria publicado, com o endereço disponível ao clicar. */
export function applyRooftopFilters(map: MapLibreMap, options: RooftopOptions, theme: Theme = 'dark') {
  const todas = options.confiancas.size === ROOFTOP_CONFIDENCES.length;
  const filtroConfianca = todas
    ? undefined
    : ['in', ['get', 'confianca'], ['literal', [...options.confiancas]]];
  const filtroPontos = combinarFiltros(
    filtroConfianca,
    options.onlyChanged
      ? ['any', ['==', ['get', 'mudou_bld'], true], ['==', ['get', 'mudou_ponto'], true]]
      : undefined,
  );
  const filtroPoligonos = combinarFiltros(
    filtroConfianca,
    options.onlyChanged ? ['==', ['get', 'mudou'], true] : undefined,
  );

  const isFinal = options.viewMode === 'final';
  const pointsVisible = !isFinal && options.showPoints;
  const corPoligonos = isFinal ? colorByTipo(['get', 'tipo'], theme) : confiancaColor(theme);

  for (const layer of [ROOFTOP_LAYER, ROOFTOP_LABEL_LAYER]) {
    if (!map.getLayer(layer)) continue;
    map.setFilter(layer, filtroPontos);
    map.setLayoutProperty(layer, 'visibility', pointsVisible ? 'visible' : 'none');
  }
  for (const layer of [ROOFTOP_POLYGON_LAYER, ROOFTOP_POLYGON_LINE_LAYER]) {
    if (!map.getLayer(layer)) continue;
    map.setFilter(layer, filtroPoligonos);
    map.setLayoutProperty(layer, 'visibility', options.showPolygons ? 'visible' : 'none');
  }
  if (map.getLayer(ROOFTOP_POLYGON_LAYER)) {
    map.setPaintProperty(ROOFTOP_POLYGON_LAYER, 'fill-color', corPoligonos);
    map.setPaintProperty(
      ROOFTOP_POLYGON_LAYER,
      'fill-opacity',
      isFinal ? 0.85 : ['case', ['==', ['get', 'mudou'], true], 0.38, 0.22],
    );
  }
  if (map.getLayer(ROOFTOP_POLYGON_LINE_LAYER)) {
    map.setPaintProperty(ROOFTOP_POLYGON_LINE_LAYER, 'line-color', corPoligonos);
    map.setPaintProperty(
      ROOFTOP_POLYGON_LINE_LAYER,
      'line-width',
      isFinal ? 1.1 : ['case', ['==', ['get', 'revisar'], true], 2.2, 1.1],
    );
  }
}

export function removeRooftopLayer(map: MapLibreMap) {
  for (const layer of [ROOFTOP_LABEL_LAYER, ROOFTOP_LAYER, ROOFTOP_POLYGON_LINE_LAYER, ROOFTOP_POLYGON_LAYER]) {
    if (map.getLayer(layer)) map.removeLayer(layer);
  }
  for (const source of [ROOFTOP_SOURCE, ROOFTOP_POLYGON_SOURCE]) {
    if (map.getSource(source)) map.removeSource(source);
  }
}

/** Camada comparativa: endereços ORIGINAIS do CNEFE (antes do rooftop).
 * Círculos vazados azuis, desenhados POR CIMA da camada rooftop — quando o
 * resultado final cai no endereço original, os dois aparecem concêntricos. */
export function addCnefeLayer(map: MapLibreMap, beforeId?: string, theme: Theme = 'dark') {
  map.addSource(CNEFE_SOURCE, {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  });
  map.addLayer({
    id: CNEFE_LAYER,
    type: 'circle',
    source: CNEFE_SOURCE,
    minzoom: 14,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 3.2, 16, 5.0, 18, 7.0],
      'circle-color': 'transparent',
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 14, 1.2, 16, 1.6, 18, 2],
      'circle-stroke-color': theme === 'light' ? '#0e7490' : '#22d3ee',
    },
  }, beforeId);
}

export function setCnefeData(map: MapLibreMap, pontos: unknown) {
  const fonte = map.getSource(CNEFE_SOURCE) as GeoJSONSource | undefined;
  if (fonte && pontos) fonte.setData(pontos as any);
}

export function removeCnefeLayer(map: MapLibreMap) {
  if (map.getLayer(CNEFE_LAYER)) map.removeLayer(CNEFE_LAYER);
  if (map.getSource(CNEFE_SOURCE)) map.removeSource(CNEFE_SOURCE);
}

export function colorByTipo(tipo: unknown, theme: Theme = 'dark'): ExpressionSpecification {
  const cores = coresPorTema(theme);
  return ['match', tipo, ...TIPOS_ORDEM.flatMap((t) => [t, cores[t]]), cores.desconhecido] as any;
}

/** Mesma regra de alturaDoPredio (constants.ts), como expressão do mapa. */
const heightExpression: any = ['case',
  ['>', ['coalesce', ['get', 'num_pavimentos'], 0], 0],
  ['*', ['get', 'num_pavimentos'], ALTURA_PAVIMENTO_M],
  ['match', ['get', 'tipo'], ...TIPOS_ORDEM.flatMap((t) => [t, ALTURA_PADRAO_POR_TIPO[t]]), ALTURA_PADRAO_POR_TIPO.desconhecido],
];

// Paleta do mapa base escuro: azul-grafite profundo em vez do preto puro do estilo
// original, com a água bem separada da terra.
const BASEMAP_DARK = {
  background: '#0b0f17',
  land: '#0f141e',
  green: '#0e1a18',
  water: '#0c2235',
  waterway: '#123049',
  roadMinor: '#1a2130',
  roadMajor: '#273043',
  roadMotorway: '#323d52',
  label: '#8b97ab',
  labelHalo: 'rgba(11, 15, 23, 0.92)',
};

// Paleta do mapa base claro: cinza-azulado suave, limpo e cristalino,
// com vegetação suave e água celeste delicada sob o vidro líquido.
const BASEMAP_LIGHT = {
  background: '#eef2f6',
  land: '#f5f7fa',
  green: '#d9ebd8',
  water: '#bde0f7',
  waterway: '#9ccaf0',
  roadMinor: '#ffffff',
  roadMajor: '#e3e8f0',
  roadMotorway: '#cbd5e1',
  label: '#334155',
  labelHalo: 'rgba(255, 255, 255, 0.95)',
};

function paint(map: MapLibreMap, layer: string, property: string, value: unknown) {
  try {
    map.setPaintProperty(layer, property as any, value);
  } catch {
    // Uma camada do estilo externo pode não ter essa propriedade.
  }
}

/**
 * Recolore o estilo do OpenFreeMap e esconde os prédios dele (os
 * nossos vêm do backend). Devolve a primeira camada de rótulos, abaixo da
 * qual entram os dados.
 */
export function styleBasemap(map: MapLibreMap, theme: Theme = 'dark'): string | undefined {
  const palette = theme === 'light' ? BASEMAP_LIGHT : BASEMAP_DARK;
  const layers = map.getStyle().layers ?? [];
  let firstLabel: string | undefined;
  for (const layer of layers) {
    const id = layer.id;
    const sourceLayer = 'source-layer' in layer ? layer['source-layer'] : undefined;
    if (layer.type === 'background') paint(map, id, 'background-color', palette.background);
    else if (layer.type === 'fill') {
      if (sourceLayer === 'building') map.setLayoutProperty(id, 'visibility', 'none');
      else if (sourceLayer === 'water') paint(map, id, 'fill-color', palette.water);
      else if (/park|wood|grass/.test(id)) paint(map, id, 'fill-color', palette.green);
      else if (sourceLayer === 'landuse' || sourceLayer === 'landcover') paint(map, id, 'fill-color', palette.land);
    } else if (layer.type === 'line') {
      if (sourceLayer === 'waterway') paint(map, id, 'line-color', palette.waterway);
      else if (/motorway/.test(id) && !/casing/.test(id)) paint(map, id, 'line-color', palette.roadMotorway);
      else if (/major/.test(id) && !/casing/.test(id)) paint(map, id, 'line-color', palette.roadMajor);
      else if (/minor|path/.test(id)) paint(map, id, 'line-color', palette.roadMinor);
      else if (/casing/.test(id)) paint(map, id, 'line-color', palette.background);
    } else if (layer.type === 'symbol') {
      paint(map, id, 'text-color', palette.label);
      paint(map, id, 'text-halo-color', palette.labelHalo);
      // Rótulos de vias e lugares ficam acima dos prédios; os da água, não.
      if (!firstLabel && sourceLayer !== 'water_name') firstLabel = id;
    }
  }
  return firstLabel;
}

/** Prédio selecionado: extrudado em 3D e com contorno de destaque. Fica no topo. */
export function addSelectionLayers(map: MapLibreMap, theme: Theme = 'dark') {
  map.addSource('selected', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({
    id: SELECTED_LAYER, type: 'fill-extrusion', source: 'selected',
    paint: {
      'fill-extrusion-color': colorByTipo(['get', 'tipo'], theme),
      'fill-extrusion-height': ['number', ['get', 'altura_3d_m'], ALTURA_PADRAO_POR_TIPO.desconhecido],
      'fill-extrusion-base': 0,
      'fill-extrusion-opacity': 0.95,
      'fill-extrusion-vertical-gradient': true,
    },
  });
  map.addLayer({
    id: SELECTED_OUTLINE, type: 'line', source: 'selected',
    paint: {
      'line-color': theme === 'light' ? '#0f172a' : '#ffffff',
      'line-width': 2,
      'line-opacity': 0.9,
      'line-blur': 0.5,
    },
  });
}

/** Grades da mais grossa à mais fina, depois pontos e polígonos (ver mapTiles.ts). */
export function addDataLayers(map: MapLibreMap, meta: MapMeta, apiUrl: string, beforeId?: string, theme: Theme = 'dark') {
  const tipoColor = colorByTipo(['get', 'tipo'], theme);

  for (const { level, enter, exit } of OVERVIEW_LEVELS) {
    map.addSource(overviewId(level), { type: 'geojson', data: overviewUrl(apiUrl, meta.version, level) });
    // As mais finas ficam por cima e cobrem a anterior enquanto ela sai.
    map.addLayer({
      id: overviewId(level), type: 'fill', source: overviewId(level),
      minzoom: enter ?? 0, maxzoom: exit + 1,
      paint: { 'fill-antialias': false },
    }, beforeId);
  }
  map.addSource('buildings', {
    type: 'vector',
    tiles: [buildingTileUrl(apiUrl, meta.version)],
    minzoom: DOT_MIN_ZOOM,
    maxzoom: FOOTPRINT_MAX_ZOOM,
    // Fora da área com dados não há o que pedir ao servidor.
    ...(meta.bounds ? { bounds: meta.bounds } : {}),
  });
  // Um disco com a área de cada prédio; no zoom 14 vira o próprio polígono.
  map.addLayer({
    id: DOT_LAYER, type: 'circle', source: 'buildings', 'source-layer': 'dots',
    minzoom: DOT_MIN_ZOOM, maxzoom: FOOTPRINT_MIN_ZOOM + 1,
    paint: {
      'circle-color': tipoColor,
      'circle-radius': dotRadiusExpression(),
      'circle-opacity': fadeRamp(DOT_MIN_ZOOM, FOOTPRINT_MIN_ZOOM, 0.9),
      // Inclinados como os polígonos, para a troca não mudar a perspectiva.
      'circle-pitch-alignment': 'map',
      'circle-pitch-scale': 'map',
    },
  }, beforeId);
  map.addLayer({
    id: FILL_LAYER, type: 'fill', source: 'buildings', 'source-layer': 'buildings',
    paint: {
      'fill-color': tipoColor,
      // Os prédios grandes já vêm como polígono desde o zoom 12 (backend/main.py).
      'fill-opacity': fadeRamp(DOT_MIN_ZOOM, null, 0.86),
    },
  }, beforeId);
  map.addLayer({
    id: OUTLINE_LAYER, type: 'line', source: 'buildings', 'source-layer': 'buildings',
    paint: {
      // Sobre o fundo escuro, um traço escuro separa prédios colados.
      // Sobre o fundo claro, um traço suave escuro sem poluir.
      'line-color': theme === 'light' ? 'rgba(15, 23, 42, 0.22)' : 'rgba(5, 8, 14, 0.7)',
      'line-width': 0.6,
      'line-opacity': ['interpolate', ['linear'], ['zoom'], FOOTPRINT_MIN_ZOOM + 0.5, 0, FOOTPRINT_MIN_ZOOM + 1.5, 1],
    },
  }, beforeId);
  // Todos os prédios em 3D. Crescem do chão ao aproximar, para a troca com
  // os pontos e a planta 2D não saltar.
  map.addLayer({
    id: EXTRUSION_LAYER, type: 'fill-extrusion', source: 'buildings', 'source-layer': 'buildings',
    minzoom: FOOTPRINT_MIN_ZOOM,
    paint: {
      'fill-extrusion-color': tipoColor,
      'fill-extrusion-height': ['interpolate', ['linear'], ['zoom'], FOOTPRINT_MIN_ZOOM, 0, FOOTPRINT_MIN_ZOOM + 1, heightExpression],
      'fill-extrusion-base': 0,
      'fill-extrusion-opacity': 0.9,
      'fill-extrusion-vertical-gradient': true,
    },
  }, beforeId);
  if (meta.bounds) map.setMaxBounds(navigableBounds(meta.bounds));
}

export function removeDataLayers(map: MapLibreMap) {
  for (const layer of [...OVERVIEW_LAYERS, DOT_LAYER, FILL_LAYER, OUTLINE_LAYER, EXTRUSION_LAYER]) {
    if (map.getLayer(layer)) map.removeLayer(layer);
  }
  for (const source of [...OVERVIEW_LAYERS, 'buildings']) {
    if (map.getSource(source)) map.removeSource(source);
  }
}

export interface DataFilters {
  showPontos: boolean;
  soDoSelecionado: boolean;
  selectedGeometryKey: string;
  tipos: readonly string[];
}

/** Filtros da barra lateral aplicados a todas as escalas: grade, pontos e polígonos. */
/**
 * Todos os prédios em 3D ou só o selecionado. Com todos em 3D o contorno 2D
 * some: desenhado no chão, ele cortaria as paredes dos prédios.
 */
export function setAllBuildings3d(map: MapLibreMap, all: boolean) {
  map.setLayoutProperty(EXTRUSION_LAYER, 'visibility', all ? 'visible' : 'none');
  map.setLayoutProperty(OUTLINE_LAYER, 'visibility', all ? 'none' : 'visible');
}

export function applyDataFilters(map: MapLibreMap, { showPontos, soDoSelecionado, selectedGeometryKey, tipos }: DataFilters, theme: Theme = 'dark') {
  const clauses: any[] = [];
  if (tipos.length < TIPOS_ORDEM.length) clauses.push(['in', ['get', 'tipo'], ['literal', tipos]]);
  if (!showPontos) clauses.push(['!=', ['get', 'fonte'], FONTE_PONTO]);
  else if (soDoSelecionado) {
    // Esconde os endereços soltos, menos os do prédio selecionado; os
    // prédios da vizinhança continuam no mapa.
    const sameBuilding = selectedGeometryKey ? ['==', ['get', 'geometry_key'], selectedGeometryKey] : false;
    clauses.push(['any', ['!=', ['get', 'fonte'], FONTE_PONTO], sameBuilding]);
  }
  const filter = clauses.length === 0 ? null : clauses.length === 1 ? clauses[0] : ['all', ...clauses];
  for (const layer of [DOT_LAYER, FILL_LAYER, OUTLINE_LAYER]) map.setFilter(layer, filter);
  // Endereços sem prédio não sobem em 3D; o selecionado sai daqui porque a
  // camada de seleção já o desenha (as duas no mesmo lugar piscariam).
  const extrusionClauses: any[] = [...clauses, ['!=', ['get', 'fonte'], FONTE_PONTO]];
  if (selectedGeometryKey) extrusionClauses.push(['!=', ['get', 'geometry_key'], selectedGeometryKey]);
  map.setFilter(EXTRUSION_LAYER, ['all', ...extrusionClauses]);

  const count = overviewCountExpression(showPontos, tipos);
  // Quanto mais prédios, mais opaca a célula.
  const density: any = ['interpolate', ['linear'], count, 1, 0.28, 15, 0.5, 60, 0.75];
  for (const { level, enter, exit } of OVERVIEW_LEVELS) {
    map.setFilter(overviewId(level), ['>', count, 0]);
    map.setPaintProperty(overviewId(level), 'fill-color', overviewColorExpression(showPontos, tipos, coresPorTema(theme)));
    map.setPaintProperty(overviewId(level), 'fill-opacity', fadeRamp(enter, exit, density));
  }
}
