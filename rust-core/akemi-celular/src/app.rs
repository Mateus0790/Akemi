//! Liga a tela (ui/app.slint) à ligação com o núcleo.

use std::path::PathBuf;
use std::rc::Rc;
use std::sync::Arc;

use slint::{ComponentHandle, Model, ModelRc, SharedString, VecModel};

use crate::core::Status;
use crate::link::{Link, Platform};
use crate::settings::Settings;

slint::include_modules!();

/// Abre a tela e roda até o app fechar.
pub fn run(platform: Arc<dyn Platform>, data_dir: PathBuf) -> Result<(), slint::PlatformError> {
    let ui = AppWindow::new()?;
    let settings = Rc::new(std::cell::RefCell::new(Settings::load(&data_dir)));
    let rows: Rc<VecModel<ChatRow>> = Rc::new(VecModel::default());
    ui.set_rows(ModelRc::from(rows.clone()));

    // Toda mudança na ligação (de qualquer linha) redesenha a tela na linha da tela.
    let weak = ui.as_weak();
    let link = Link::new(platform, move || {
        let weak = weak.clone();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = weak.upgrade() {
                LINK.with(|l| {
                    if let Some(link) = l.get() {
                        refresh(&ui, link);
                    }
                });
            }
        });
    });
    LINK.with(|l| {
        let _ = l.set(link.clone());
    });

    fill_settings(&ui, &settings.borrow());
    ui.set_muted(settings.borrow().muted);
    if settings.borrow().complete() {
        link.start(&settings.borrow());
    } else {
        ui.set_show_settings(true);
    }

    ui.on_send({
        let (link, weak) = (link.clone(), ui.as_weak());
        move |text| {
            if link.send_text(&text) {
                if let Some(ui) = weak.upgrade() {
                    ui.set_draft(SharedString::new());
                }
            }
        }
    });
    ui.on_typing({
        let link = link.clone();
        move |_| link.interrupt_if_speaking()
    });
    ui.on_toggle_pause({
        let link = link.clone();
        move || link.toggle_pause()
    });
    ui.on_toggle_mute({
        let (link, weak, settings, dir) = (link.clone(), ui.as_weak(), settings.clone(), data_dir.clone());
        move || {
            let mut s = settings.borrow_mut();
            s.muted = !s.muted;
            link.set_muted(s.muted);
            let _ = s.save(&dir);
            if let Some(ui) = weak.upgrade() {
                ui.set_muted(s.muted);
            }
        }
    });
    ui.on_save_settings({
        let (link, weak, settings, dir) = (link.clone(), ui.as_weak(), settings.clone(), data_dir.clone());
        move |url, token, name| {
            let Some(ui) = weak.upgrade() else { return };
            let new = match settings.borrow().validated(&url, &token, &name) {
                Ok(new) => new,
                Err(msg) => {
                    ui.set_settings_error(msg.into());
                    return;
                }
            };
            if let Err(e) = new.save(&dir) {
                ui.set_settings_error(format!("Não consegui salvar: {e}").into());
                return;
            }
            *settings.borrow_mut() = new.clone();
            fill_settings(&ui, &new);
            ui.set_settings_error(SharedString::new());
            ui.set_show_settings(false);
            link.start(&new);
        }
    });

    refresh(&ui, &link);
    ui.run()
}

thread_local! {
    static LINK: std::cell::OnceCell<Link> = const { std::cell::OnceCell::new() };
}

/// O app voltou para a frente (chamado pelo Android).
pub fn resumed() {
    LINK.with(|l| {
        if let Some(link) = l.get() {
            link.wake_up();
        }
    });
}

fn fill_settings(ui: &AppWindow, s: &Settings) {
    ui.set_cfg_url(s.url.as_str().into());
    ui.set_cfg_token(s.token.as_str().into());
    ui.set_cfg_name(s.name.as_str().into());
    ui.set_cfg_complete(s.complete());
}

fn refresh(ui: &AppWindow, link: &Link) {
    let grew = link.with_core(|c| {
        ui.set_akemi_name(c.akemi_name.as_str().into());
        ui.set_status_text(c.status_text().into());
        ui.set_status_level(match c.status {
            Status::Conectado => 2,
            Status::Conectando => 1,
            _ => 0,
        });
        ui.set_connected(c.connected());
        ui.set_paused(c.paused);
        ui.set_speaking(c.speaking());
        ui.set_banner(if c.connected() {
            SharedString::new()
        } else {
            c.last_error.as_str().into()
        });

        let model = ui.get_rows();
        let rows = model.as_any().downcast_ref::<VecModel<ChatRow>>().expect("VecModel");
        let same = rows.row_count() == c.items.len()
            && c.items.last().map(|i| i.text.as_str()) == rows.row_data_last().map(|r| r.text.to_string()).as_deref();
        if same {
            return false;
        }
        rows.set_vec(
            c.items
                .iter()
                .map(|i| ChatRow {
                    mine: i.mine,
                    text: i.text.as_str().into(),
                    elsewhere: i.elsewhere,
                })
                .collect::<Vec<_>>(),
        );
        true
    });
    if grew {
        // Espera o layout medir as mensagens novas antes de rolar.
        let weak = ui.as_weak();
        slint::Timer::single_shot(std::time::Duration::from_millis(30), move || {
            if let Some(ui) = weak.upgrade() {
                ui.invoke_scroll_to_end();
            }
        });
    }
}

trait LastRow {
    fn row_data_last(&self) -> Option<ChatRow>;
}

impl LastRow for VecModel<ChatRow> {
    fn row_data_last(&self) -> Option<ChatRow> {
        self.row_count().checked_sub(1).and_then(|i| self.row_data(i))
    }
}
