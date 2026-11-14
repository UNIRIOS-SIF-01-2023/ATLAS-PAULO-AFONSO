import { Icon } from './Icon';

interface Props {
  bearing: number;
  is3d: boolean;
  all3d: boolean;
  locating: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetNorth: () => void;
  onToggle3d: () => void;
  onToggleAll3d: () => void;
  onRecenter: () => void;
  onLocate: () => void;
  onReport: () => void;
}

export function MapControls(props: Props) {
  const rotated = Math.abs(props.bearing) > 0.5;
  return (
    <nav className="controls" aria-label="Controles do mapa">
      <div className="glass control-group">
        <button type="button" className="control" aria-label="Aproximar" title="Aproximar" onClick={props.onZoomIn}>
          <Icon name="plus" />
        </button>
        <span className="control-sep" />
        <button type="button" className="control" aria-label="Afastar" title="Afastar" onClick={props.onZoomOut}>
          <Icon name="minus" />
        </button>
      </div>

      <div className="glass control-group">
        <button
          type="button"
          className={`control compass${rotated ? ' rotated' : ''}`}
          aria-label="Apontar para o norte"
          title="Apontar para o norte"
          onClick={props.onResetNorth}
        >
          <span style={{ transform: `rotate(${-props.bearing}deg)` }}>
            <Icon name="compass" />
          </span>
        </button>
        <span className="control-sep" />
        <button
          type="button"
          className={`control${props.is3d ? ' on' : ''}`}
          aria-pressed={props.is3d}
          aria-label="Vista inclinada (3D)"
          title={props.is3d ? 'Ver de cima (2D)' : 'Vista inclinada (3D)'}
          onClick={props.onToggle3d}
        >
          <span className="control-text">{props.is3d ? '3D' : '2D'}</span>
        </button>
        <span className="control-sep" />
        <button
          type="button"
          className={`control${props.all3d ? ' on' : ''}`}
          aria-pressed={props.all3d}
          aria-label="Todos os prédios em 3D"
          title={props.all3d ? 'Todos os prédios em 3D — clique para só o selecionado' : 'Só o prédio selecionado em 3D — clique para todos'}
          onClick={props.onToggleAll3d}
        >
          <Icon name={props.all3d ? 'buildings3d' : 'cube'} />
        </button>
      </div>

      <div className="glass control-group">
        <button type="button" className="control" aria-label="Ver a cidade inteira" title="Ver a cidade inteira" onClick={props.onRecenter}>
          <Icon name="home" />
        </button>
        <span className="control-sep" />
        <button
          type="button"
          className={`control${props.locating ? ' busy' : ''}`}
          aria-label="Minha localização"
          title="Minha localização"
          onClick={props.onLocate}
        >
          <Icon name="locate" />
        </button>
      </div>

      <div className="glass control-group">
        <button type="button" className="control" aria-label="Relatório" title="Relatório" onClick={props.onReport}>
          <Icon name="chart" />
        </button>
      </div>
    </nav>
  );
}
