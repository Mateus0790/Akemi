//! A conversa com o núcleo, sem rede e sem tela: só o estado e as regras.
//!
//! É a mesma lógica do `app/lib/link.dart` (Flutter) e fala o protocolo de
//! `akemi/net/companion.py`. A rede (`net.rs`) entrega o que chega em
//! [`Core::handle_message`] e manda o que estiver em [`Core::take_outbox`]; a
//! voz (`speaker.rs`) pega as falas em [`Core::next_say`].

use std::collections::{HashSet, VecDeque};

use serde_json::{json, Map, Value};

pub const PROTOCOL_VERSION: u64 = 1;
pub const CLOSE_UNAUTHORIZED: u16 = 4401;
pub const CLOSE_BAD_MESSAGE: u16 = 4400;
const MAX_ITEMS: usize = 200;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Desconectado,
    Conectando,
    Conectado,
    TokenRecusado,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChatItem {
    pub mine: bool,
    pub text: String,
    /// Ela disse isso em outro lugar (no PC).
    pub elsewhere: bool,
}

/// Algo que a tela ou o celular precisa fazer depois de uma mensagem.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Effect {
    /// Ela disse algo (vai para a notificação se o app estiver em segundo plano).
    AkemiText(String),
    /// Pare a voz agora.
    StopSpeaking,
}

/// Uma fala pendente: o `id` volta para o núcleo no `spoken`.
#[derive(Clone, Debug, PartialEq)]
pub struct Say {
    pub id: Value,
    pub text: String,
}

#[derive(Debug)]
pub struct Core {
    pub items: Vec<ChatItem>,
    pub status: Status,
    pub paused: bool,
    pub akemi_name: String,
    pub last_error: String,
    /// Modo "sozinha no celular": a IA direto, sem o núcleo.
    pub local: bool,
    /// Esperando a IA responder (só no modo sozinha).
    pub thinking: bool,
    outbox: Vec<String>,
    say_queue: VecDeque<Say>,
    current: Option<Value>,
    interrupted: HashSet<String>,
}

impl Default for Core {
    fn default() -> Self {
        Self {
            items: Vec::new(),
            status: Status::Desconectado,
            paused: false,
            akemi_name: "Akemi".into(),
            last_error: String::new(),
            outbox: Vec::new(),
            say_queue: VecDeque::new(),
            current: None,
            interrupted: HashSet::new(),
            local: false,
            thinking: false,
        }
    }
}

fn key(id: &Value) -> String {
    id.to_string()
}

impl Core {
    pub fn connected(&self) -> bool {
        self.status == Status::Conectado
    }

    /// Ela está falando (ou tem fala na fila).
    pub fn speaking(&self) -> bool {
        self.current.is_some() || !self.say_queue.is_empty()
    }

    fn send(&mut self, kind: &str, fields: Value) {
        let mut msg = Map::new();
        msg.insert("type".into(), Value::String(kind.into()));
        if let Value::Object(f) = fields {
            msg.extend(f);
        }
        self.outbox.push(Value::Object(msg).to_string());
    }

    /// O que precisa ir para o núcleo, na ordem.
    pub fn take_outbox(&mut self) -> Vec<String> {
        std::mem::take(&mut self.outbox)
    }

    /// A primeira mensagem de toda conexão.
    pub fn hello(&mut self, token: &str, name: &str) {
        let name = if name.trim().is_empty() { "celular" } else { name.trim() };
        self.outbox.clear();
        self.send(
            "hello",
            json!({
                "token": token,
                "version": PROTOCOL_VERSION,
                "corpo": {"nome": name, "tipo": "celular", "sentidos": ["voz", "microfone"]},
            }),
        );
    }

    fn add(&mut self, item: ChatItem) {
        self.items.push(item);
        if self.items.len() > MAX_ITEMS {
            let extra = self.items.len() - MAX_ITEMS;
            self.items.drain(..extra);
        }
    }

