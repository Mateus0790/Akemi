//! Entrada do app no Android (a NativeActivity chama `android_main`).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use slint::android::android_activity::{MainEvent, PollEvent};
use slint::android::AndroidApp;

use crate::droid::Droid;

#[no_mangle]
fn android_main(app: AndroidApp) {
    // SAFETY: os ponteiros vêm do próprio android-activity e valem enquanto o app existe.
    let Some(droid) = (unsafe { Droid::new(app.vm_as_ptr(), app.activity_as_ptr()) }) else {
        eprintln!("akemi-celular: não consegui falar com o Java do Android");
        return;
    };
    let droid = Arc::new(droid);
    let dir = app
        .internal_data_path()
        .unwrap_or_else(|| PathBuf::from("/data/local/tmp/akemi"));

    let asked = AtomicBool::new(false);
    let events = droid.clone();
    slint::android::init_with_event_listener(app, move |event| match event {
        PollEvent::Main(MainEvent::Resume { .. }) => {
            events.set_background(false);
            crate::app::resumed();
            if !asked.swap(true, Ordering::Relaxed) {
                events.ask_notification_permission();
            }
        }
        PollEvent::Main(MainEvent::Start) => events.set_background(false),
        PollEvent::Main(MainEvent::Stop) => events.set_background(true),
        _ => {}
    })
    .expect("slint android");

    if let Err(e) = crate::app::run(droid, dir) {
        eprintln!("akemi-celular: {e}");
    }
}
