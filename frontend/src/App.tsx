import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import type { Map as MapLibreMap, MapLayerMouseEvent } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { alturaDoPredio, FONTE_PONTO, TIPOS_ORDEM, type Theme } from './constants';
import {
  addDataLayers,
  addCnefeLayer,
  addRooftopLayer,
  addSelectionLayers,
  applyDataFilters,
  applyRooftopFilters,
  basemapUrl,
  CLICKABLE_LAYERS,
  DOT_LAYER,
  EXTRUSION_LAYER,
  FILL_LAYER,
  OVERVIEW_LAYERS,
  overviewId,
  removeDataLayers,
  removeCnefeLayer,
  removeRooftopLayer,
  ROOFTOP_CLICKABLE_LAYERS,
  ROOFTOP_POLYGON_LAYER,
  setAllBuildings3d,
  setCnefeData,
  setRooftopData,
  styleBasemap,
} from './mapLayers';
import { countFootprints, countOverviewCells, legendSource, OVERVIEW_LEVELS, type Bounds, type MapMeta } from './mapTiles';
import { BuildingCard, type BuildingProps } from './components/BuildingCard';
import { AuthBox, AuthScreen, type AuthUser } from './components/AuthBox';
import { authHeaders, guardarSessao, lerSessao, limparSessao } from './session';
import { Icon } from './components/Icon';
import { LayersPanel } from './components/LayersPanel';
import { MapControls } from './components/MapControls';
import { RooftopCard } from './components/RooftopCard';
import { RooftopControls } from './components/RooftopControls';
import { defaultRooftopOptions, type RooftopOptions, type RooftopSelection } from './rooftop';
import { SearchBox, type SearchPick } from './components/SearchBox';
const Report = lazy(() => import('./Report'));

type GeoJsonFeature = {
  type: 'Feature';
  id?: string | number;
  geometry: { type: string; coordinates: any };
  properties: Record<string, any>;
};

type Toast = { id: number; text: string; tone: 'info' | 'error' };

const API_URL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';
// Modo de conferência: ?camada=rooftop mostra só os pontos gerados localmente.
const ROOFTOP_ONLY = new URLSearchParams(window.location.search).get('camada') === 'rooftop';
const CENTRO = [-38.223898, -9.402317] as [number, number];
const ZOOM_INICIAL = 16.5;
// Acima de 60° o horizonte entra na tela e puxa a cidade inteira para um
// canto da imagem; o 3D do prédio selecionado já se lê bem nessa faixa.
const PITCH_3D = 45;
const PITCH_MAXIMO = 60;
const LEGEND_THROTTLE_MS = 150;
// Um clique na grade aproxima dois níveis: o bastante para ver o trecho
// no nível seguinte, sem pular direto para os prédios.
const CLICK_ZOOM_STEP = 2;
const ZOOM_PREDIO = 17.5;
const ZOOM_BAIRRO = 14.5;
const TOAST_MS = 3500;

function sameCounts(a: Record<string, number>, b: Record<string, number>) {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => a[key] === b[key]);
}

