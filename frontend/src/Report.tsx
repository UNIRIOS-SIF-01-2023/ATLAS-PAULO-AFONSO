import { useEffect, useMemo, useState } from 'react';
import { corDoTipo, rotuloDoTipo, TIPOS_ORDEM, type Theme } from './constants';
import { formatNumber, titleCase } from './format';
import { authHeaders } from './session';
import { Icon } from './components/Icon';

type Stats = { total: number; by_tipo: Record<string, number>; by_bairro: Record<string, number> };

function normalizarBairro(nome: string): string {
  return nome.replace(/\s+\d+\s*$/, '').trim() || nome;
}

async function fetchStats(apiUrl: string, params: URLSearchParams, signal?: AbortSignal): Promise<Stats> {
  const response = await fetch(`${apiUrl}/api/stats?${params}`, { signal, headers: { ...authHeaders() } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function BarList({ rows, color }: { rows: { key: string; label: string; n: number; color?: string }[]; color?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <ul className="bar-list">
      {rows.map((row) => (
        <li key={row.key}>
          <span className="bar-label">{row.label}</span>
          <span className="bar-value">{formatNumber(row.n)}</span>
          <span className="bar-track" aria-hidden="true">
            <span style={{ width: `${(row.n / max) * 100}%`, background: row.color ?? color }} />
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function Report({ apiUrl, theme = 'dark', onClose }: { apiUrl: string; theme?: Theme; onClose: () => void }) {
  const [filtroTipo, setFiltroTipo] = useState('todos');
  const [filtroBairros, setFiltroBairros] = useState<string[]>([]);
  const [buscaBairro, setBuscaBairro] = useState('');
  const [base, setBase] = useState<Stats | null>(null);
  const [stats, setStats] = useState<{ key: string; value: Stats } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const bairros = useMemo(() => {
    const counts = new Map<string, number>();
    for (const [raw, count] of Object.entries(base?.by_bairro ?? {})) {
      const label = normalizarBairro(raw);
      counts.set(label, (counts.get(label) ?? 0) + count);
    }
    return [...counts.keys()].sort((a, b) => a.localeCompare(b, 'pt-BR'));
  }, [base]);

  const bairrosFiltrados = useMemo(() => {
    const q = buscaBairro.trim().toLocaleLowerCase('pt-BR');
    return q ? bairros.filter((b) => b.toLocaleLowerCase('pt-BR').includes(q)) : bairros;
  }, [bairros, buscaBairro]);

  useEffect(() => {
    const controller = new AbortController();
    fetchStats(apiUrl, new URLSearchParams(), controller.signal)
      .then(setBase)
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Erro ao carregar relatório');
      });
    return () => controller.abort();
  }, [apiUrl]);

  const isDefault = filtroTipo === 'todos' && filtroBairros.length === 0;
  const statsKey = `${filtroTipo}|${[...filtroBairros].sort().join('|')}`;

  useEffect(() => {
    if (!base || isDefault) return;
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (filtroTipo !== 'todos') params.set('tipo', filtroTipo);
    Object.keys(base.by_bairro)
      .filter((raw) => filtroBairros.includes(normalizarBairro(raw)))
      .forEach((bairro) => params.append('bairro', bairro));
    fetchStats(apiUrl, params, controller.signal)
      .then((result) => {
        setError(null);
        setStats({ key: statsKey, value: result });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Erro ao carregar relatório');
      });
    return () => controller.abort();
  }, [apiUrl, base, filtroTipo, filtroBairros, isDefault, statsKey]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const visibleStats = isDefault ? base : stats?.key === statsKey ? stats.value : null;
  const porTipo = TIPOS_ORDEM
    .map((tipo) => ({ key: tipo, label: rotuloDoTipo(tipo), n: visibleStats?.by_tipo?.[tipo] ?? 0, color: corDoTipo(tipo, theme) }))
    .filter((row) => row.n > 0);
  const porBairro = [...Object.entries(visibleStats?.by_bairro ?? {})
    .reduce((acc, [raw, n]) => acc.set(normalizarBairro(raw), (acc.get(normalizarBairro(raw)) ?? 0) + n), new Map<string, number>())]
    .sort((a, b) => b[1] - a[1])
    .map(([nome, n]) => ({ key: nome, label: titleCase(nome), n }));
  const resumoFiltros = `Uso: ${filtroTipo === 'todos' ? 'todos' : rotuloDoTipo(filtroTipo)} · Bairros: ${filtroBairros.length === 0 ? 'todos' : filtroBairros.map(titleCase).join(', ')}`;

  const toggleBairro = (bairro: string) =>
    setFiltroBairros((list) => (list.includes(bairro) ? list.filter((b) => b !== bairro) : [...list, bairro]));

  return (
    <div className="report-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="report glass" role="dialog" aria-modal="true" aria-labelledby="report-title">
        <header className="report-head">
          <div>
            <h2 id="report-title">Relatório de edificações</h2>
            <p className="muted">Paulo Afonso · BA — área com cobertura de footprints</p>
          </div>
          <div className="report-actions">
            <button type="button" className="glass-button" onClick={() => window.print()}>
              <Icon name="print" size={16} /> Exportar PDF
            </button>
            <button type="button" className="icon-button" aria-label="Fechar relatório" onClick={onClose}>
              <Icon name="close" size={16} />
            </button>
          </div>
        </header>

        <p className="print-only">{resumoFiltros}</p>

        <div className="report-grid">
          <aside className="report-filters">
            <h3 className="section-label">Uso</h3>
            <div className="chip-wrap">
              <button type="button" className={`chip${filtroTipo === 'todos' ? ' on' : ''}`} onClick={() => setFiltroTipo('todos')}>Todos</button>
              {TIPOS_ORDEM.map((tipo) => (
                <button
                  key={tipo}
                  type="button"
                  className={`chip${filtroTipo === tipo ? ' on' : ''}`}
                  style={{ ['--tipo' as string]: corDoTipo(tipo, theme) }}
                  onClick={() => setFiltroTipo(tipo)}
                >
                  <span className="swatch" />{rotuloDoTipo(tipo)}
                </button>
              ))}
            </div>

            <div className="section-head">
              <h3 className="section-label">Bairros {filtroBairros.length > 0 && `· ${filtroBairros.length}`}</h3>
              {filtroBairros.length > 0 && <button type="button" className="link-button" onClick={() => setFiltroBairros([])}>Limpar</button>}
            </div>
            <div className="search-field compact">
              <Icon name="search" size={16} className="search-icon" />
              <input type="search" placeholder="Filtrar bairros" aria-label="Filtrar bairros" value={buscaBairro} onChange={(e) => setBuscaBairro(e.target.value)} />
            </div>
            <ul className="check-list">
              {bairrosFiltrados.map((bairro) => (
                <li key={bairro}>
                  <label>
                    <input type="checkbox" checked={filtroBairros.includes(bairro)} onChange={() => toggleBairro(bairro)} />
                    <span>{titleCase(bairro)}</span>
                  </label>
                </li>
              ))}
              {base && bairrosFiltrados.length === 0 && <li className="muted pad">Nenhum bairro com esse nome.</li>}
            </ul>
          </aside>

          <div className="report-content">
            {error && <p className="toast error" role="alert">Erro ao carregar: {error}</p>}
            <div className="stat-hero">
              <span className="stat-value">{visibleStats ? formatNumber(visibleStats.total) : '—'}</span>
              <span className="stat-caption">edificações{isDefault ? '' : ' com os filtros'}</span>
            </div>
            <h3 className="section-label">Por uso</h3>
            {visibleStats ? <BarList rows={porTipo} /> : <p className="muted">Carregando…</p>}
            <h3 className="section-label">Por bairro · {porBairro.length}</h3>
            {visibleStats ? <BarList rows={porBairro} color="var(--accent)" /> : <p className="muted">Carregando…</p>}
          </div>
        </div>
      </section>
    </div>
  );
}
