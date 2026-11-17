//! Voz e notificações do Android, chamando o Java do sistema por JNI.
//!
//! Só usa classes do próprio Android (TextToSpeech, NotificationManager), sem
//! código Java nosso. Tudo o que der errado aqui vira "sem voz" ou "sem
//! notificação": o chat continua funcionando.

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use jni::objects::{GlobalRef, JObject, JValue};
use jni::{JNIEnv, JavaVM};

use crate::link::Platform;

const CHANNEL_ID: &str = "akemi_falas";
const QUEUE_FLUSH: i32 = 0;
const IMPORTANCE_HIGH: i32 = 4;
const FLAG_ACTIVITY_NEW_TASK: i32 = 0x1000_0000;
const FLAG_ACTIVITY_SINGLE_TOP: i32 = 0x2000_0000;
const FLAG_UPDATE_CURRENT: i32 = 0x0800_0000;
const FLAG_IMMUTABLE: i32 = 0x0400_0000;
const MAX_SPEECH: usize = 3900;
const AKEMI_PINK: i32 = 0xFFFC_3B7F_u32 as i32;

pub struct Droid {
    vm: JavaVM,
    activity: GlobalRef,
    tts: Mutex<Option<GlobalRef>>,
    stop: AtomicBool,
    background: AtomicBool,
    channel_ready: AtomicBool,
    next_id: AtomicI32,
}

type JResult<T> = jni::errors::Result<T>;

impl Droid {
    /// # Safety
    /// `vm` e `activity` precisam ser os ponteiros que o android-activity entrega
    /// (`AndroidApp::vm_as_ptr` e `AndroidApp::activity_as_ptr`).
    pub unsafe fn new(vm: *mut c_void, activity: *mut c_void) -> Option<Self> {
        let vm = JavaVM::from_raw(vm.cast()).ok()?;
        let activity = {
            let env = vm.attach_current_thread_permanently().ok()?;
            env.new_global_ref(JObject::from_raw(activity.cast())).ok()?
        };
        let droid = Self {
            vm,
            activity,
            tts: Mutex::new(None),
            stop: AtomicBool::new(false),
            background: AtomicBool::new(false),
            channel_ready: AtomicBool::new(false),
            next_id: AtomicI32::new(0),
        };
        // Liga a voz já: o Android leva um tempinho para carregar o motor.
        droid.tts();
        Some(droid)
    }

    pub fn set_background(&self, background: bool) {
        self.background.store(background, Ordering::Relaxed);
    }

    /// Roda `f` com o Java desta linha, num quadro próprio (as referências
    /// locais são soltas no fim). Erro do Java vira `None` e vai para o log.
    fn java<R>(&self, what: &str, f: impl FnOnce(&mut JNIEnv) -> JResult<R>) -> Option<R> {
        let mut env = self.vm.attach_current_thread_permanently().ok()?;
        let result = env.with_local_frame(32, |env| f(env));
        match result {
            Ok(r) => Some(r),
            Err(e) => {
                if env.exception_check().unwrap_or(false) {
                    let _ = env.exception_describe();
                    let _ = env.exception_clear();
                }
                eprintln!("akemi-celular: {what}: {e}");
                None
            }
        }
    }

    fn lock_tts(&self) -> std::sync::MutexGuard<'_, Option<GlobalRef>> {
        self.tts.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// O motor de voz do sistema (criado uma vez).
    fn tts(&self) -> Option<GlobalRef> {
        let mut slot = self.lock_tts();
        if slot.is_none() {
            *slot = self.java("criar a voz", |env| {
                let tts = env.new_object(
                    "android/speech/tts/TextToSpeech",
                    "(Landroid/content/Context;Landroid/speech/tts/TextToSpeech$OnInitListener;)V",
                    &[JValue::Object(self.activity.as_obj()), JValue::Object(&JObject::null())],
                )?;
                env.new_global_ref(tts)
            });
        }
        slot.clone()
    }

