//! A ligação com o núcleo: uma linha de rede e uma de voz, em segundo plano.
//!
//! - A linha de rede conecta no núcleo (WebSocket), reconecta sozinha quando
//!   cai (1, 2, 4, 8, 16, 30 s) e para se o token for recusado.
//! - A linha de voz fala os `say` um de cada vez e avisa o núcleo (`spoken`).
//!
//! As duas mexem no mesmo [`Core`], e a tela é avisada a cada mudança.

use std::io::ErrorKind;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use tungstenite::protocol::frame::coding::CloseCode;
use tungstenite::{Message, WebSocket};

use crate::brain::Brain;
use crate::core::{ChatItem, Core, Effect, Status};
use crate::settings::{parse_ws_url, Mode, Settings};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const POLL: Duration = Duration::from_millis(120);
const PING_EVERY: Duration = Duration::from_secs(20);
/// Sem nada do núcleo por este tempo, a conexão morreu (celular saiu do Wi-Fi).
const SILENCE_LIMIT: Duration = Duration::from_secs(60);

/// O que muda de aparelho para aparelho (voz, notificação). No PC não faz nada.
pub trait Platform: Send + Sync {
    /// Fala e só volta quando terminar (ou quando [`Platform::stop_speaking`] for chamado).
    /// Devolve `false` se a voz deu erro.
    fn speak(&self, text: &str) -> bool;
    fn stop_speaking(&self);
    fn notify(&self, title: &str, body: &str);
    /// O app está escondido (outra tela na frente, celular bloqueado).
    fn in_background(&self) -> bool;
}

#[derive(Default)]
struct Control {
    settings: Settings,
    wanted: bool,
    generation: u64,
    wake: bool,
}

struct Inner {
    core: Mutex<Core>,
    say_ready: Condvar,
    ctl: Mutex<Control>,
    ctl_changed: Condvar,
    muted: AtomicBool,
    platform: Arc<dyn Platform>,
    on_change: Box<dyn Fn() + Send + Sync>,
    /// Onde guardar a conversa do modo sozinha (no celular).
    history_file: Option<PathBuf>,
}

#[derive(Clone)]
pub struct Link(Arc<Inner>);

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

impl Link {
    /// `data_dir`: a pasta do app, onde fica a conversa do modo sozinha (`None` = não guarda).
    pub fn new(
        platform: Arc<dyn Platform>,
        data_dir: Option<PathBuf>,
        on_change: impl Fn() + Send + Sync + 'static,
    ) -> Self {
        let inner = Arc::new(Inner {
            core: Mutex::new(Core::default()),
            say_ready: Condvar::new(),
            ctl: Mutex::new(Control::default()),
            ctl_changed: Condvar::new(),
            muted: AtomicBool::new(false),
            platform,
            on_change: Box::new(on_change),
            history_file: data_dir.map(|d| d.join("conversa-sozinha.json")),
        });
        let net = inner.clone();
        thread::Builder::new()
            .name("akemi-rede".into())
            .spawn(move || net_loop(&net))
            .expect("thread");
        let voice = inner.clone();
        thread::Builder::new()
            .name("akemi-voz".into())
            .spawn(move || voice_loop(&voice))
            .expect("thread");
        Self(inner)
    }

    /// Lê o estado para desenhar a tela.
    pub fn with_core<R>(&self, f: impl FnOnce(&Core) -> R) -> R {
        f(&lock(&self.0.core))
    }

    /// Conecta (ou reconecta com endereço/token novos).
    pub fn start(&self, settings: &Settings) {
        self.0.muted.store(settings.muted, Ordering::Relaxed);
        let mut ctl = lock(&self.0.ctl);
        ctl.settings = settings.clone();
        ctl.wanted = true;
        ctl.generation += 1;
        drop(ctl);
        lock(&self.0.core).last_error.clear();
        self.0.ctl_changed.notify_all();
    }

