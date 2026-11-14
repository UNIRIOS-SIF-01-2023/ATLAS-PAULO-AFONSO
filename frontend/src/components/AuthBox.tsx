import { useState } from 'react';
import type { Theme } from '../constants';
import { Icon } from './Icon';

export type AuthUser = { id: number; nome: string; email: string };

type LoginResposta = { access_token: string; token_type: string; user: AuthUser };

type Props = {
  apiUrl: string;
  user: AuthUser | null;
  onAuth: (token: string, user: AuthUser) => void;
  onLogout: () => void;
};

async function postAuth(apiUrl: string, rota: 'login' | 'cadastro', body: { nome?: string; email: string; senha: string }): Promise<LoginResposta> {
  const response = await fetch(`${apiUrl}/api/auth/${rota}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  // O Pydantic pode devolver string ou lista em `detail`.
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
    throw new Error(detail ?? `HTTP ${response.status}`);
  }
  return data as LoginResposta;
}

const MENSAGENS: Record<string, string> = {
  'e-mail já cadastrado': 'Esse e-mail já tem cadastro. Tente entrar.',
  'e-mail ou senha inválidos': 'E-mail ou senha inválidos.',
};

export function AuthBox({ apiUrl, user, onAuth, onLogout }: Props) {
  const [modo, setModo] = useState<'login' | 'cadastro'>('login');
  const [nome, setNome] = useState('');
  const [email, setEmail] = useState('');
  const [senha, setSenha] = useState('');
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  if (user) {
    return (
      <div className="auth auth-logado" aria-label="Conta">
        <span className="auth-ola">Olá, {user.nome}</span>
        <button type="button" className="link-button" onClick={onLogout}>
          Sair
        </button>
      </div>
    );
  }

  async function enviar(event: React.FormEvent) {
    event.preventDefault();
    setCarregando(true);
    setErro(null);
    try {
      const body = modo === 'cadastro' ? { nome, email, senha } : { email, senha };
      const resposta = await postAuth(apiUrl, modo, body);
      onAuth(resposta.access_token, resposta.user);
    } catch (e) {
      const mensagem = e instanceof Error ? e.message : 'Não foi possível agora.';
      setErro(MENSAGENS[mensagem] ?? mensagem);
    } finally {
      setCarregando(false);
    }
  }

  return (
    <div className="auth" aria-label={modo === 'login' ? 'Entrar' : 'Criar conta'}>
      <div className="segmented" role="radiogroup" aria-label="Entrar ou criar conta">
        {(['login', 'cadastro'] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={modo === m}
            onClick={() => { setModo(m); setErro(null); }}
          >
            {m === 'login' ? 'Entrar' : 'Cadastrar'}
          </button>
        ))}
      </div>
      <form className="auth-form" onSubmit={enviar}>
        {modo === 'cadastro' && (
          <input
            type="text"
            placeholder="Nome"
            autoComplete="name"
            value={nome}
            onChange={(e) => setNome(e.target.value)}
            required
            minLength={2}
            maxLength={100}
          />
        )}
        <input
          type="email"
          placeholder="E-mail"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          type="password"
          placeholder="Senha"
          autoComplete={modo === 'cadastro' ? 'new-password' : 'current-password'}
          value={senha}
          onChange={(e) => setSenha(e.target.value)}
          required
          minLength={modo === 'cadastro' ? 6 : 1}
        />
        {erro && <p className="auth-erro" role="alert">{erro}</p>}
        <button type="submit" className="glass-button auth-enviar" disabled={carregando}>
          {carregando ? 'Aguarde…' : modo === 'login' ? 'Entrar' : 'Criar conta'}
        </button>
      </form>
    </div>
  );
}

/** Tela cheia e sem saída: sem JWT válido o app nem monta o mapa. */
export function AuthScreen({ apiUrl, onAuth, theme, onToggleTheme }: {
  apiUrl: string;
  onAuth: Props['onAuth'];
  theme: Theme;
  onToggleTheme: () => void;
}) {
  return (
    <div className="auth-screen">
      <div className="auth-card glass">
        <button
          type="button"
          className="icon-button auth-theme"
          aria-label={theme === 'light' ? 'Mudar para tema escuro' : 'Mudar para tema claro'}
          title={theme === 'light' ? 'Mudar para tema escuro' : 'Mudar para tema claro'}
          onClick={onToggleTheme}
        >
          <Icon name={theme === 'light' ? 'moon' : 'sun'} size={16} />
        </button>
        <header className="brand">
          <span className="brand-mark"><Icon name="building" size={22} /></span>
          <div>
            <h1>Atlas Urbano</h1>
            <p>Paulo Afonso · BA</p>
          </div>
        </header>
        <p className="muted">Entre ou crie sua conta para ver o mapa.</p>
        <AuthBox apiUrl={apiUrl} user={null} onAuth={onAuth} onLogout={() => {}} />
      </div>
    </div>
  );
}
