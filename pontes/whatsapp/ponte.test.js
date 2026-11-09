'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createBridge, describe, loadEnv, MARK } = require('./ponte');

function fakeFetch(reply) {
  const calls = [];
  const fetch = async (url, opts) => {
    calls.push({ url, headers: opts.headers, body: JSON.parse(opts.body) });
    return { ok: true, status: 200, json: async () => ({ resposta: reply }) };
  };
  return { fetch, calls };
}

const chat = { id: { _serialized: '5511999@c.us' }, name: 'Ana', isGroup: false };

test('mensagem de outra pessoa vai para a Akemi e a resposta volta na conversa', async () => {
  const { fetch, calls } = fakeFetch(`${MARK}Oi! Aqui é a Akemi.`);
  const sent = [];
  const bridge = createBridge({ url: 'http://x/mensagem', token: 't'.repeat(20), fetch });
  const r = await bridge.handle({ fromMe: false, body: 'oi', type: 'chat' }, chat, 'Ana', async (t) => sent.push(t));
  assert.equal(r, `${MARK}Oi! Aqui é a Akemi.`);
  assert.deepEqual(sent, [`${MARK}Oi! Aqui é a Akemi.`]);
  assert.equal(calls[0].headers.Authorization, `Bearer ${'t'.repeat(20)}`);
  assert.deepEqual(calls[0].body, { plataforma: 'whatsapp', id: '5511999@c.us', grupo: false, contato: 'Ana', texto: 'oi' });
});

test('resposta vazia: ela fica quieta', async () => {
  const { fetch } = fakeFetch('');
  const sent = [];
  const bridge = createBridge({ url: 'u', token: 't', fetch });
  assert.equal(await bridge.handle({ fromMe: false, body: 'oi', type: 'chat' }, chat, 'Ana', async (t) => sent.push(t)), null);
  assert.deepEqual(sent, []);
});

test('você escrevendo avisa que assumiu; a resposta dela não conta', async () => {
  const { fetch, calls } = fakeFetch('');
  const bridge = createBridge({ url: 'u', token: 't', fetch });
  await bridge.handle({ fromMe: true, body: 'já respondo', type: 'chat' }, chat, 'Ana', async () => {});
  await bridge.handle({ fromMe: true, body: `${MARK}Oi! Aqui é a Akemi.`, type: 'chat' }, chat, 'Ana', async () => {});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.do_dono, true);
});

test('status e mídia', async () => {
  const { fetch, calls } = fakeFetch('');
  const bridge = createBridge({ url: 'u', token: 't', fetch });
  await bridge.handle({ isStatus: true, body: 'story', type: 'chat' }, chat, 'Ana', async () => {});
  assert.equal(calls.length, 0);
  assert.equal(describe({ type: 'ptt', body: 'xyz' }), '(mandou um áudio)');
  assert.equal(describe({ type: 'image', body: 'olha' }), '(mandou uma foto) olha');
  assert.equal(describe({ type: 'chat', body: ' oi ' }), 'oi');
});

test('erro da Akemi vira exceção com dica', async () => {
  const fetch = async () => ({ ok: false, status: 403 });
  const bridge = createBridge({ url: 'u', token: 't', fetch });
  await assert.rejects(bridge.handle({ fromMe: false, body: 'oi', type: 'chat' }, chat, 'Ana', async () => {}), /403/);
});

test('lê o .env sem sobrescrever o ambiente', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'akemi-'));
  const file = path.join(dir, '.env');
  fs.writeFileSync(file, '# comentário\nAKEMI_TOKEN="segredo-longo-1234567"\nSECRETARIA_PORT=9000\n');
  const env = loadEnv([path.join(dir, 'nao-existe'), file], { SECRETARIA_PORT: '8766' });
  assert.equal(env.AKEMI_TOKEN, 'segredo-longo-1234567');
  assert.equal(env.SECRETARIA_PORT, '8766');
});