    /// O app voltou para a frente: se caiu, tenta já em vez de esperar o intervalo.
    pub fn wake_up(&self) {
        lock(&self.0.ctl).wake = true;
        self.0.ctl_changed.notify_all();
    }

    pub fn set_muted(&self, muted: bool) {
        self.0.muted.store(muted, Ordering::Relaxed);
        if muted {
            self.0.platform.stop_speaking();
        }
    }

    pub fn send_text(&self, text: &str) -> bool {
        let (ok, fx) = lock(&self.0.core).send_text(text);
        self.0.apply(fx);
        self.0.changed();
        ok
    }

    /// Você começou a falar ou digitar: se ela estiver falando, para.
    pub fn interrupt_if_speaking(&self) {
        let fx = {
            let mut core = lock(&self.0.core);
            if !core.speaking() {
                return;
            }
            core.interrupt()
        };
        self.0.apply(fx);
        self.0.changed();
    }

    pub fn toggle_pause(&self) {
        lock(&self.0.core).toggle_pause();
    }
}

impl Inner {
    fn changed(&self) {
        (self.on_change)();
    }

    fn apply(&self, effects: Vec<Effect>) {
        for fx in effects {
            match fx {
                Effect::StopSpeaking => self.platform.stop_speaking(),
                Effect::AkemiText(text) => {
                    if self.platform.in_background() {
                        let name = lock(&self.core).akemi_name.clone();
                        self.platform.notify(&name, &text);
                    }
                }
            }
        }
    }

    fn current(&self) -> (u64, bool) {
        let ctl = lock(&self.ctl);
        (ctl.generation, ctl.wanted)
    }

    /// Espera até `d` passar, ou até mudarem as configurações, ou o app voltar para a frente.
    fn sleep(&self, d: Duration, generation: u64) {
        let deadline = Instant::now() + d;
        let mut ctl = lock(&self.ctl);
        ctl.wake = false;
        while !ctl.wake && ctl.generation == generation {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            ctl = self
                .ctl_changed
                .wait_timeout(ctl, left)
                .unwrap_or_else(|e| e.into_inner())
                .0;
        }
        ctl.wake = false;
    }
}

// ------------------------------------------------------------------ rede
enum End {
    /// Trocaram o endereço/token, ou mandaram parar.
    Replaced,
    Closed(Option<u16>, String),
}

fn net_loop(inner: &Inner) {
    let mut delay = 1u64;
    loop {
        let (generation, settings) = {
            let mut ctl = lock(&inner.ctl);
            while !ctl.wanted {
                ctl = inner.ctl_changed.wait(ctl).unwrap_or_else(|e| e.into_inner());
            }
            (ctl.generation, ctl.settings.clone())
        };
        lock(&inner.core).set_connecting();
        inner.changed();

        if settings.mode == Mode::Alone {
            alone_loop(inner, generation, &settings);
            delay = 1;
            continue;
        }

        match connect(&settings.url) {
            Err(_) => {
                lock(&inner.core).unreachable(&settings.url);
                inner.changed();
            }
            Ok(mut ws) => {
                lock(&inner.core).hello(&settings.token, &settings.name);
                let (end, welcomed) = session(inner, &mut ws, generation);
                let _ = ws.close(None);
                let _ = ws.flush();
                match end {
                    End::Replaced => {
                        delay = 1;
                        continue;
                    }
                    End::Closed(code, reason) => {
                        let (retry, fx) = lock(&inner.core).connection_lost(code, &reason);
                        inner.apply(fx);
                        if welcomed {
                            delay = 1;
                        }
                        if !retry {
                            let mut ctl = lock(&inner.ctl);
                            if ctl.generation == generation {
                                ctl.wanted = false;
                            }
                            drop(ctl);
                            inner.changed();
                            continue;
                        }
                        inner.changed();
                    }
                }
            }
        }
        if inner.current() != (generation, true) {
            delay = 1;
            if !inner.current().1 {
                lock(&inner.core).disconnected();
                inner.changed();
            }
            continue;
        }
        inner.sleep(Duration::from_secs(delay), generation);
        delay = if delay >= 15 { 30 } else { delay * 2 };
    }
}