    /// Pede para falar. `Some(true)`: começou. `Some(false)`: o motor ainda não ficou pronto.
    fn start_speaking(&self, tts: &GlobalRef, text: &str) -> Option<bool> {
        self.java("falar", |env| {
            let tag = env.new_string("pt-BR")?;
            let locale = env
                .call_static_method(
                    "java/util/Locale",
                    "forLanguageTag",
                    "(Ljava/lang/String;)Ljava/util/Locale;",
                    &[JValue::Object(&tag)],
                )?
                .l()?;
            // Sem a voz em português instalada, fala com a voz padrão mesmo.
            env.call_method(
                tts.as_obj(),
                "setLanguage",
                "(Ljava/util/Locale;)I",
                &[JValue::Object(&locale)],
            )?;
            let jtext = env.new_string(text)?;
            let id = env.new_string("akemi")?;
            let r = env
                .call_method(
                    tts.as_obj(),
                    "speak",
                    "(Ljava/lang/CharSequence;ILandroid/os/Bundle;Ljava/lang/String;)I",
                    &[
                        JValue::Object(&jtext),
                        JValue::Int(QUEUE_FLUSH),
                        JValue::Object(&JObject::null()),
                        JValue::Object(&id),
                    ],
                )?
                .i()?;
            Ok(r == 0)
        })
    }

    fn is_speaking(&self, tts: &GlobalRef) -> bool {
        self.java("ver se fala", |env| {
            env.call_method(tts.as_obj(), "isSpeaking", "()Z", &[])?.z()
        })
        .unwrap_or(false)
    }

    /// Pede a permissão de notificação (Android 13+). Chamar com o app na frente.
    pub fn ask_notification_permission(&self) {
        self.java("pedir permissão", |env| {
            if sdk(env)? < 33 {
                return Ok(());
            }
            let ctx = self.activity.as_obj();
            let perm = env.new_string("android.permission.POST_NOTIFICATIONS")?;
            let granted = env
                .call_method(
                    ctx,
                    "checkSelfPermission",
                    "(Ljava/lang/String;)I",
                    &[JValue::Object(&perm)],
                )?
                .i()?;
            if granted != 0 {
                let arr = env.new_object_array(1, "java/lang/String", &perm)?;
                env.call_method(
                    ctx,
                    "requestPermissions",
                    "([Ljava/lang/String;I)V",
                    &[JValue::Object(&arr), JValue::Int(1)],
                )?;
            }
            Ok(())
        });
    }

    fn post_notification(&self, env: &mut JNIEnv, title: &str, body: &str) -> JResult<()> {
        let ctx = self.activity.as_obj();
        let service = env.new_string("notification")?;
        let nm = env
            .call_method(
                ctx,
                "getSystemService",
                "(Ljava/lang/String;)Ljava/lang/Object;",
                &[JValue::Object(&service)],
            )?
            .l()?;
        let channel_id = env.new_string(CHANNEL_ID)?;
        if !self.channel_ready.load(Ordering::Relaxed) {
            let name = env.new_string("Falas da Akemi")?;
            let channel = env.new_object(
                "android/app/NotificationChannel",
                "(Ljava/lang/String;Ljava/lang/CharSequence;I)V",
                &[
                    JValue::Object(&channel_id),
                    JValue::Object(&name),
                    JValue::Int(IMPORTANCE_HIGH),
                ],
            )?;
            let desc = env.new_string("O que a Akemi diz enquanto o app está em segundo plano")?;
            env.call_method(
                &channel,
                "setDescription",
                "(Ljava/lang/String;)V",
                &[JValue::Object(&desc)],
            )?;
            env.call_method(
                &nm,
                "createNotificationChannel",
                "(Landroid/app/NotificationChannel;)V",
                &[JValue::Object(&channel)],
            )?;
            self.channel_ready.store(true, Ordering::Relaxed);
        }

        // Tocar na notificação abre o app (o mesmo, não um novo).
        let pm = env
            .call_method(ctx, "getPackageManager", "()Landroid/content/pm/PackageManager;", &[])?
            .l()?;
        let pkg = env
            .call_method(ctx, "getPackageName", "()Ljava/lang/String;", &[])?
            .l()?;
        let intent = env
            .call_method(
                &pm,
                "getLaunchIntentForPackage",
                "(Ljava/lang/String;)Landroid/content/Intent;",
                &[JValue::Object(&pkg)],
            )?
            .l()?;

        const B: &str = "Landroid/app/Notification$Builder;";
        let builder = env.new_object(
            "android/app/Notification$Builder",
            "(Landroid/content/Context;Ljava/lang/String;)V",
            &[JValue::Object(ctx), JValue::Object(&channel_id)],
        )?;
        let icon = env
            .get_static_field("android/R$drawable", "stat_notify_chat", "I")?
            .i()?;
        env.call_method(&builder, "setSmallIcon", format!("(I){B}"), &[JValue::Int(icon)])?;
        env.call_method(&builder, "setColor", format!("(I){B}"), &[JValue::Int(AKEMI_PINK)])?;
        let jtitle = env.new_string(title)?;
        env.call_method(
            &builder,
            "setContentTitle",
            format!("(Ljava/lang/CharSequence;){B}"),
            &[JValue::Object(&jtitle)],
        )?;
        let jbody = env.new_string(body)?;
        env.call_method(
            &builder,
            "setContentText",
            format!("(Ljava/lang/CharSequence;){B}"),
            &[JValue::Object(&jbody)],
        )?;
        let style = env.new_object("android/app/Notification$BigTextStyle", "()V", &[])?;
        env.call_method(
            &style,
            "bigText",
            "(Ljava/lang/CharSequence;)Landroid/app/Notification$BigTextStyle;",
            &[JValue::Object(&jbody)],
        )?;
        env.call_method(
            &builder,
            "setStyle",
            format!("(Landroid/app/Notification$Style;){B}"),
            &[JValue::Object(&style)],
        )?;
        env.call_method(&builder, "setAutoCancel", format!("(Z){B}"), &[JValue::Bool(1)])?;
        if !intent.is_null() {
            env.call_method(
                &intent,
                "addFlags",
                "(I)Landroid/content/Intent;",
                &[JValue::Int(FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_SINGLE_TOP)],
            )?;
            let pending = env
                .call_static_method(
                    "android/app/PendingIntent",
                    "getActivity",
                    "(Landroid/content/Context;ILandroid/content/Intent;I)Landroid/app/PendingIntent;",
                    &[
                        JValue::Object(ctx),
                        JValue::Int(0),
                        JValue::Object(&intent),
                        JValue::Int(FLAG_UPDATE_CURRENT | FLAG_IMMUTABLE),
                    ],
                )?
                .l()?;
            env.call_method(
                &builder,
                "setContentIntent",
                format!("(Landroid/app/PendingIntent;){B}"),
                &[JValue::Object(&pending)],
            )?;
        }
        let notification = env
            .call_method(&builder, "build", "()Landroid/app/Notification;", &[])?
            .l()?;
        let id = self.next_id.fetch_add(1, Ordering::Relaxed) % 50 + 1;
        env.call_method(
            &nm,
            "notify",
            "(ILandroid/app/Notification;)V",
            &[JValue::Int(id), JValue::Object(&notification)],
        )?;
        Ok(())
    }
}

