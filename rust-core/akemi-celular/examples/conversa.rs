//! Uma conversa de ponta a ponta, sem tela: usado por tests/nucleo_de_teste.py
//! para conferir o app contra o servidor de verdade (akemi/net/companion.py).
//!
//!     conversa ws://127.0.0.1:8799 TOKEN

use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use akemi_celular::link::{Link, Platform};
use akemi_celular::settings::Settings;

#[derive(Default)]
struct Recorder(Mutex<Vec<String>>);

impl Platform for Recorder {
    fn speak(&self, text: &str) -> bool {
        self.0.lock().unwrap().push(text.to_string());
        true
    }
    fn stop_speaking(&self) {}
    fn notify(&self, _: &str, _: &str) {}
    fn in_background(&self) -> bool {
        false
    }
}

fn wait(what: &str, cond: impl Fn() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(15);
    while !cond() {
        if Instant::now() > deadline {
            eprintln!("FALHOU: esperando {what}");
            std::process::exit(1);
        }
        thread::sleep(Duration::from_millis(50));
    }
    println!("ok: {what}");
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (url, token) = (args[1].clone(), args[2].clone());
    let rec = Arc::new(Recorder::default());
    let link = Link::new(rec.clone(), None, || {});
    link.start(&Settings {
        url,
        token,
        name: "teste".into(),
        ..Settings::default()
    });

    wait("boas-vindas com histórico", || {
        link.with_core(|c| c.connected() && !c.items.is_empty())
    });
    assert!(link.send_text("oi do teste"));
    wait("a resposta falada", || {
        rec.0.lock().unwrap().iter().any(|t| t.contains("oi do teste"))
    });
    wait("terminar de falar", || link.with_core(|c| !c.speaking()));
    wait("a fala do PC", || {
        link.with_core(|c| c.items.iter().any(|i| i.elsewhere))
    });
    link.toggle_pause();
    wait("pausar", || link.with_core(|c| c.paused));
    thread::sleep(Duration::from_millis(500)); // dá tempo do `spoken` sair
}