fn connect(url: &str) -> Result<WebSocket<TcpStream>, String> {
    let (host, port) = parse_ws_url(url).ok_or("endereço inválido")?;
    let addrs = (host.as_str(), port).to_socket_addrs().map_err(|e| e.to_string())?;
    let mut last = "sem endereço".to_string();
    for addr in addrs {
        match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
            Ok(stream) => {
                let _ = stream.set_nodelay(true);
                stream
                    .set_read_timeout(Some(CONNECT_TIMEOUT))
                    .map_err(|e| e.to_string())?;
                stream
                    .set_write_timeout(Some(CONNECT_TIMEOUT))
                    .map_err(|e| e.to_string())?;
                let (ws, _) = tungstenite::client(url, stream).map_err(|e| e.to_string())?;
                ws.get_ref().set_read_timeout(Some(POLL)).map_err(|e| e.to_string())?;
                return Ok(ws);
            }
            Err(e) => last = e.to_string(),
        }
    }
    Err(last)
}

fn session(inner: &Inner, ws: &mut WebSocket<TcpStream>, generation: u64) -> (End, bool) {
    let mut welcomed = false;
    let mut close: Option<(u16, String)> = None;
    let mut last_heard = Instant::now();
    let mut last_ping = Instant::now();
    loop {
        if inner.current() != (generation, true) {
            return (End::Replaced, welcomed);
        }
        let outbox = lock(&inner.core).take_outbox();
        for raw in outbox {
            if ws.send(Message::Text(raw.into())).is_err() {
                return (End::Closed(None, String::new()), welcomed);
            }
        }
        if last_ping.elapsed() >= PING_EVERY {
            last_ping = Instant::now();
            let _ = ws.send(Message::Ping(Vec::new().into()));
        }
        if last_heard.elapsed() >= SILENCE_LIMIT {
            return (End::Closed(None, String::new()), welcomed);
        }
        match ws.read() {
            Ok(msg) => {
                last_heard = Instant::now();
                let text = match msg {
                    Message::Text(t) => t.to_string(),
                    Message::Binary(b) => String::from_utf8_lossy(&b).into_owned(),
                    Message::Close(frame) => {
                        if let Some(f) = frame {
                            close = Some((u16::from(f.code), f.reason.to_string()));
                        } else {
                            close = Some((u16::from(CloseCode::Normal), String::new()));
                        }
                        continue;
                    }
                    _ => continue,
                };
                let fx = {
                    let mut core = lock(&inner.core);
                    let fx = core.handle_message(&text);
                    welcomed |= core.status == Status::Conectado;
                    fx
                };
                inner.say_ready.notify_all();
                inner.apply(fx);
                inner.changed();
            }
            Err(tungstenite::Error::Io(e)) if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
            Err(_) => {
                let (code, reason) = close.map_or((None, String::new()), |(c, r)| (Some(c), r));
                return (End::Closed(code, reason), welcomed);
            }
        }
    }
}

// --------------------------------------------------------- modo sozinha
/// A Akemi sem o núcleo: cada mensagem sua vai direto para a IA.
fn alone_loop(inner: &Inner, generation: u64, settings: &Settings) {
    let brain = Brain::new(settings);
    let items = inner.history_file.as_ref().map(|f| load_history(f)).unwrap_or_default();
    lock(&inner.core).start_local(items);
    inner.changed();
    while inner.current() == (generation, true) {
        let pending = lock(&inner.core).take_local_messages();
        if pending == 0 {
            thread::sleep(POLL);
            continue;
        }
        inner.changed();
        let history = lock(&inner.core).items.clone();
        let answer = brain.reply(&history);
        if inner.current() != (generation, true) {
            break; // trocaram as configurações enquanto ela pensava
        }
        let fx = {
            let mut core = lock(&inner.core);
            match answer {
                Ok(text) => core.local_reply(&text),
                Err(e) => {
                    core.local_error(&e);
                    Vec::new()
                }
            }
        };
        inner.say_ready.notify_all();
        inner.apply(fx);
        if let Some(file) = &inner.history_file {
            save_history(file, &lock(&inner.core).items);
        }
        inner.changed();
    }
}

