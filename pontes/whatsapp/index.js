'use strict';
// Liga o seu WhatsApp à secretária da Akemi (docs/SECRETARIA.md).
//
//   cd pontes/whatsapp
//   npm install
//   npm start          (na primeira vez, escaneie o QR code com o celular)
//
// Ele entra como um "aparelho conectado" da sua conta, igual ao WhatsApp Web.
// Não é oficial: o WhatsApp pode bloquear contas que usam automação. Use por sua conta e risco.

const path = require('node:path');
const { createBridge, loadEnv } = require('./ponte');

const env = loadEnv([path.join(__dirname, '.env'), path.join(__dirname, '..', '..', '.env')]);
const port = env.SECRETARIA_PORT || '8766';
const url = env.AKEMI_PONTE || `http://127.0.0.1:${port}/mensagem`;
const token = env.SECRETARIA_TOKEN || env.AKEMI_TOKEN || '';
if (token.length < 16) {
  console.error('Defina AKEMI_TOKEN (o mesmo do .env da Akemi, com 16 caracteres ou mais).');
  process.exit(2);
}

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

const bridge = createBridge({ url, token });
const client = new Client({ authStrategy: new LocalAuth({ dataPath: path.join(__dirname, '.sessao') }) });

client.on('qr', (qr) => {
  console.log('No celular: WhatsApp > Aparelhos conectados > Conectar um aparelho, e escaneie:');
  qrcode.generate(qr, { small: true });
});
client.on('ready', () => console.log(`WhatsApp conectado. Mensagens vão para a Akemi em ${url}`));
client.on('auth_failure', (why) => console.error('O login no WhatsApp falhou:', why));
client.on('disconnected', (why) => {
  console.error('O WhatsApp desconectou:', why);
  process.exit(1);
});

// message_create chega para toda mensagem nova: as dos outros e as suas.
client.on('message_create', async (msg) => {
  try {
    const chat = await msg.getChat();
    let name = chat.name;
    if (!msg.fromMe) {
      const contact = await msg.getContact();
      name = contact.pushname || contact.name || contact.number || chat.name;
    }
    // sendSeen: false = a conversa continua como não lida para você.
    await bridge.handle(msg, chat, name, (text) => chat.sendMessage(text, { sendSeen: false }));
  } catch (err) {
    console.error('erro:', err.message);
  }
});

client.initialize();