    // ------------------------------------------------------------ recepção
    pub fn handle_message(&mut self, raw: &str) -> Vec<Effect> {
        let Ok(Value::Object(msg)) = serde_json::from_str::<Value>(raw) else {
            return Vec::new();
        };
        let text = msg.get("text").and_then(Value::as_str).unwrap_or("").to_string();
        let mut effects = Vec::new();
        match msg.get("type").and_then(Value::as_str).unwrap_or("") {
            "welcome" => {
                self.last_error.clear();
                self.paused = msg.get("paused").and_then(Value::as_bool).unwrap_or(false);
                self.akemi_name = msg.get("nome").and_then(Value::as_str).unwrap_or("Akemi").to_string();
                self.items.clear();
                if let Some(Value::Array(hist)) = msg.get("historico") {
                    for h in hist {
                        if let Some(t) = h.get("texto").and_then(Value::as_str) {
                            let mine = h.get("de").and_then(Value::as_str) == Some("voce");
                            self.add(ChatItem {
                                mine,
                                text: t.to_string(),
                                elsewhere: false,
                            });
                        }
                    }
                }
                self.status = Status::Conectado;
            }
            "say" if !text.is_empty() => {
                self.add(ChatItem {
                    mine: false,
                    text: text.clone(),
                    elsewhere: false,
                });
                effects.push(Effect::AkemiText(text.clone()));
                self.say_queue.push_back(Say {
                    id: msg.get("id").cloned().unwrap_or(Value::Null),
                    text,
                });
            }
            "fala" if !text.is_empty() => {
                self.add(ChatItem {
                    mine: false,
                    text: text.clone(),
                    elsewhere: true,
                });
                effects.push(Effect::AkemiText(text));
            }
            "stop" => effects.extend(self.stop_local(true)),
            "paused" => self.paused = msg.get("paused").and_then(Value::as_bool).unwrap_or(false),
            _ => {}
        }
        effects
    }

    // ---------------------------------------------------------------- voz
    /// A próxima fala da fila (a voz chama, fala, e depois chama [`Core::finish_say`]).
    pub fn next_say(&mut self) -> Option<Say> {
        let say = self.say_queue.pop_front()?;
        self.current = Some(say.id.clone());
        Some(say)
    }

    /// Terminou de falar (`ok` = sem erro na voz). Avisa o núcleo como foi.
    pub fn finish_say(&mut self, id: &Value, ok: bool) {
        let status = if self.interrupted.remove(&key(id)) {
            "interrupted"
        } else if ok {
            "done"
        } else {
            "error"
        };
        if self.current.as_ref() == Some(id) {
            self.current = None;
        }
        self.send("spoken", json!({"id": id, "status": status}));
    }

    /// Para de falar e avisa o núcleo do que ficou sem falar.
    fn stop_local(&mut self, notify: bool) -> Vec<Effect> {
        let pending: Vec<Say> = self.say_queue.drain(..).collect();
        if notify {
            for say in pending {
                self.send("spoken", json!({"id": say.id, "status": "interrupted"}));
            }
        }
        if let Some(id) = &self.current {
            self.interrupted.insert(key(id));
        }
        vec![Effect::StopSpeaking]
    }

    // ------------------------------------------------------------ conexão
    pub fn set_connecting(&mut self) {
        self.status = Status::Conectando;
        self.local = false;
        self.thinking = false;
    }

    // ------------------------------------------------------ modo sozinha
    /// Liga o modo sozinha com a conversa que estava guardada no celular.
    pub fn start_local(&mut self, items: Vec<ChatItem>) {
        self.local = true;
        self.thinking = false;
        self.paused = false;
        self.akemi_name = "Akemi".into();
        self.last_error.clear();
        self.items = items;
        self.outbox.clear();
        self.status = Status::Conectado;
    }

