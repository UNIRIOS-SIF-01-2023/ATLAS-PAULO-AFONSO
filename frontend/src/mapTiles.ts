import type { LngLatBoundsLike } from 'maplibre-gl';
// Com extensão: os testes rodam este arquivo direto no Node.
import { TIPOS_ORDEM } from './constants.ts';

// Do mais perto ao mais longe: polígonos (14+), um ponto por prédio com a
// área real (12-14) e grades de densidade de /api/map/overview (até 12).
// Os zooms de tile seguem backend/main.py (DOT_MAX_ZOOM, POLYGON_MIN_ZOOM).
export const FOOTPRINT_MIN_ZOOM = 14;
// Abaixo daqui um prédio tem menos de 1 px e o tile já traz a cidade inteira.
export const DOT_MIN_ZOOM = 12;
// Acima disso o MapLibre amplia o tile 16 (overzoom), que já tem precisão
// de centímetros, em vez de pedir blocos novos a cada passo de zoom.
export const FOOTPRINT_MAX_ZOOM = 16;

// Em cada troca de nível, a camada nova chega à opacidade total antes de a
// anterior começar a sair: nunca há um meio-termo com as duas apagadas.
export const FADE_IN = 0.3;
export const FADE_OUT_START = 0.3;
export const FADE_OUT_END = 0.7;

export interface OverviewLevel {
  level: number; // índice em LEVEL_CELL_DEG (backend/overview.py)
  label: string;
  enter: number | null; // zoom em que a grade aparece
  exit: number; // zoom em que a próxima camada a substitui
}

// Cada nível dobra a resolução do anterior, um por nível de zoom, até os
// pontos dos prédios, que já formam uma textura fina, assumirem.
export const OVERVIEW_LEVELS: OverviewLevel[] = [
  { level: 0, label: '~175 m', enter: null, exit: 11 },
  { level: 1, label: '~88 m', enter: 11, exit: DOT_MIN_ZOOM },
];

// Metros por pixel no zoom 0 na latitude de Paulo Afonso (mundo de 512 px).
const METERS_PER_PIXEL_Z0 = (2 * Math.PI * 6378137 * Math.cos((9.4 * Math.PI) / 180)) / 512;
const DOT_MIN_RADIUS_PX = 1;

/**
 * Raio do ponto em px para um disco com a área do prédio. Só prédios com
 * menos de ~3 px viram ponto (backend/main.py, MIN_POLYGON_PX), então no
 * zoom 14 o disco tem o tamanho do polígono e afastando vira uma textura fina.
 */
export function dotRadiusExpression(): any {
  const radiusMeters = ['sqrt', ['/', ['max', ['get', 'area_m2'], 1], Math.PI]];
  const at = (zoom: number) => ['max', DOT_MIN_RADIUS_PX, ['*', radiusMeters, 2 ** zoom / METERS_PER_PIXEL_Z0]];
  return ['interpolate', ['exponential', 2], ['zoom'], DOT_MIN_ZOOM, at(DOT_MIN_ZOOM), FOOTPRINT_MIN_ZOOM + 1, at(FOOTPRINT_MIN_ZOOM + 1)];
}

export type Bounds = [number, number, number, number];

export interface MapMeta {
  version: string;
  bounds: Bounds | null;
  levels?: number;
  address_count?: number;
  building_count?: number;
  changed_count?: number;
  high_count?: number;
  medium_count?: number;
  low_count?: number;
  high_pct?: number;
  medium_pct?: number;
  low_pct?: number;
  coverage_pct?: number;
  anexo_count?: number;
  revise_face_count?: number;
  polygon_count?: number;
  polygon_file?: string;
  polygon_missing?: boolean;
  updated_at?: number | null;
  source?: string;
  missing?: boolean;
}

/** Opacidade por zoom: entra em [enter, enter+FADE_IN], sai depois de exit. */
export function fadeRamp(enter: number | null, exit: number | null, full: any): any {
  const stops: any[] = [];
  if (enter !== null) stops.push(enter, 0, enter + FADE_IN, full);
  if (exit !== null) stops.push(exit + FADE_OUT_START, full, exit + FADE_OUT_END, 0);
  return ['interpolate', ['linear'], ['zoom'], ...stops];
}

/** Camada que a legenda conta: a que domina a tela neste zoom. */
export function legendSource(zoom: number): number | 'footprints' | 'dots' {
  if (zoom >= FOOTPRINT_MIN_ZOOM + 0.5) return 'footprints';
  if (zoom >= DOT_MIN_ZOOM + 0.5) return 'dots';
  let current = OVERVIEW_LEVELS[0];
  for (const level of OVERVIEW_LEVELS) {
    if (level.enter !== null && zoom >= level.enter + 0.5) current = level;
  }
  return current.level;
}