fn sdk(env: &mut JNIEnv) -> JResult<i32> {
    env.get_static_field("android/os/Build$VERSION", "SDK_INT", "I")?.i()
}

fn shorten(text: &str) -> &str {
    match text.char_indices().nth(MAX_SPEECH) {
        Some((i, _)) => &text[..i],
        None => text,
    }
}

impl Platform for Droid {
    fn speak(&self, text: &str) -> bool {
        self.stop.store(false, Ordering::Relaxed);
        let Some(tts) = self.tts() else { return false };
        let text = shorten(text);

        // Na primeira fala o motor pode ainda estar carregando: tenta por uns segundos.
        let asked = Instant::now();
        loop {
            match self.start_speaking(&tts, text) {
                Some(true) => break,
                Some(false) if asked.elapsed() < Duration::from_secs(6) => {}
                _ => return false,
            }
            if self.stop.load(Ordering::Relaxed) {
                return true;
            }
            thread::sleep(Duration::from_millis(250));
        }

        // Espera começar (até 3 s) e depois terminar, ou alguém mandar parar.
        let started = Instant::now();
        let mut heard = false;
        loop {
            if self.stop.load(Ordering::Relaxed) {
                return true;
            }
            if self.is_speaking(&tts) {
                heard = true;
            } else if heard || started.elapsed() > Duration::from_secs(3) {
                return true;
            }
            thread::sleep(Duration::from_millis(100));
        }
    }

    fn stop_speaking(&self) {
        self.stop.store(true, Ordering::Relaxed);
        let tts = self.lock_tts().clone();
        if let Some(tts) = tts {
            self.java("parar a voz", |env| {
                env.call_method(tts.as_obj(), "stop", "()I", &[]).map(|_| ())
            });
        }
    }

    fn notify(&self, title: &str, body: &str) {
        self.java("notificar", |env| self.post_notification(env, title, body));
    }

    fn in_background(&self) -> bool {
        self.background.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn corta_texto_longo_sem_quebrar_acento() {
        let long = "é".repeat(5000);
        assert_eq!(super::shorten(&long).chars().count(), super::MAX_SPEECH);
        assert_eq!(super::shorten("oi"), "oi");
    }
}