fn load_history(file: &std::path::Path) -> Vec<ChatItem> {
    let Ok(raw) = std::fs::read_to_string(file) else {
        return Vec::new();
    };
    let Ok(serde_json::Value::Array(list)) = serde_json::from_str(&raw) else {
        return Vec::new();
    };
    list.iter()
        .filter_map(|v| {
            Some(ChatItem {
                mine: v.get("de")?.as_str()? == "voce",
                text: v.get("texto")?.as_str()?.to_string(),
                elsewhere: false,
            })
        })
        .collect()
}

fn save_history(file: &std::path::Path, items: &[ChatItem]) {
    let list: Vec<_> = items
        .iter()
        .filter(|i| !i.elsewhere)
        .map(|i| serde_json::json!({"de": if i.mine { "voce" } else { "akemi" }, "texto": i.text}))
        .collect();
    let tmp = file.with_extension("json.tmp");
    if std::fs::write(&tmp, serde_json::Value::Array(list).to_string()).is_ok() {
        let _ = std::fs::rename(tmp, file);
    }
}

// ------------------------------------------------------------------- voz
fn voice_loop(inner: &Inner) {
    loop {
        let say = {
            let mut core = lock(&inner.core);
            loop {
                if let Some(say) = core.next_say() {
                    break say;
                }
                core = inner.say_ready.wait(core).unwrap_or_else(|e| e.into_inner());
            }
        };
        let ok = inner.muted.load(Ordering::Relaxed) || inner.platform.speak(&say.text);
        lock(&inner.core).finish_say(&say.id, ok);
        inner.changed();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::mpsc;

    struct Quiet(Mutex<Vec<String>>);
    impl Platform for Quiet {
        fn speak(&self, text: &str) -> bool {
            lock(&self.0).push(text.to_string());
            true
        }
        fn stop_speaking(&self) {}
        fn notify(&self, _: &str, _: &str) {}
        fn in_background(&self) -> bool {
            false
        }
    }

    fn wait_for(link: &Link, what: impl Fn(&Core) -> bool) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while !link.with_core(&what) {
            assert!(Instant::now() < deadline, "demorou demais");
            thread::sleep(Duration::from_millis(20));
        }
    }

    /// Um núcleo de mentira: confere o hello, dá as boas-vindas e pede uma fala.
    #[test]
    fn conversa_completa_com_um_nucleo_de_teste() {
        let server = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = server.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel::<String>();
        thread::spawn(move || {
            let (stream, _) = server.accept().unwrap();
            let mut ws = tungstenite::accept(stream).unwrap();
            let hello = ws.read().unwrap().to_string();
            tx.send(hello).unwrap();
            let welcome = r#"{"type":"welcome","paused":false,"nome":"Akemi","historico":[]}"#;
            ws.send(Message::Text(welcome.into())).unwrap();
            ws.send(Message::Text(r#"{"type":"say","id":3,"text":"Oi!"}"#.into()))
                .unwrap();
            loop {
                match ws.read() {
                    Ok(Message::Text(t)) => tx.send(t.to_string()).unwrap(),
                    Ok(_) => {}
                    Err(_) => break,
                }
            }
        });

        let quiet = Arc::new(Quiet(Mutex::new(Vec::new())));
        let link = Link::new(quiet.clone(), None, || {});
        let settings = Settings {
            url: format!("ws://127.0.0.1:{port}"),
            token: "x".repeat(20),
            ..Settings::default()
        };
        link.start(&settings);

        let hello: serde_json::Value =
            serde_json::from_str(&rx.recv_timeout(Duration::from_secs(10)).unwrap()).unwrap();
        assert_eq!(hello["type"], "hello");
        assert_eq!(hello["token"], "x".repeat(20));
        wait_for(&link, |c| c.connected());

        let spoken: serde_json::Value =
            serde_json::from_str(&rx.recv_timeout(Duration::from_secs(10)).unwrap()).unwrap();
        assert_eq!(spoken, serde_json::json!({"type": "spoken", "id": 3, "status": "done"}));
        assert_eq!(*lock(&quiet.0), vec!["Oi!".to_string()]);

        assert!(link.send_text("tudo bem?"));
        let msg: serde_json::Value = serde_json::from_str(&rx.recv_timeout(Duration::from_secs(10)).unwrap()).unwrap();
        assert_eq!(msg, serde_json::json!({"type": "owner_message", "text": "tudo bem?"}));
    }

    #[test]
    fn token_recusado_nao_fica_tentando() {
        let server = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = server.local_addr().unwrap().port();
        thread::spawn(move || {
            let (stream, _) = server.accept().unwrap();
            let mut ws = tungstenite::accept(stream).unwrap();
            let _ = ws.read();
            let frame = tungstenite::protocol::CloseFrame {
                code: CloseCode::from(4401),
                reason: "token inválido".into(),
            };
            let _ = ws.close(Some(frame));
            while ws.read().is_ok() {}
        });
        let link = Link::new(Arc::new(Quiet(Mutex::new(Vec::new()))), None, || {});
        link.start(&Settings {
            url: format!("ws://127.0.0.1:{port}"),
            token: "y".repeat(20),
            ..Settings::default()
        });
        wait_for(&link, |c| c.status == Status::TokenRecusado);
        assert!(link.with_core(|c| c.last_error.contains("AKEMI_TOKEN")));
    }

    /// Modo sozinha: sem núcleo, a mensagem vai para uma IA (de teste) e a conversa fica salva.
    #[test]
    fn sozinha_no_celular_com_uma_ia_de_teste() {
        use std::io::{BufRead, BufReader, Read, Write};
        let server = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = server.local_addr().unwrap().port();
        thread::spawn(move || {
            let (mut stream, _) = server.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let (mut len, mut line) = (0usize, String::new());
            loop {
                line.clear();
                reader.read_line(&mut line).unwrap();
                let l = line.trim().to_ascii_lowercase();
                if l.is_empty() {
                    break;
                }
                if let Some(v) = l.strip_prefix("content-length:") {
                    len = v.trim().parse().unwrap();
                }
            }
            let mut body = vec![0; len];
            reader.read_exact(&mut body).unwrap();
            let answer = r#"{"choices":[{"message":{"content":"Tô aqui, mesmo sem o PC."}}]}"#;
            let head = format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n", answer.len());
            stream.write_all(head.as_bytes()).unwrap();
            stream.write_all(answer.as_bytes()).unwrap();
        });

        let dir = std::env::temp_dir().join(format!("akemi-sozinha-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let quiet = Arc::new(Quiet(Mutex::new(Vec::new())));
        let link = Link::new(quiet.clone(), Some(dir.clone()), || {});
        link.start(&Settings {
            mode: crate::settings::Mode::Alone,
            provider: crate::settings::Provider::Other,
            brain_url: format!("http://127.0.0.1:{port}/v1"),
            brain_model: "teste".into(),
            ..Settings::default()
        });
        wait_for(&link, |c| c.connected() && c.local);
        assert!(link.send_text("cadê você?"));
        wait_for(&link, |c| c.items.len() == 2 && !c.thinking);
        assert_eq!(link.with_core(|c| c.items[1].text.clone()), "Tô aqui, mesmo sem o PC.");
        let deadline = Instant::now() + Duration::from_secs(5);
        while lock(&quiet.0).is_empty() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(*lock(&quiet.0), vec!["Tô aqui, mesmo sem o PC.".to_string()]);
        let saved = load_history(&dir.join("conversa-sozinha.json"));
        assert_eq!(saved.len(), 2);
        assert!(saved[0].mine);
        let _ = std::fs::remove_dir_all(dir);
    }
}