/** A versão muda a cada importação, então o navegador nunca reusa tiles velhos. */
export function buildingTileUrl(apiUrl: string, version: string) {
  return `${apiUrl}/api/tiles/{z}/{x}/{y}.pbf?v=${encodeURIComponent(version)}`;
}

export function overviewUrl(apiUrl: string, version: string, level: number) {
  return `${apiUrl}/api/map/overview?level=${level}&v=${encodeURIComponent(version)}`;
}

/** Área navegável: os dados mais uma margem, para não cair no vazio. */
export function navigableBounds([west, south, east, north]: Bounds, margin = 0.25): LngLatBoundsLike {
  const dx = (east - west) * margin;
  const dy = (north - south) * margin;
  return [[west - dx, south - dy], [east + dx, north + dy]];
}

const ALL_TIPOS: readonly string[] = TIPOS_ORDEM;

function allTipos(tipos: readonly string[]) {
  return tipos.length === ALL_TIPOS.length;
}

/** Quantos prédios de um tipo a célula tem, com ou sem os endereços sem prédio. */
function cellTipoCount(tipo: string, showPontos: boolean): any {
  const b = ['coalesce', ['get', `b_${tipo}`], 0];
  return showPontos ? ['+', b, ['coalesce', ['get', `p_${tipo}`], 0]] : b;
}

/** Contagem que a célula representa para os tipos visíveis. */
export function overviewCountExpression(showPontos: boolean, tipos: readonly string[] = ALL_TIPOS): any {
  if (allTipos(tipos)) return showPontos ? ['+', ['get', 'n_b'], ['get', 'n_p']] : ['get', 'n_b'];
  return ['+', 0, ...tipos.map((tipo) => cellTipoCount(tipo, showPontos))];
}

/**
 * Cor da célula pelo uso predominante entre os tipos visíveis. Com todos
 * visíveis, usa o predominante já calculado pelo servidor; `desconhecido`
 * só pinta a célula quando nenhum uso conhecido aparece nela.
 */
export function overviewColorExpression(
  showPontos: boolean,
  tipos: readonly string[],
  colors: Record<string, string>,
): any {
  if (allTipos(tipos)) {
    return ['match', ['get', showPontos ? 'tipo' : 'tipo_b'],
      ...ALL_TIPOS.flatMap((tipo) => [tipo, colors[tipo]]), colors.desconhecido];
  }
  const known = tipos.filter((tipo) => tipo !== 'desconhecido');
  if (!known.length) return colors.desconhecido;
  const max = known.length === 1 ? cellTipoCount(known[0], showPontos) : ['max', ...known.map((t) => cellTipoCount(t, showPontos))];
  return ['let', 'maior', max, ['case',
    ...known.flatMap((tipo) => [
      ['all', ['>', ['var', 'maior'], 0], ['==', cellTipoCount(tipo, showPontos), ['var', 'maior']]],
      colors[tipo],
    ]),
    colors.desconhecido,
  ]];
}

type RenderedFeature = { id?: string | number; properties?: Record<string, unknown> | null };

/**
 * Legenda no zoom de footprints: cada prédio uma vez, mesmo partido entre
 * tiles ou desenhado como ponto e polígono na troca. O id é o mesmo nos dois.
 */
export function countFootprints(features: RenderedFeature[]) {
  const seen = new Set<string>();
  const counts: Record<string, number> = {};
  for (const feature of features) {
    const properties = feature.properties ?? {};
    const key = String(properties.id ?? properties.geometry_key ?? feature.id ?? '');
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const tipo = String(properties.tipo ?? 'desconhecido');
    counts[tipo] = (counts[tipo] ?? 0) + 1;
  }
  return { counts, total: seen.size };
}

/** Legenda na visão afastada: soma as células visíveis, só dos tipos visíveis. */
export function countOverviewCells(features: RenderedFeature[], showPontos: boolean, tipos: readonly string[] = ALL_TIPOS) {
  const seen = new Set<string>();
  const counts: Record<string, number> = {};
  let total = 0;
  const prefixes = showPontos ? ['b_', 'p_'] : ['b_'];
  for (const feature of features) {
    const properties = feature.properties ?? {};
    const key = String(properties.id ?? feature.id ?? '');
    if (!key || seen.has(key)) continue;
    seen.add(key);
    for (const tipo of tipos) {
      for (const prefix of prefixes) {
        const n = Number(properties[`${prefix}${tipo}`] ?? 0);
        if (n > 0) {
          counts[tipo] = (counts[tipo] ?? 0) + n;
          total += n;
        }
      }
    }
  }
  return { counts, total };
}
