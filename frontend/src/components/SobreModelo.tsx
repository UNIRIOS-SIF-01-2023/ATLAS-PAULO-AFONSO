import { useEffect, useState } from 'react';
import { rotuloDoTipo } from '../constants';
import { formatNumber } from '../format';
import { authHeaders } from '../session';
import { Icon } from './Icon';

type Metricas = { f1_macro: number; acuracia: number };
type PorClasse = Record<string, { precisao: number; recall: number; f1: number; suporte: number }>;
type Info = {
  versao: string;
  treinado_em: string;
  metricas: {
    dados: { n_total: number; n_treino: number; n_previsao: number };
    modelos: Record<string, { espacial: Metricas }>;
    melhor: { modelo: string };
    modelo_final: Metricas & { por_classe: PorClasse };
  };
};

const NOME_MODELO: Record<string, string> = {
  random_forest: 'Random Forest',
  gradient_boosting: 'Gradient Boosting',
};

const pct = (x: number) => `${(x * 100).toFixed(1).replace('.', ',')}%`;
const dec = (x: number) => x.toFixed(2).replace('.', ',');

/** Seção "Sobre o modelo" (RF05): o que a IA faz, com que dados e onde erra. */
export function SobreModelo({ apiUrl, onClose }: { apiUrl: string; onClose: () => void }) {
  const [info, setInfo] = useState<Info | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/predict-uso/modelo`, { signal: controller.signal, headers: { ...authHeaders() } })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Erro ${response.status}`);
        setInfo(body as Info);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setErro(err instanceof TypeError ? 'Sem conexão com o servidor.' : err instanceof Error ? err.message : 'Erro ao carregar.');
      });
    return () => controller.abort();
  }, [apiUrl]);

  const m = info?.metricas;
  const baseline = m?.modelos.mais_frequente?.espacial;

  return (
    <div className="report-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="report glass sobre-modelo" role="dialog" aria-modal="true" aria-labelledby="sobre-modelo-title">
        <header className="report-head">
          <div>
            <h2 id="sobre-modelo-title">Sobre o modelo</h2>
            <p className="muted">Classificador de uso das edificações sem endereço</p>
          </div>
          <button type="button" className="icon-button" aria-label="Fechar" onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </header>

        <div className="report-content">
          <h3 className="section-label">O que ele faz</h3>
          <p>
            Muitos prédios do mapa não têm endereço no CNEFE (IBGE), então o uso oficial deles é desconhecido. O modelo
            aprendeu com os prédios que têm uso oficial e estima o uso dos outros a partir do que todo prédio tem: a forma
            e o tamanho do polígono, a fonte do footprint e a vizinhança (quantos prédios há por perto, de que tamanho e de
            que uso). A porcentagem mostrada é a confiança do modelo na própria resposta.
          </p>

          {erro && <p className="ia-erro" role="alert">{erro}</p>}
          {!info && !erro && <p className="muted">Carregando…</p>}

          {m && m.modelo_final && baseline && (
            <>
              <h3 className="section-label">Dados</h3>
              <p>
                {formatNumber(m.dados.n_total)} prédios: {formatNumber(m.dados.n_treino)} com uso oficial serviram de
                exemplo e {formatNumber(m.dados.n_previsao)} sem uso conhecido recebem a estimativa. O uso oficial vem do
                CNEFE 2022 (IBGE); os polígonos, do Overture Maps (que reúne Google Open Buildings, Microsoft ML Buildings
                e OpenStreetMap) e do OpenStreetMap.
              </p>

              <h3 className="section-label">Resultado</h3>
              <p>
                Modelo: {NOME_MODELO[m.melhor.modelo] ?? m.melhor.modelo}, avaliado com validação cruzada espacial (áreas
                inteiras de 500 m ficam de fora do treino). F1 macro {dec(m.modelo_final.f1_macro)}, contra{' '}
                {dec(baseline.f1_macro)} de quem sempre responde “residencial”. A acurácia ({pct(m.modelo_final.acuracia)})
                quase não difere da desse palpite ({pct(baseline.acuracia)}), porque a maioria dos prédios é residencial.
              </p>
              <table className="ia-tabela">
                <thead>
                  <tr><th>Uso</th><th>Precisão</th><th>Recall</th><th>F1</th><th>Exemplos</th></tr>
                </thead>
                <tbody>
                  {Object.entries(m.modelo_final.por_classe).map(([classe, v]) => (
                    <tr key={classe}>
                      <td>{rotuloDoTipo(classe)}</td>
                      <td>{dec(v.precisao)}</td>
                      <td>{dec(v.recall)}</td>
                      <td>{dec(v.f1)}</td>
                      <td>{formatNumber(v.suporte)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <h3 className="section-label">Limites</h3>
          <ul className="ia-limites">
            <li>É uma estimativa, nunca um dado oficial.</li>
            <li>Acerta bem os residenciais, mas reconhece poucos comerciais e, na avaliação, não acertou nenhum dos usos raros (serviço, religioso, institucional, hospedagem, agropecuário). Quando ele aponta um uso raro, trate como um palpite fraco, ainda mais com confiança baixa.</li>
            <li>Os exemplos de treino são os prédios com endereço; os sem endereço podem ter outro perfil (anexos, galpões, área rural).</li>
            <li>Serve para priorizar conferência de campo e ler tendências, não para decidir sobre um prédio específico.</li>
          </ul>

          {info && <p className="muted small">Modelo {info.versao}, treinado em {new Date(info.treinado_em).toLocaleDateString('pt-BR')}.</p>}
        </div>
      </section>
    </div>
  );
}
