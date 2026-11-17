//! Painel da Akemi: uma janela para configurar tudo dela.
//!
//!     akemi-painel                 (acha a pasta da Akemi sozinho)
//!     akemi-painel C:\Akemi        (ou diga onde ela está)
//!
//! Edita o `.env` (sem estragar os comentários), o `data/painel.json`, a
//! personalidade, os gatilhos e o `mcp.json`. Veja docs/PAINEL_RUST.md.

#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod app;
mod envfile;
mod fetch;
mod project;
mod schema;

use std::path::PathBuf;

fn pick_root() -> PathBuf {
    if let Some(arg) = std::env::args_os().nth(1) {
        let path = PathBuf::from(arg);
        return project::find_root(&path).unwrap_or(path);
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if let Some(root) = project::find_root(&cwd) {
        return root;
    }
    // O .exe costuma ficar dentro da pasta da Akemi (ex.: rust-core/target/release).
    if let Some(root) = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().and_then(project::find_root))
    {
        return root;
    }
    cwd
}

fn main() -> eframe::Result<()> {
    if std::env::args().any(|a| a == "--help" || a == "-h") {
        println!("Uso: akemi-painel [pasta-da-akemi]\n\nAbre o painel para configurar a Akemi.");
        return Ok(());
    }
    let root = pick_root();
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_title("Painel da Akemi")
            .with_inner_size([1120.0, 760.0])
            .with_min_inner_size([860.0, 520.0]),
        ..Default::default()
    };
    eframe::run_native(
        "Painel da Akemi",
        options,
        Box::new(move |cc| Ok(Box::new(app::PainelApp::new(cc, root)))),
    )
}
