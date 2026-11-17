//! No PC: abre a mesma tela do celular numa janela, para testar sem o Android.
//! (Sem voz e sem notificação: ela só escreve.)

use std::sync::Arc;

use akemi_celular::link::Platform;

struct Desktop;

impl Platform for Desktop {
    fn speak(&self, _text: &str) -> bool {
        true
    }
    fn stop_speaking(&self) {}
    fn notify(&self, _title: &str, _body: &str) {}
    fn in_background(&self) -> bool {
        false
    }
}

fn main() -> Result<(), slint::PlatformError> {
    let dir = std::env::var_os("AKEMI_CELULAR_DIR")
        .map(Into::into)
        .unwrap_or_else(|| std::env::temp_dir().join("akemi-celular"));
    akemi_celular::app::run(Arc::new(Desktop), dir)
}
