//! App da Akemi para celular, em Rust.
//!
//! O chip (memória, jeito, estado) continua no PC ou no servidor. O celular só
//! empresta a tela, a voz e as notificações dele. Ver docs/CELULAR_RUST.md.

pub mod app;
pub mod brain;
pub mod core;
pub mod link;
pub mod settings;

#[cfg(target_os = "android")]
mod android;
#[cfg(target_os = "android")]
mod droid;
