// Sessão JWT (login padrão): token + usuário no localStorage.
// O token vai em todo pedido à API (Authorization: Bearer) e é
// revalidado no servidor a cada abertura (/api/auth/eu).

export type SessaoUsuario = { id: number; nome: string; email: string };

export type Sessao = { token: string; user: SessaoUsuario };

const CHAVE = 'atlas-sessao';

export function lerSessao(): Sessao | null {
  try {
    const raw = localStorage.getItem(CHAVE);
    if (!raw) return null;
    const sessao = JSON.parse(raw) as Partial<Sessao>;
    if (typeof sessao.token !== 'string' || typeof sessao.user !== 'object' || !sessao.user) return null;
    return sessao as Sessao;
  } catch {
    return null;
  }
}

export function guardarSessao(sessao: Sessao): void {
  try {
    localStorage.setItem(CHAVE, JSON.stringify(sessao));
  } catch {
    /* navegador sem storage: segue só em memória */
  }
}

export function limparSessao(): void {
  try {
    localStorage.removeItem(CHAVE);
  } catch {
    /* sem storage: nada a limpar */
  }
}

/** Cabeçalho Bearer para os fetches da API (vazio sem login). */
export function authHeaders(): Record<string, string> {
  const sessao = lerSessao();
  return sessao ? { Authorization: `Bearer ${sessao.token}` } : {};
}

/** Confere o token no servidor (/api/auth/eu). Devolve o usuário, ou null se a
 * sessão não vale mais. Um pedido cancelado rejeita em vez de devolver null:
 * cancelamento (ex.: a montagem dupla do StrictMode) não é sessão inválida. */
export async function revalidarSessao(
  apiUrl: string,
  token: string,
  signal: AbortSignal,
  fetchFn: typeof fetch = fetch,
): Promise<SessaoUsuario | null> {
  try {
    const response = await fetchFn(`${apiUrl}/api/auth/eu`, {
      headers: { Authorization: `Bearer ${token}` },
      signal,
    });
    return response.ok ? ((await response.json()) as SessaoUsuario) : null;
  } catch (erro) {
    if (signal.aborted) throw erro;
    return null;
  }
}
