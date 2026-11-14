import type { Theme } from '../constants';
import { formatNumber, titleCase } from '../format';
import { corDaConfianca, rotuloDaConfianca, type RooftopSelection } from '../rooftop';
import { Icon } from './Icon';

interface Props {
  selection: RooftopSelection;
  theme?: Theme;
  onClose: () => void;
  onCopy: (text: string, label: string) => void;
}

function numero(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function texto(value: unknown): string {
  return value == null || value === '' ? '—' : String(value);
}

export function RooftopCard({ selection, theme = 'dark', onClose, onCopy }: Props) {
  const props = selection.properties;
  const polygon = selection.kind === 'polygon';
  const confianca = props.confianca;
  const pct = numero(props.confiabilidade_pct);
  const enderecos = polygon ? String(props.enderecos ?? '').split(' | ').filter(Boolean) : [];
  const nEnderecos = numero(props.n_enderecos);
  const titulo = polygon
    ? `Prédio com ${formatNumber(nEnderecos ?? 0)} ${nEnderecos === 1 ? 'endereço' : 'endereços'}`
    : titleCase(String(props.endereco ?? '')) || 'Endereço sem nome';
  const bldId = texto(props.bld_id);
  const addrKey = texto(props.addr_key);
  const mudou = polygon
    ? Boolean(props.mudou)
    : Boolean(props.mudou_bld) || Boolean(props.mudou_ponto);

  return (
    <article className="building">
      <header className="card-head">
        <button type="button" className="icon-button" aria-label="Voltar" onClick={onClose}>
          <Icon name="back" size={18} />
        </button>
        <span className="tipo-pill" style={{ ['--tipo' as string]: corDaConfianca(confianca, theme) }}>
          <span className="swatch" />
          {rotuloDaConfianca(confianca)}{pct != null ? ` · ${formatNumber(pct)}%` : ''}
        </span>
        <button type="button" className="icon-button" aria-label="Fechar" onClick={onClose}>
          <Icon name="close" size={16} />
        </button>
      </header>

      <h2 className="building-title">{titulo}</h2>
      <p className="building-sub muted">
        {polygon ? 'Polígono do prédio no CNEFE rooftop' : 'Ponto de endereço no CNEFE rooftop'}
      </p>

      {polygon ? (
        <dl className="facts">
          <div><dt>Endereços</dt><dd>{formatNumber(nEnderecos ?? 0)}</dd></div>
          <div><dt>Unidades</dt><dd>{formatNumber(numero(props.n_unidades) ?? 0)}</dd></div>
          <div>
            <dt>Confiabilidade</dt>
            <dd>{pct != null ? `${formatNumber(pct)}%` : '—'}</dd>
          </div>
          <div><dt>Alterado</dt><dd>{mudou ? 'Sim' : 'Não'}</dd></div>
        </dl>
      ) : (
        <dl className="facts">
          <div><dt>Método</dt><dd>{texto(props.metodo)}</dd></div>
          <div>
            <dt>Confiabilidade</dt>
            <dd>{numero(props.confiabilidade_pct) != null ? `${formatNumber(numero(props.confiabilidade_pct)!)}%` : '—'}</dd>
          </div>
          <div><dt>Unidades</dt><dd>{formatNumber(numero(props.unidades) ?? 0)}</dd></div>
          <div>
            <dt>Deslocamento</dt>
            <dd>{numero(props.deslocamento_m) != null ? `${formatNumber(numero(props.deslocamento_m)!, 1)} m` : '—'}</dd>
          </div>
        </dl>
      )}

      {polygon && Boolean(props.revisar) && <p className="muted small">Face com ordem dos números para revisar.</p>}
      {polygon && props.metodos != null && <p className="muted small">Métodos: {texto(props.metodos)}</p>}
      {!polygon && mudou && (
        <p className="muted small">
          Antes: prédio {texto(props.bld_antes)} · {texto(props.metodo_antes)} · confiança{' '}
          {rotuloDaConfianca(props.confianca_antes).toLowerCase()}.
        </p>
      )}
      {!polygon && Boolean(props.revisar_face) && (
        <p className="muted small">Face com ordem dos números para revisar (ρ = {texto(props.face_rho)}).</p>
      )}

      {enderecos.length > 0 && (
        <>
          <h3 className="section-label">Endereços neste prédio</h3>
          <ul className="address-list">
            {enderecos.map((endereco) => (
              <li key={endereco}>
                <span className="result-row">
                  <span className="swatch" style={{ background: corDaConfianca(confianca, theme) }} />
                  <span className="result-text"><strong>{titleCase(endereco)}</strong></span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <footer className="provenance">
        <span>CNEFE rooftop (local)</span>
        <span>
          <button type="button" className="link-button" onClick={() => onCopy(bldId, 'ID do prédio copiado')}>
            Prédio {bldId}
          </button>
          {!polygon && (
            <>
              {' · '}
              <button type="button" className="link-button" onClick={() => onCopy(addrKey, 'ID do endereço copiado')}>
                Endereço {addrKey}
              </button>
            </>
          )}
        </span>
      </footer>
    </article>
  );
}
