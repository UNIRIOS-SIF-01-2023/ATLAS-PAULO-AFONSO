import assert from 'node:assert/strict';
import { test } from 'node:test';
import { revalidarSessao } from '../src/session.ts';

const usuario = { id: 7, nome: 'Teste', email: 't@x' };
const resposta = (status, corpo) => async () => ({ ok: status < 400, status, json: async () => corpo });

test('token aceito devolve o usuário', async () => {
  const u = await revalidarSessao('http://api', 'tok', new AbortController().signal, resposta(200, usuario));
  assert.deepEqual(u, usuario);
});

test('token recusado pelo servidor devolve null (a sessão deve ser apagada)', async () => {
  const u = await revalidarSessao('http://api', 'tok', new AbortController().signal, resposta(401, {}));
  assert.equal(u, null);
});

test('pedido cancelado não conta como sessão inválida', async () => {
  // O React (StrictMode) monta o efeito duas vezes e cancela o primeiro fetch.
  const controller = new AbortController();
  const fetchCancelado = async () => {
    controller.abort();
    throw new DOMException('aborted', 'AbortError');
  };
  await assert.rejects(revalidarSessao('http://api', 'tok', controller.signal, fetchCancelado));
});