    /// O que você mandou e ainda não foi respondido (o modo sozinha não usa a rede).
    pub fn take_local_messages(&mut self) -> usize {
        let pending = self
            .take_outbox()
            .iter()
            .filter(|raw| raw.contains(r#""type":"owner_message""#))
            .count();
        if pending > 0 {
            self.thinking = true;
        }
        pending
    }

    /// A IA respondeu: entra no chat e na fila da voz.
    pub fn local_reply(&mut self, text: &str) -> Vec<Effect> {
        self.thinking = false;
        self.add(ChatItem {
            mine: false,
            text: text.to_string(),
            elsewhere: false,
        });
        self.say_queue.push_back(Say {
            id: Value::Null,
            text: text.to_string(),
        });
        vec![Effect::AkemiText(text.to_string())]
    }

    /// A IA não respondeu: avisa na conversa (sem falar em voz alta).
    pub fn local_error(&mut self, error: &str) {
        self.thinking = false;
        self.add(ChatItem {
            mine: false,
            text: format!("{}: {error}]", crate::brain::ERROR_PREFIX),
            elsewhere: false,
        });
    }

    /// A conexão caiu. `code` é o código de fechamento do WebSocket, se veio um.
    /// Devolve `true` se vale tentar de novo.
    pub fn connection_lost(&mut self, code: Option<u16>, reason: &str) -> (bool, Vec<Effect>) {
        let effects = self.stop_local(false);
        self.current = None;
        self.interrupted.clear();
        self.outbox.clear();
        match code {
            Some(CLOSE_UNAUTHORIZED) => {
                self.last_error = "A Akemi recusou o token. Confira o AKEMI_TOKEN.".into();
                self.status = Status::TokenRecusado;
                (false, effects)
            }
            Some(CLOSE_BAD_MESSAGE) => {
                self.last_error = if reason.is_empty() {
                    "Conexão recusada.".into()
                } else {
                    reason.into()
                };
                self.status = Status::Conectando;
                (true, effects)
            }
            _ => {
                self.status = Status::Conectando;
                (true, effects)
            }
        }
    }

    pub fn unreachable(&mut self, url: &str) {
        self.last_error = format!("Não achei a Akemi em {url}. O PC está ligado e no mesmo Wi-Fi?");
        self.status = Status::Conectando;
    }

    pub fn disconnected(&mut self) {
        self.status = Status::Desconectado;
    }

    // ------------------------------------------------------------- envio
    /// Você digitou (ou ditou) algo para ela.
    pub fn send_text(&mut self, text: &str) -> (bool, Vec<Effect>) {
        let text = text.trim();
        if text.is_empty() || !self.connected() {
            return (false, Vec::new());
        }
        let effects = self.stop_local(true);
        self.add(ChatItem {
            mine: true,
            text: text.to_string(),
            elsewhere: false,
        });
        self.send("owner_message", json!({"text": text}));
        (true, effects)
    }

    /// Você começou a falar (ou a digitar) enquanto ela falava: ela para na hora.
    pub fn interrupt(&mut self) -> Vec<Effect> {
        if !self.connected() {
            return Vec::new();
        }
        let effects = self.stop_local(true);
        self.send("owner_speech_started", json!({}));
        effects
    }

    pub fn toggle_pause(&mut self) {
        if self.connected() && !self.local {
            let kind = if self.paused { "resume" } else { "pause" };
            self.send(kind, json!({}));
        }
    }

    pub fn status_text(&self) -> &'static str {
        match self.status {
            Status::Conectado if self.local && self.thinking => "pensando...",
            Status::Conectado if self.local => "sozinha no celular",
            Status::Conectado if self.paused => "pausada (só responde a você)",
            Status::Conectado => "conectada",
            Status::Conectando => "conectando...",
            Status::TokenRecusado => "token recusado",
            Status::Desconectado => "desconectada",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sent(core: &mut Core) -> Vec<Value> {
        core.take_outbox()
            .iter()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect()
    }

    fn welcome(core: &mut Core) {
        core.handle_message(r#"{"type":"welcome"}"#);
    }

    #[test]
    fn welcome_traz_o_historico_e_o_nome() {
        let mut c = Core::default();
        c.handle_message(
            &json!({"type": "welcome", "paused": false, "nome": "Akemi",
                    "historico": [{"de": "voce", "texto": "oi"}, {"de": "akemi", "texto": "Oi! Sumiu, hein."}]})
            .to_string(),
        );
        assert!(c.connected());
        let texts: Vec<_> = c.items.iter().map(|i| i.text.as_str()).collect();
        assert_eq!(texts, ["oi", "Oi! Sumiu, hein."]);
        assert!(c.items[0].mine && !c.items[1].mine);
    }

    #[test]
    fn hello_se_apresenta_como_celular() {
        let mut c = Core::default();
        c.hello("t".repeat(20).as_str(), "  ");
        let msg = &sent(&mut c)[0];
        assert_eq!(msg["type"], "hello");
        assert_eq!(msg["version"], 1);
        assert_eq!(msg["corpo"]["tipo"], "celular");
        assert_eq!(msg["corpo"]["nome"], "celular");
    }

    #[test]
    fn say_vai_para_a_fila_e_confirma_para_o_nucleo() {
        let mut c = Core::default();
        welcome(&mut c);
        let fx = c.handle_message(r#"{"type":"say","id":7,"text":"Tô aqui."}"#);
        assert_eq!(fx, vec![Effect::AkemiText("Tô aqui.".into())]);
        let say = c.next_say().unwrap();
        assert_eq!(say.text, "Tô aqui.");
        assert!(c.speaking());
        c.finish_say(&say.id, true);
        assert!(!c.speaking());
        assert_eq!(sent(&mut c), vec![json!({"type": "spoken", "id": 7, "status": "done"})]);
    }

    #[test]
    fn fala_do_pc_so_aparece_no_chat() {
        let mut c = Core::default();
        let fx = c.handle_message(r#"{"type":"fala","text":"Lembrete: água"}"#);
        assert_eq!(fx, vec![Effect::AkemiText("Lembrete: água".into())]);
        assert!(c.items[0].elsewhere);
        assert!(c.next_say().is_none());
    }

    #[test]
    fn stop_interrompe_a_fala_e_a_fila() {
        let mut c = Core::default();
        welcome(&mut c);
        c.handle_message(r#"{"type":"say","id":1,"text":"um"}"#);
        c.handle_message(r#"{"type":"say","id":"b","text":"dois"}"#);
        let first = c.next_say().unwrap();
        let fx = c.handle_message(r#"{"type":"stop"}"#);
        assert_eq!(fx, vec![Effect::StopSpeaking]);
        c.finish_say(&first.id, true);
        assert_eq!(
            sent(&mut c),
            vec![
                json!({"type": "spoken", "id": "b", "status": "interrupted"}),
                json!({"type": "spoken", "id": 1, "status": "interrupted"}),
            ]
        );
        assert!(c.next_say().is_none());
    }

    #[test]
    fn mensagem_so_sai_conectado() {
        let mut c = Core::default();
        assert!(!c.send_text("oi").0);
        welcome(&mut c);
        assert!(c.send_text("  oi akemi  ").0);
        assert_eq!(sent(&mut c), vec![json!({"type": "owner_message", "text": "oi akemi"})]);
        assert!(c.items.last().unwrap().mine);
    }

    #[test]
    fn interromper_avisa_o_nucleo() {
        let mut c = Core::default();
        welcome(&mut c);
        assert_eq!(c.interrupt(), vec![Effect::StopSpeaking]);
        assert_eq!(sent(&mut c), vec![json!({"type": "owner_speech_started"})]);
    }

    #[test]
    fn pausa_pede_e_o_nucleo_confirma() {
        let mut c = Core::default();
        welcome(&mut c);
        c.toggle_pause();
        assert_eq!(sent(&mut c), vec![json!({"type": "pause"})]);
        c.handle_message(r#"{"type":"paused","paused":true}"#);
        assert!(c.paused);
        assert_eq!(c.status_text(), "pausada (só responde a você)");
        c.toggle_pause();
        assert_eq!(sent(&mut c), vec![json!({"type": "resume"})]);
    }

    #[test]
    fn token_recusado_para_de_tentar() {
        let mut c = Core::default();
        welcome(&mut c);
        let (retry, _) = c.connection_lost(Some(CLOSE_UNAUTHORIZED), "token inválido");
        assert!(!retry);
        assert_eq!(c.status, Status::TokenRecusado);
        let (retry, _) = c.connection_lost(None, "");
        assert!(retry);
    }

    #[test]
    fn modo_sozinha_responde_sem_rede() {
        let mut c = Core::default();
        c.start_local(vec![ChatItem {
            mine: true,
            text: "antes".into(),
            elsewhere: false,
        }]);
        assert!(c.connected() && c.local);
        assert_eq!(c.status_text(), "sozinha no celular");
        assert!(c.send_text("oi").0);
        assert_eq!(c.take_local_messages(), 1);
        assert_eq!(c.status_text(), "pensando...");
        let fx = c.local_reply("Oi! Tô aqui.");
        assert_eq!(fx, vec![Effect::AkemiText("Oi! Tô aqui.".into())]);
        let say = c.next_say().unwrap();
        c.finish_say(&say.id, true);
        assert_eq!(c.take_local_messages(), 0); // o `spoken` não vai para lugar nenhum
        c.toggle_pause();
        assert!(c.take_outbox().is_empty());
        let texts: Vec<_> = c.items.iter().map(|i| i.text.as_str()).collect();
        assert_eq!(texts, ["antes", "oi", "Oi! Tô aqui."]);
        c.set_connecting();
        assert!(!c.local);
    }

    #[test]
    fn mensagens_quebradas_sao_ignoradas() {
        let mut c = Core::default();
        assert!(c.handle_message("isso não é json").is_empty());
        assert!(c.handle_message("[1, 2]").is_empty());
        assert!(c.handle_message(r#"{"type":"say","text":""}"#).is_empty());
        assert!(c.items.is_empty());
    }

    #[test]
    fn historico_guarda_no_maximo_200() {
        let mut c = Core::default();
        for i in 0..250 {
            c.handle_message(&json!({"type": "fala", "text": format!("n{i}")}).to_string());
        }
        assert_eq!(c.items.len(), 200);
        assert_eq!(c.items[0].text, "n50");
    }
}