async function fetchJson(url: string, signal?: AbortSignal): Promise<any> {
  const response = await fetch(url, { signal, headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/** Centro da caixa envolvente de uma geometria GeoJSON. */
function geometryCenter(geometry: { coordinates: any } | undefined): [number, number] | null {
  if (!geometry) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const walk = (c: any) => {
    if (typeof c[0] === 'number') {
      minX = Math.min(minX, c[0]); maxX = Math.max(maxX, c[0]);
      minY = Math.min(minY, c[1]); maxY = Math.max(maxY, c[1]);
    } else c.forEach(walk);
  };
  walk(geometry.coordinates);
  return Number.isFinite(minX) ? [(minX + maxX) / 2, (minY + maxY) / 2] : null;
}

/** Espaço que o painel ocupa sobre o mapa, para centralizar no que sobra. */
function panelPadding(extra = 0) {
  const pad = { top: extra, right: extra, bottom: extra, left: extra };
  if (window.innerWidth > 720) pad.left += 396;
  else pad.bottom += Math.round(window.innerHeight * 0.42) + 8;
  return pad;
}

const PREF_3D = 'atlas:predios3d';
const PREF_THEME = 'atlas:theme';

function readTheme(): Theme {
  const param = new URLSearchParams(window.location.search).get('tema');
  if (param === 'claro' || param === 'bright' || param === 'light') return 'light';
  if (param === 'escuro' || param === 'dark') return 'dark';
  try {
    const saved = window.localStorage.getItem(PREF_THEME);
    if (saved === 'dark' || saved === 'light') return saved;
  } catch {
    // Modo privado ou armazenamento bloqueado.
  }
  return 'light'; // Padrão no tema claro líquido!
}

function saveTheme(theme: Theme) {
  try {
    window.localStorage.setItem(PREF_THEME, theme);
  } catch {
    // ignora
  }
}

// Preferência só deste navegador; sem armazenamento disponível, vale o padrão.
function readAll3d() {
  try {
    return window.localStorage.getItem(PREF_3D) !== 'selecionado';
  } catch {
    return true;
  }
}

function saveAll3d(all: boolean) {
  try {
    window.localStorage.setItem(PREF_3D, all ? 'todos' : 'selecionado');
  } catch {
    // Modo privado ou armazenamento bloqueado: só não lembra a escolha.
  }
}

function selectedFromUrl() {
  return new URLSearchParams(window.location.search).get('predio');
}

export default function App() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const labelLayerRef = useRef<string | undefined>(undefined);
  const searchRef = useRef<HTMLInputElement>(null);
  const userMarkerRef = useRef<maplibregl.Marker | null>(null);
  // Um link com ?predio= e sem câmera na URL precisa levar o mapa até ele.
  const flyToSelectionRef = useRef(Boolean(selectedFromUrl()) && !window.location.hash.includes('mapa='));

  const [theme, setTheme] = useState<Theme>(readTheme);
  const themeRef = useRef(theme);
  useEffect(() => {
    themeRef.current = theme;
  }, [theme]);
  const [styleRevision, setStyleRevision] = useState(0);

  const [selectedId, setSelectedId] = useState<string | null>(ROOFTOP_ONLY ? null : selectedFromUrl);
  const [selectedDetail, setSelectedDetail] = useState<{ id: string; feature: GeoJsonFeature } | null>(null);
  const [selectedPreview, setSelectedPreview] = useState<{ id: string; feature: GeoJsonFeature } | null>(null);
  const [showPontos, setShowPontos] = useState(true);
  const [soDoSelecionado, setSoDoSelecionado] = useState(false);
  const [hiddenTipos, setHiddenTipos] = useState<ReadonlySet<string>>(new Set());
  const [showReport, setShowReport] = useState(false);
  const [showRooftop, setShowRooftop] = useState(ROOFTOP_ONLY);
  const [rooftopOptions, setRooftopOptions] = useState<RooftopOptions>(defaultRooftopOptions);
  // Comparação no modo debug: endereços ORIGINAIS do CNEFE por cima do rooftop.
  const [showCnefe, setShowCnefe] = useState(false);
  const [rooftopMeta, setRooftopMeta] = useState<MapMeta | null>(null);
  const [rooftopSelection, setRooftopSelection] = useState<RooftopSelection | null>(null);
  const rooftopOptionsRef = useRef(rooftopOptions);
  useEffect(() => {
    rooftopOptionsRef.current = rooftopOptions;
  }, [rooftopOptions]);
  // O mapa cujo estilo terminou de carregar / que já tem as camadas de dados.
  // Guardar a instância, e não um booleano, evita usar um mapa novo (recriado
  // no hot reload) antes de o estilo dele carregar.
  const [readyMap, setReadyMap] = useState<MapLibreMap | null>(null);
  const [meta, setMeta] = useState<MapMeta | null>(null);
  const [dataMap, setDataMap] = useState<MapLibreMap | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [gridLabel, setGridLabel] = useState<string | null>(null);
  const [bearing, setBearing] = useState(0);
  const [is3d, setIs3d] = useState(true);
  const [all3d, setAll3d] = useState(readAll3d);
  const [loading, setLoading] = useState(true);
  const [locating, setLocating] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Conta com JWT: sem token válido o app nem monta o mapa.
  const [user, setUser] = useState<AuthUser | null>(() => lerSessao()?.user ?? null);
  const [sessaoPronta, setSessaoPronta] = useState(false);
  const [showAuth, setShowAuth] = useState(false);

  // Revalida o token guardado no servidor a cada abertura.
  useEffect(() => {
    const sessao = lerSessao();
    if (!sessao) {
      setSessaoPronta(true);
      return;
    }
    const controller = new AbortController();
    fetch(`${API_URL}/api/auth/eu`, {
      headers: { Authorization: `Bearer ${sessao.token}` },
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((u) => setUser(u))
      .catch(() => {
        limparSessao();
        setUser(null);
      })
      .finally(() => setSessaoPronta(true));
    return () => controller.abort();
  }, []);

  const tipos = useMemo(() => TIPOS_ORDEM.filter((tipo) => !hiddenTipos.has(tipo)), [hiddenTipos]);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === 'light' ? 'dark' : 'light'));
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const metaTheme = document.querySelector('meta[name="theme-color"]');
    if (metaTheme) metaTheme.setAttribute('content', theme === 'light' ? '#eef2f6' : '#0b0f17');
    saveTheme(theme);
  }, [theme]);

  const notify = useCallback((text: string, tone: Toast['tone'] = 'info') => {
    const id = Date.now() + Math.random();
    setToasts((list) => [...list.filter((t) => t.text !== text), { id, text, tone }]);
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), tone === 'error' ? TOAST_MS * 2 : TOAST_MS);
  }, []);

  const select = useCallback((id: string | null, preview: GeoJsonFeature | null = null) => {
    setSelectedPreview(id && preview ? { id, feature: preview } : null);
    setSelectedId(id);
    if (id) setSheetOpen(true);
  }, []);

  // --- Mapa --------------------------------------------------------------

  useEffect(() => {
    // O div do mapa só existe na vista logada; sem `user` nas
    // dependências o efeito rodava uma vez na tela de login e nunca
    // mais (foi assim que o mapa parou de montar após o login).
    if (!user || !mapContainer.current) return;
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: basemapUrl(themeRef.current),
      center: CENTRO,
      zoom: ZOOM_INICIAL,
      pitch: PITCH_3D,
      maxPitch: PITCH_MAXIMO,
      minZoom: 9.5,
      renderWorldCopies: false,
      // Câmera no endereço (#mapa=zoom/lat/lng/rumo/inclinação): links compartilháveis.
      hash: 'mapa',
      attributionControl: { compact: true },
      // Tiles e grades da nossa API exigem o Bearer do login.
      transformRequest: (url) =>
        url.startsWith(API_URL) ? { url, headers: { ...authHeaders() } } : { url },
    });
    mapRef.current = map;
    // No modo debug a conferência sempre começa no centro da cidade, bem
    // próxima e inclinada: aí os primeiros polígonos da área carregam na
    // hora. O hash compartilhado não vale aqui (era ele que abria de cima).
    if (ROOFTOP_ONLY) map.jumpTo({ center: CENTRO, zoom: ZOOM_INICIAL, pitch: PITCH_3D, bearing: 0 });
    // Só em desenvolvimento: inspecionar o mapa pelo console do navegador.
    if (import.meta.env.DEV) (window as unknown as { __atlasMap: MapLibreMap }).__atlasMap = map;
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 96 }), 'bottom-right');

    const onClick = (event: MapLayerMouseEvent) => {
      const rooftopLayers = ROOFTOP_CLICKABLE_LAYERS.filter((layer) => map.getLayer(layer));
      if (rooftopLayers.length) {
        const rooftop = map.queryRenderedFeatures(event.point, { layers: rooftopLayers })[0];
        if (rooftop) {
          const properties = (rooftop.properties ?? {}) as Record<string, unknown>;
          const kind = rooftop.layer.id === ROOFTOP_POLYGON_LAYER ? 'polygon' : 'point';
          const id = String(kind === 'polygon' ? properties.bld_id : properties.addr_key);
          setRooftopSelection({ kind, id, properties });
          select(null);
          return;
        }
      }
      setRooftopSelection(null);
      if (typeof legendSource(map.getZoom()) === 'number') {
        const cell = map.queryRenderedFeatures(event.point, { layers: OVERVIEW_LAYERS.filter((l) => map.getLayer(l)) })[0];
        if (cell) {
          map.easeTo({ center: event.lngLat, zoom: map.getZoom() + CLICK_ZOOM_STEP });
          return;
        }
      }
      const features = map.queryRenderedFeatures(event.point, { layers: CLICKABLE_LAYERS.filter((l) => map.getLayer(l)) });
      const clicked = features.find((feature) =>
        (feature.geometry.type === 'Polygon' || feature.geometry.type === 'MultiPolygon')
          && feature.properties?.fonte !== FONTE_PONTO)
        ?? features[0];
      const id = clicked?.properties?.id;
      select(id == null ? null : String(id), clicked
        ? { type: 'Feature', geometry: clicked.geometry as any, properties: clicked.properties ?? {} }
        : null);
    };
    const onStyleLoad = () => {
      labelLayerRef.current = styleBasemap(map, themeRef.current);
      addSelectionLayers(map, themeRef.current);
      setReadyMap(map);
      setStyleRevision((rev) => rev + 1);
    };
    const onError = (event: maplibregl.ErrorEvent) => {
      // Tiles cancelados ao mover o mapa não são erro para o usuário.
      if (/abort/i.test(event.error?.message ?? '')) return;
      const sourceId = (event as maplibregl.ErrorEvent & { sourceId?: string }).sourceId;
      notify(sourceId?.startsWith('overview') || sourceId === 'buildings'
        ? 'Não foi possível carregar parte dos prédios. Verifique o servidor.'
        : 'Falha ao carregar o mapa.', 'error');
    };
    const onCamera = () => {
      setBearing(map.getBearing());
      setIs3d(map.getPitch() > 5);
    };
    const onDataLoading = (event: maplibregl.MapSourceDataEvent) => {
      if (event.sourceId === 'buildings' || event.sourceId?.startsWith('overview')) setLoading(true);
    };
    const onIdle = () => setLoading(false);

    map.on('click', onClick);
    // `load` espera todos os tiles visíveis; o estilo já está pronto antes.
    map.on('style.load', onStyleLoad);
    map.on('error', onError);
    map.on('rotate', onCamera);
    map.on('pitch', onCamera);
    map.on('dataloading', onDataLoading as any);
    map.on('idle', onIdle);

    return () => {
      setReadyMap(null);
      map.remove();
      mapRef.current = null;
    };
  }, [notify, select, user]);

  // Troca de tema no mapa existente
  const prevThemeRef = useRef(theme);
  useEffect(() => {
    if (prevThemeRef.current === theme) return;
    prevThemeRef.current = theme;
    const map = mapRef.current;
    if (!map) return;
    setDataMap(null);
    map.setStyle(basemapUrl(theme));
  }, [theme]);

  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    fetchJson(`${API_URL}/api/map/meta`, controller.signal)
      .then((data: MapMeta) => setMeta(data))
      .catch(() => {
        if (!controller.signal.aborted) notify(`Servidor de dados fora do ar (${API_URL}).`, 'error');
      });
    return () => controller.abort();
  }, [notify, user]);

  useEffect(() => {
    if (!user || (!ROOFTOP_ONLY && !showRooftop)) return;
    const controller = new AbortController();
    fetchJson(`${API_URL}/api/rooftop/vila-nobre/meta`, controller.signal)
      .then((data: MapMeta) => setRooftopMeta(data))
      .catch(() => {
        if (!controller.signal.aborted) notify('Não foi possível carregar os metadados do rooftop.', 'error');
      });
    return () => controller.abort();
  }, [notify, showRooftop, user]);

  useEffect(() => {
    const map = readyMap;
    if (!map || map !== mapRef.current || !meta) return;
    // Os prédios do Atlas (grades, pontos, polígonos e o 3D) ficam por baixo
    // também no modo debug: é o contexto que dá escala à conferência.
    addDataLayers(map, meta, API_URL, labelLayerRef.current, theme);
    setDataMap(map);
    // Cursor de mão sobre o que dá para clicar.
    const pointer = () => { map.getCanvas().style.cursor = 'pointer'; };
    const reset = () => { map.getCanvas().style.cursor = ''; };
    for (const layer of [EXTRUSION_LAYER, FILL_LAYER, DOT_LAYER, ...OVERVIEW_LAYERS]) {
      map.on('mouseenter', layer, pointer);
      map.on('mouseleave', layer, reset);
    }
    return () => {
      setDataMap(null);
      for (const layer of [EXTRUSION_LAYER, FILL_LAYER, DOT_LAYER, ...OVERVIEW_LAYERS]) {
        map.off('mouseenter', layer, pointer);
        map.off('mouseleave', layer, reset);
      }
      if (mapRef.current === map) removeDataLayers(map);
    };
  }, [readyMap, meta, styleRevision, theme]);

  // Camada rooftop opcional sobre os dados do Atlas. No modo debug ela é o
  // assunto da tela e carrega por viewport: só a área visível a cada parada
  // (o arquivo inteiro são 38MB, e a cidade inteira não cabe na tela).
  useEffect(() => {
    const map = dataMap;
    if (!map || map !== mapRef.current || (!ROOFTOP_ONLY && !showRooftop)) return;
    const dinamico = ROOFTOP_ONLY;
    addRooftopLayer(map, API_URL, labelLayerRef.current, theme, { dynamic: dinamico });
    applyRooftopFilters(map, rooftopOptionsRef.current, theme);
    const pointer = () => { map.getCanvas().style.cursor = 'pointer'; };
    const reset = () => { map.getCanvas().style.cursor = ''; };
    for (const layer of ROOFTOP_CLICKABLE_LAYERS) {
      map.on('mouseenter', layer, pointer);
      map.on('mouseleave', layer, reset);
    }

    let timer: number | undefined;
    let pedido = 0;
    let ultimaCaixa: Bounds | null = null;
    let ultimoComPontos = false;
    // A vista (com margem) já está dentro da última caixa pedida?
    const cobre = (vista: Bounds, caixa: Bounds) =>
      vista[0] >= caixa[0] && vista[1] >= caixa[1] && vista[2] <= caixa[2] && vista[3] <= caixa[3];
    const carregarVisao = () => {
      const b = map.getBounds();
      const sw = b.getSouthWest();
      const ne = b.getNorthEast();
      const dx = (ne.lng - sw.lng) * 0.5;
      const dy = (ne.lat - sw.lat) * 0.5;
      const vista: Bounds = [sw.lng - dx, sw.lat - dy, ne.lng + dx, ne.lat + dy];
      // Os pontos só desenham a partir do zoom 14; não adianta pedi-los antes.
      const comPontos = map.getZoom() >= 13.5;
      if (ultimaCaixa && cobre(vista, ultimaCaixa) && (!comPontos || ultimoComPontos)) return;
      const meu = ++pedido;
      Promise.all([
        comPontos ? fetchJson(`${API_URL}/api/rooftop/vila-nobre?bbox=${vista.join(',')}`) : null,
        fetchJson(`${API_URL}/api/rooftop/vila-nobre/poligonos?bbox=${vista.join(',')}`),
      ]).then(([pontos, poligonos]) => {
        if (meu !== pedido || mapRef.current !== map) return;
        ultimaCaixa = vista;
        ultimoComPontos = comPontos;
        setRooftopData(map, pontos, poligonos);
      }).catch(() => {
        if (meu === pedido) notify('Não foi possível carregar a área do rooftop.', 'error');
      });
    };
    // Paradas rápidas de câmera não disparam pedidos em cascata.
    const agendar = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = undefined;
        carregarVisao();
      }, 250);
    };
    if (dinamico) {
      carregarVisao(); // o primeiro quadro já traz os prédios da área central
      map.on('moveend', agendar);
    }
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      pedido += 1; // respostas em voo não escrevem no mapa novo
      map.off('moveend', agendar);
      for (const layer of ROOFTOP_CLICKABLE_LAYERS) {
        map.off('mouseenter', layer, pointer);
        map.off('mouseleave', layer, reset);
      }
      if (mapRef.current === map) removeRooftopLayer(map);
    };
  }, [dataMap, showRooftop, styleRevision, theme, notify]);

  // Camada comparativa do CNEFE original no modo debug: ciclo próprio de
  // busca por viewport, para o toggle não derrubar os dados do rooftop.
  useEffect(() => {
    const map = dataMap;
    if (!map || map !== mapRef.current || !ROOFTOP_ONLY || !showCnefe) return;
    addCnefeLayer(map, undefined, theme);
    let timer: number | undefined;
    let pedido = 0;
    let ultimaCaixa: Bounds | null = null;
    const cobre = (vista: Bounds, caixa: Bounds) =>
      vista[0] >= caixa[0] && vista[1] >= caixa[1] && vista[2] <= caixa[2] && vista[3] <= caixa[3];
    const carregarVisao = () => {
      const b = map.getBounds();
      const sw = b.getSouthWest();
      const ne = b.getNorthEast();
      const dx = (ne.lng - sw.lng) * 0.5;
      const dy = (ne.lat - sw.lat) * 0.5;
      const vista: Bounds = [sw.lng - dx, sw.lat - dy, ne.lng + dx, ne.lat + dy];
      if (ultimaCaixa && cobre(vista, ultimaCaixa)) return;
      const meu = ++pedido;
      fetchJson(`${API_URL}/api/rooftop/cnefe?bbox=${vista.join(',')}`)
        .then((pontos) => {
          if (meu !== pedido || mapRef.current !== map) return;
          ultimaCaixa = vista;
          setCnefeData(map, pontos);
        })
        .catch(() => {
          if (meu === pedido) notify('Não foi possível carregar o CNEFE original.', 'error');
        });
    };
    const agendar = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = undefined;
        carregarVisao();
      }, 250);
    };
    carregarVisao();
    map.on('moveend', agendar);
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      pedido += 1;
      map.off('moveend', agendar);
      if (mapRef.current === map) removeCnefeLayer(map);
    };
  }, [dataMap, showCnefe, styleRevision, theme, notify]);

  // Filtros e visibilidade mudam sem recarregar o GeoJSON.
  useEffect(() => {
    const map = dataMap ?? readyMap;
    if (!map || map !== mapRef.current) return;
    applyRooftopFilters(map, rooftopOptions, theme);
  }, [dataMap, readyMap, rooftopOptions, styleRevision, theme]);

  // --- Seleção -----------------------------------------------------------

  useEffect(() => {
    if (!selectedId || !user) return;
    const controller = new AbortController();
    fetchJson(`${API_URL}/api/buildings/${encodeURIComponent(selectedId)}`, controller.signal)
      .then((feature: GeoJsonFeature) => setSelectedDetail({ id: selectedId, feature }))
      .catch(() => {
        if (controller.signal.aborted) return;
        notify('Não foi possível carregar esta edificação.', 'error');
        setSelectedId(null);
      });
    return () => controller.abort();
  }, [selectedId, notify, user]);

  // O prédio selecionado fica no endereço (?predio=), para compartilhar.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (selectedId) url.searchParams.set('predio', selectedId);
    else url.searchParams.delete('predio');
    window.history.replaceState(window.history.state, '', url);
  }, [selectedId]);

  const selectedFeature = selectedDetail?.id === selectedId ? selectedDetail.feature : null;
  const selectedProps = selectedFeature?.properties as BuildingProps | undefined;
  const selected3dFeature = selectedFeature ?? (selectedPreview?.id === selectedId ? selectedPreview.feature : null);
  const selectedGeometryKey: string = selected3dFeature?.properties?.geometry_key ?? '';
  const selectedCenter = useMemo(() => geometryCenter(selected3dFeature?.geometry), [selected3dFeature]);
  const { metros: height, estimada: heightEstimated } = alturaDoPredio(
    selected3dFeature?.properties?.tipo, selected3dFeature?.properties?.num_pavimentos);

  useEffect(() => {
    const map = readyMap;
    if (!map || map !== mapRef.current) return;
    const source = map.getSource('selected') as maplibregl.GeoJSONSource | undefined;
    const isBuilding = selected3dFeature
      && ['Polygon', 'MultiPolygon'].includes(selected3dFeature.geometry.type)
      && selected3dFeature.properties?.fonte !== FONTE_PONTO;
    source?.setData({
      type: 'FeatureCollection',
      features: isBuilding
        ? [{ ...selected3dFeature, properties: { ...selected3dFeature.properties, altura_3d_m: height } } as any]
        : [],
    });
  }, [readyMap, selected3dFeature, height, styleRevision]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedFeature || !flyToSelectionRef.current) return;
    flyToSelectionRef.current = false;
    const center = geometryCenter(selectedFeature.geometry);
    if (center) map.jumpTo({ center, zoom: ZOOM_PREDIO, padding: panelPadding() });
  }, [selectedFeature]);

  // --- Filtros e legenda -------------------------------------------------

  useEffect(() => {
    const map = dataMap;
    if (!map || map !== mapRef.current) return;
    applyDataFilters(map, { showPontos, soDoSelecionado, selectedGeometryKey, tipos }, theme);
  }, [dataMap, showPontos, soDoSelecionado, selectedGeometryKey, tipos, theme, styleRevision]);

  useEffect(() => {
    const map = dataMap;
    if (!map || map !== mapRef.current) return;
    setAllBuildings3d(map, all3d);
    saveAll3d(all3d);
  }, [dataMap, all3d, styleRevision]);

  useEffect(() => {
    const map = dataMap;
    if (!map || map !== mapRef.current || ROOFTOP_ONLY) return;
    const updateVisibleCounts = () => {
      const source = legendSource(map.getZoom());
      const next = typeof source === 'number'
        ? countOverviewCells(map.queryRenderedFeatures({ layers: [overviewId(source)] }), showPontos, tipos)
        : countFootprints(map.queryRenderedFeatures({ layers: source === 'dots' ? [DOT_LAYER, FILL_LAYER] : [FILL_LAYER] }));
      setGridLabel(typeof source === 'number' ? OVERVIEW_LEVELS[source].label : null);
      setTotal(next.total);
      setCounts((previous) => (sameCounts(previous, next.counts) ? previous : next.counts));
    };
    // Contar varre todas as feições desenhadas; com dezenas de tiles chegando
    // a cada movimento, uma contagem por evento travava a navegação.
    let timer: number | undefined;
    const scheduleCount = () => {
      if (timer === undefined) {
        timer = window.setTimeout(() => {
          timer = undefined;
          updateVisibleCounts();
        }, LEGEND_THROTTLE_MS);
      }
    };
    const onSourceData = (event: maplibregl.MapSourceDataEvent) => {
      if ((event.sourceId === 'buildings' || event.sourceId.startsWith('overview-')) && event.isSourceLoaded) scheduleCount();
    };
    map.on('idle', scheduleCount);
    map.on('moveend', scheduleCount);
    map.on('sourcedata', onSourceData);
    scheduleCount();
    return () => {
      window.clearTimeout(timer);
      map.off('idle', scheduleCount);
      map.off('moveend', scheduleCount);
      map.off('sourcedata', onSourceData);
    };
  }, [dataMap, showPontos, soDoSelecionado, selectedGeometryKey, tipos]);

  // --- Ações -------------------------------------------------------------

  const closeSelection = useCallback(() => select(null), [select]);

  const onSearchPick = useCallback((choice: SearchPick) => {
    const map = mapRef.current;
    const { lng, lat } = choice.item;
    if (choice.kind === 'building') {
      select(choice.item.id);
      map?.flyTo({ center: [lng, lat], zoom: Math.max(map.getZoom(), ZOOM_PREDIO), padding: panelPadding(), essential: true });
    } else {
      map?.flyTo({ center: [lng, lat], zoom: ZOOM_BAIRRO, padding: panelPadding(), essential: true });
    }
    searchRef.current?.blur();
  }, [select]);

  const recenter = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    if (meta?.bounds) {
      const [w, s, e, n] = meta.bounds;
      map.fitBounds([[w, s], [e, n]], { padding: panelPadding(48), pitch: 0, bearing: 0 });
    } else map.flyTo({ center: CENTRO, zoom: 12 });
  }, [meta]);

  const locate = useCallback(() => {
    const map = mapRef.current;
    if (!map || !navigator.geolocation) {
      notify('Localização indisponível neste navegador.', 'error');
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setLocating(false);
        const here: [number, number] = [coords.longitude, coords.latitude];
        if (!userMarkerRef.current) {
          const dot = document.createElement('div');
          dot.className = 'user-dot';
          userMarkerRef.current = new maplibregl.Marker({ element: dot });
        }
        userMarkerRef.current.setLngLat(here).addTo(map);
        const [w, s, e, n] = meta?.bounds ?? [-180, -90, 180, 90];
        if (here[0] < w || here[0] > e || here[1] < s || here[1] > n) {
          notify('Você está fora da área do Atlas (Paulo Afonso).');
          return;
        }
        map.flyTo({ center: here, zoom: 17, padding: panelPadding(), essential: true });
      },
      () => {
        setLocating(false);
        notify('Não foi possível obter sua localização.', 'error');
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }, [meta, notify]);

  const copy = useCallback((text: string, label: string) => {
    navigator.clipboard?.writeText(text).then(() => notify(label), () => notify('Não foi possível copiar.', 'error'));
  }, [notify]);

  const toggleRooftop = useCallback((value: boolean) => {
    setShowRooftop(value);
    if (!value) setRooftopSelection(null);
  }, []);

  const toggleTipo = useCallback((tipo: string) => {
    setHiddenTipos((hidden) => {
      const next = new Set(hidden);
      if (next.has(tipo)) next.delete(tipo);
      else next.add(tipo);
      // Esconder tudo deixaria o mapa vazio sem explicação; volta a mostrar todos.
      return next.size === TIPOS_ORDEM.length ? new Set() : next;
    });
  }, []);

  const onlyTipo = useCallback((tipo: string) => {
    setHiddenTipos(new Set(TIPOS_ORDEM.filter((t) => t !== tipo)));
  }, []);

  // Atalhos: "/" busca, Esc fecha o que estiver aberto.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if (event.key === '/' && !typing) {
        event.preventDefault();
        setSheetOpen(true);
        searchRef.current?.focus();
      } else if (event.key === 'Escape' && !typing) {
        if (showReport) setShowReport(false);
        else if (selectedId) closeSelection();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showReport, selectedId, closeSelection]);

  // Sem JWT válido, nem o mapa monta: a tela de login não tem saída.
  if (!sessaoPronta) {
    return (
      <div className="auth-screen">
        <div className="auth-card glass">
          <p className="muted">Verificando sessão…</p>
        </div>
      </div>
    );
  }
  if (!user) {
    return (
      <AuthScreen
        apiUrl={API_URL}
        theme={theme}
        onToggleTheme={toggleTheme}
        onAuth={(token, u) => {
          guardarSessao({ token, user: u });
          if (!lerSessao()) {
            notify('Este navegador bloqueou o armazenamento: a sessão não pode ser mantida.', 'error');
            return;
          }
          setUser(u);
        }}
      />
    );
  }

  return (
    <div className="app">
      <div ref={mapContainer} className="map" />

      <aside className={`sidebar glass${sheetOpen ? ' open' : ''}`} aria-label="Painel do mapa">
        <button
          type="button"
          className="sheet-handle"
          aria-label={sheetOpen ? 'Recolher painel' : 'Expandir painel'}
          aria-expanded={sheetOpen}
          onClick={() => setSheetOpen((open) => !open)}
        >
          <span />
        </button>
        <header className="brand">
          <span className="brand-mark"><Icon name="building" size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1>Atlas Urbano</h1>
            <p>{ROOFTOP_ONLY ? 'Vila Nobre · CNEFE rooftop (local)' : 'Paulo Afonso · BA'}</p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={user ? `Conta de ${user.nome}` : 'Entrar ou criar conta'}
            title={user ? `Entrou como ${user.nome}` : 'Entrar ou criar conta'}
            onClick={() => setShowAuth((open) => !open)}
          >
            <Icon name="user" size={16} />
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label={theme === 'light' ? 'Mudar para tema escuro' : 'Mudar para tema claro'}
            title={theme === 'light' ? 'Mudar para tema escuro' : 'Mudar para tema claro'}
            onClick={toggleTheme}
          >
            <Icon name={theme === 'light' ? 'moon' : 'sun'} size={16} />
          </button>
        </header>
        {showAuth && (
          <AuthBox
            apiUrl={API_URL}
            user={user}
            onAuth={(token, u) => {
              guardarSessao({ token, user: u });
              if (!lerSessao()) {
                notify('Este navegador bloqueou o armazenamento: a sessão não pode ser mantida.', 'error');
                return;
              }
              setUser(u);
              setShowAuth(false);
            }}
            onLogout={() => {
              limparSessao();
              setUser(null);
            }}
          />
        )}
        {!ROOFTOP_ONLY && <SearchBox ref={searchRef} apiUrl={API_URL} onPick={onSearchPick} onFocusChange={(focused) => focused && setSheetOpen(true)} />}
        <div className="sidebar-body">
          {rooftopSelection ? (
            <RooftopCard
              selection={rooftopSelection}
              theme={theme}
              onClose={() => setRooftopSelection(null)}
              onCopy={copy}
            />
          ) : ROOFTOP_ONLY ? (
            <div className="layers">
              <div className="stat-hero">
                <span className="stat-value">{rooftopMeta?.address_count ?? 0}</span>
                <span className="stat-caption">pontos rooftop gerados localmente</span>
              </div>
              <RooftopControls
                value={rooftopOptions}
                onChange={setRooftopOptions}
                meta={rooftopMeta}
                theme={theme}
              />
              <label className="switch-row" style={{ marginTop: 12 }}>
                <span>
                  <strong>Cruzar com CNEFE original</strong>
                  <span className="muted">Círculos vazados: onde o algoritmo acerta o ponto bruto do IBGE</span>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  className="switch"
                  checked={showCnefe}
                  onChange={(event) => setShowCnefe(event.target.checked)}
                />
              </label>
              <p className="hint">Camada de conferência do algoritmo CNEFE rooftop sobre os prédios do Atlas, que carregam por área visível. Clique num ponto ou polígono para ver o detalhe.</p>
              <p className="muted small">Aproxime o mapa para visualizar os rótulos dos endereços.</p>
            </div>
          ) : selectedId ? (
            <BuildingCard
              building={selectedProps ?? null}
              previewTipo={selected3dFeature?.properties?.tipo}
              center={selectedCenter}
              heightM={height}
              heightEstimated={heightEstimated}
              theme={theme}
              onClose={closeSelection}
              onCenter={() => selectedCenter && mapRef.current?.flyTo({ center: selectedCenter, zoom: Math.max(mapRef.current.getZoom(), ZOOM_PREDIO), padding: panelPadding(), essential: true })}
              onSelectAddress={(id) => select(id, selected3dFeature)}
              onCopy={copy}
            />
          ) : (
            <LayersPanel
              counts={counts}
              total={total}
              gridLabel={gridLabel}
              hiddenTipos={hiddenTipos}
              theme={theme}
              onToggleTipo={toggleTipo}
              onOnlyTipo={onlyTipo}
              onShowAllTipos={() => setHiddenTipos(new Set())}
              showPontos={showPontos}
              onShowPontos={setShowPontos}
              soDoSelecionado={soDoSelecionado}
              onSoDoSelecionado={setSoDoSelecionado}
              hasSelection={Boolean(selectedId)}
              showRooftop={showRooftop}
              onShowRooftop={toggleRooftop}
              rooftopMeta={rooftopMeta}
              rooftopOptions={rooftopOptions}
              onRooftopOptions={setRooftopOptions}
            />
          )}
        </div>
      </aside>

      <MapControls
        bearing={bearing}
        is3d={is3d}
        all3d={all3d}
        onToggleAll3d={() => {
          setAll3d((all) => !all);
          // Ver prédios em 3D de cima não mostra nada; inclina a câmera.
          if (!all3d && !is3d) mapRef.current?.easeTo({ pitch: PITCH_3D });
        }}
        locating={locating}
        onZoomIn={() => mapRef.current?.zoomIn()}
        onZoomOut={() => mapRef.current?.zoomOut()}
        onResetNorth={() => mapRef.current?.easeTo({ bearing: 0 })}
        onToggle3d={() => mapRef.current?.easeTo({ pitch: is3d ? 0 : PITCH_3D })}
        onRecenter={recenter}
        onLocate={locate}
        onReport={() => setShowReport(true)}
      />

      <div className={`loading-pill glass${loading ? ' show' : ''}`} role="status" aria-live="polite">
        <span className="spinner" />
        {loading ? 'Carregando prédios…' : ''}
      </div>

      <div className="toasts" aria-live="assertive">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast glass ${toast.tone}`} role={toast.tone === 'error' ? 'alert' : 'status'}>
            {toast.text}
          </div>
        ))}
      </div>

      {showReport && (
        <Suspense fallback={<div className="report-backdrop"><div className="report glass"><p className="muted pad">Carregando relatório…</p></div></div>}>
          <Report apiUrl={API_URL} theme={theme} onClose={() => setShowReport(false)} />
        </Suspense>
      )}
    </div>
  );
}
