export const TIPOS_ORDEM = [
  'residencial',
  'comercial',
  'servico',
  'religioso',
  'institucional',
  'hospedagem',
  'agropecuario',
  'desconhecido',
] as const;

export const TIPO_LABEL: Record<string, string> = {
  residencial: 'Residencial',
  comercial: 'Comercial',
  servico: 'Serviço',
  religioso: 'Religioso',
  institucional: 'Institucional',
  hospedagem: 'Hospedagem',
  agropecuario: 'Agropecuário',
  desconhecido: 'Não identificado',
};

export type Theme = 'dark' | 'light';

// Cores de sistema do modo escuro da Apple: saturadas o bastante para ler
// sobre o mapa escuro e distintas entre si.
export const TIPO_CORES_DARK: Record<string, string> = {
  residencial: '#0A84FF',
  comercial: '#FF453A',
  servico: '#FF9F0A',
  religioso: '#BF5AF2',
  institucional: '#64D2FF',
  hospedagem: '#30D158',
  agropecuario: '#FFD60A',
  desconhecido: '#8E8E93',
};

// Cores de sistema do modo claro da Apple: tons equilibrados e contrastantes
// para fundos claros e interface em vidro leitoso/cristalino.
export const TIPO_CORES_LIGHT: Record<string, string> = {
  residencial: '#007AFF',
  comercial: '#FF3B30',
  servico: '#FF9500',
  religioso: '#AF52DE',
  institucional: '#0098C8',
  hospedagem: '#34C759',
  agropecuario: '#D99B00',
  desconhecido: '#6C757D',
};

export const TIPO_CORES: Record<string, string> = TIPO_CORES_DARK;

export function coresPorTema(theme: Theme = 'dark'): Record<string, string> {
  return theme === 'light' ? TIPO_CORES_LIGHT : TIPO_CORES_DARK;
}

export function corDoTipo(tipo: unknown, theme: Theme = 'dark'): string {
  const cores = coresPorTema(theme);
  return (typeof tipo === 'string' && cores[tipo]) || cores.desconhecido;
}

export function rotuloDoTipo(tipo: unknown): string {
  return (typeof tipo === 'string' && TIPO_LABEL[tipo]) || TIPO_LABEL.desconhecido;
}

export const FONTE_PONTO = 'QG/CNEFE';

export const ALTURA_PAVIMENTO_M = 3;

// Sem número de pavimentos (a maioria), cada uso recebe uma altura típica:
// casas térreas baixas, templos e prédios públicos mais altos. Com todos os
// prédios em 3D, uma altura única viraria um paredão uniforme.
export const ALTURA_PADRAO_POR_TIPO: Record<string, number> = {
  residencial: 4,
  comercial: 5,
  servico: 5,
  religioso: 9,
  institucional: 7,
  hospedagem: 7,
  agropecuario: 5,
  desconhecido: 4,
};

/** Altura em metros e se ela é estimada (sem número de pavimentos). */
export function alturaDoPredio(tipo: unknown, pavimentos: unknown): { metros: number; estimada: boolean } {
  const n = Number(pavimentos);
  if (Number.isFinite(n) && n > 0) return { metros: n * ALTURA_PAVIMENTO_M, estimada: false };
  const padrao = (typeof tipo === 'string' && ALTURA_PADRAO_POR_TIPO[tipo]) || ALTURA_PADRAO_POR_TIPO.desconhecido;
  return { metros: padrao, estimada: true };
}
