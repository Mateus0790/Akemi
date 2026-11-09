'use strict';
// A parte que conversa com a Akemi. Não depende do WhatsApp, para dar para testar
// (node --test). Quem liga no WhatsApp de verdade é o index.js.

const fs = require('node:fs');

const MARK = '🤖 '; // a Akemi marca o que ela manda pela sua conta
const MEDIA = {
  ptt: 'mandou um áudio', audio: 'mandou um áudio', image: 'mandou uma foto', video: 'mandou um vídeo',
  sticker: 'mandou uma figurinha', document: 'mandou um arquivo', location: 'mandou uma localização',
  vcard: 'mandou um contato',
};
const WITH_CAPTION = new Set(['image', 'video', 'document']);

/** O texto da mensagem; o que não é texto vira uma descrição ("(mandou um áudio)"). */
function describe(msg) {
  const body = String(msg.body || '').trim();
  const what = MEDIA[msg.type];
  if (!what) return body;
  return WITH_CAPTION.has(msg.type) && body ? `(${what}) ${body}` : `(${what})`;
}

/** Lê KEY=VALOR dos .env que existirem (sem sobrescrever o que já está no ambiente). */
function loadEnv(paths, env = process.env) {
  for (const path of paths) {
    let text;
    try { text = fs.readFileSync(path, 'utf8'); } catch { continue; }
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
      if (m && !(m[1] in env)) env[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
    }
  }
  return env;
}

function createBridge({ url, token, fetch = globalThis.fetch }) {
  async function post(payload) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`a Akemi respondeu ${res.status} (confira o AKEMI_TOKEN e se ela está aberta)`);
    return res.json();
  }

  /**
   * Uma mensagem nova numa conversa (sua ou de outra pessoa).
   * msg: {fromMe, body, type, from, isStatus}; chat: {id: {_serialized}, name, isGroup}.
   * send(texto) manda a resposta dela na conversa. Devolve o que ela respondeu (ou null).
   */
  async function handle(msg, chat, contactName, send) {
    if (msg.isStatus || msg.from === 'status@broadcast') return null;
    const base = { plataforma: 'whatsapp', id: chat.id._serialized, grupo: Boolean(chat.isGroup) };
    if (msg.fromMe) {
      if (String(msg.body || '').startsWith(MARK)) return null; // é a resposta dela
      // Foi você: ela sai de cena nessa conversa por um tempo.
      await post({ ...base, contato: chat.name || contactName || 'você', texto: describe(msg) || '(mensagem)', do_dono: true });
      return null;
    }
    const texto = describe(msg);
    if (!texto) return null;
    const r = await post({ ...base, contato: contactName || chat.name || 'alguém', texto });
    if (r && r.resposta) {
      await send(r.resposta);
      return r.resposta;
    }
    return null;
  }

  return { handle, post };
}

module.exports = { createBridge, describe, loadEnv, MARK };
