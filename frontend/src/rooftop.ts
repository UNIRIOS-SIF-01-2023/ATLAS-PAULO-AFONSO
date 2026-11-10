import type { Theme } from './constants';

export type RooftopConfidence = 'alta' | 'media' | 'baixa' | 'nenhuma';
export type RooftopViewMode = 'final' | 'debug';

export const ROOFTOP_CONFIDENCES: RooftopConfidence[] = ['alta', 'media', 'baixa', 'nenhuma'];

export interface RooftopOptions {
  viewMode: RooftopViewMode;
  showPoints: boolean;
  showPolygons: boolean;
  showRawCnefe: boolean;
  confiancas: ReadonlySet<RooftopConfidence>;
  onlyChanged: boolean;
}

export interface RooftopSelection {
  kind: 'point' | 'polygon';
  id: string;
  properties: Record<string, unknown>;
}

export function defaultRooftopOptions(): RooftopOptions {
  return {
    viewMode: 'debug',
    showPoints: true,
    showPolygons: true,
    showRawCnefe: false,
    confiancas: new Set(ROOFTOP_CONFIDENCES),
    onlyChanged: false,
  };
}

export function corDaConfianca(confianca: unknown, theme: Theme = 'dark'): string {
  const light = theme === 'light';
  switch (confianca) {
    case 'alta': return light ? '#16a34a' : '#22c55e'; // verde bem vivo
    case 'media': return light ? '#ea580c' : '#f97316'; // bem laranja
    case 'baixa': return light ? '#dc2626' : '#ef4444'; // bem vermelho
    default: return light ? '#475569' : '#94a3b8';
  }
}

export function rotuloDaConfianca(confianca: unknown): string {
  switch (confianca) {
    case 'alta': return 'Alta';
    case 'media': return 'Média';
    case 'baixa': return 'Baixa';
    default: return 'Sem prédio';
  }
}
