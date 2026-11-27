import { corDoTipo, rotuloDoTipo, TIPOS_ORDEM, type Theme } from '../constants';
import { formatNumber } from '../format';
import { Icon } from './Icon';

interface Props {
  counts: Record<string, number>;
  total: number;
  gridLabel: string | null;
  hiddenTipos: ReadonlySet<string>;
  theme?: Theme;
  onToggleTipo: (tipo: string) => void;
  onOnlyTipo: (tipo: string) => void;
  onShowAllTipos: () => void;
  showPontos: boolean;
  onShowPontos: (value: boolean) => void;
  soDoSelecionado: boolean;
  onSoDoSelecionado: (value: boolean) => void;
  hasSelection: boolean;
  onSobreModelo: () => void;
}

export function LayersPanel(props: Props) {
  const { counts, total, gridLabel, hiddenTipos, theme = 'dark' } = props;
  const max = Math.max(1, ...TIPOS_ORDEM.map((tipo) => counts[tipo] ?? 0));

  return (
    <div className="layers">
      <div className="stat-hero">
        <span className="stat-value">{formatNumber(total)}</span>
        <span className="stat-caption">{gridLabel ? 'edificações na área visível' : 'edificações na tela'}</span>
      </div>

      <div className="section-head">
        <h2 className="section-label">Uso da edificação</h2>
        {hiddenTipos.size > 0 && (
          <button type="button" className="link-button" onClick={props.onShowAllTipos}>Mostrar todos</button>
        )}
      </div>
      <ul className="tipo-list" aria-label="Filtrar por uso">
        {TIPOS_ORDEM.map((tipo) => {
          const visible = !hiddenTipos.has(tipo);
          const n = counts[tipo] ?? 0;
          return (
            <li key={tipo}>
              <button
                type="button"
                className={`tipo-row${visible ? '' : ' off'}`}
                aria-pressed={visible}
                title="Clique para mostrar ou esconder. Clique duplo para ver só este uso."
                onClick={() => props.onToggleTipo(tipo)}
                onDoubleClick={() => props.onOnlyTipo(tipo)}
              >
                <span className="swatch" style={{ background: corDoTipo(tipo, theme) }} />
                <span className="tipo-name">{rotuloDoTipo(tipo)}</span>
                <span className="tipo-count">{visible ? formatNumber(n) : 'oculto'}</span>
                <span className="tipo-bar" aria-hidden="true">
                  <span style={{ width: visible ? `${(n / max) * 100}%` : 0, background: corDoTipo(tipo, theme) }} />
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <h2 className="section-label">Endereços sem prédio</h2>
      <div className="option-card">
        <label className="switch-row">
          <span>
            <strong>Mostrar endereços</strong>
            <span className="muted">Pontos do CNEFE sem footprint mapeado</span>
          </span>
          <input type="checkbox" role="switch" className="switch" checked={props.showPontos} onChange={(e) => props.onShowPontos(e.target.checked)} />
        </label>
        {props.showPontos && (
          <div className="segmented" role="radiogroup" aria-label="Quais endereços mostrar">
            <button type="button" role="radio" aria-checked={!props.soDoSelecionado} onClick={() => props.onSoDoSelecionado(false)}>Todos</button>
            <button type="button" role="radio" aria-checked={props.soDoSelecionado} onClick={() => props.onSoDoSelecionado(true)}>Só do prédio selecionado</button>
          </div>
        )}
        {props.showPontos && props.soDoSelecionado && !props.hasSelection && (
          <p className="muted small">Selecione um prédio no mapa para ver os endereços dele.</p>
        )}
      </div>

      <h2 className="section-label">Inteligência artificial</h2>
      <div className="option-card">
        <p className="muted small">Prédios sem uso oficial recebem uma estimativa do classificador ao abrir o cartão.</p>
        <button type="button" className="link-button" onClick={props.onSobreModelo}>Sobre o modelo</button>
      </div>

      <p className="hint">
        <Icon name="info" size={14} />
        <span>
          {gridLabel
            ? `Visão afastada: cada quadrado resume ${gridLabel}, na cor do uso predominante. Aproxime ou toque num quadrado para ver os prédios.`
            : 'Toque num prédio para ver os dados e vê-lo em 3D. Afastando o mapa, os prédios viram pontos e depois uma grade de densidade.'}
        </span>
      </p>
    </div>
  );
}
