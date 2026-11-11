"""A janela do avatar: sem borda, sempre por cima e transparente (no Windows).

O Tk roda numa thread só dele; o resto da Akemi (asyncio) conversa com ele por
uma fila (:meth:`AvatarWindow.post`). Nada do Tk é tocado fora dessa thread.

Com o mouse:
- arrastar: leva ela para onde você quiser (ela fica ali até "Sentar na janela")
- passar o mouse indo e voltando em cima dela: carinho
- botão direito: menu (sentar na janela, dormir, pausar, esconder, fechar)
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from . import perch
from .draw import Painter, canvas_size, load_skin
from .model import AvatarModel

logger = structlog.get_logger()

KEY = "#ff00fe"  # cor que o Windows deixa transparente (e clicável através)
FPS = 30
PERCH_EVERY_S = 0.5


class AvatarWindow:
    def __init__(
        self,
        *,
        scale: float = 1.0,
        skin: Path | None = None,
        perch_on_windows: bool = True,
        sleep_after_s: float = 300.0,
        on_pat: Callable[[], None] | None = None,
        on_menu: Callable[[str], None] | None = None,
        mood: Callable[[], tuple[str, float]] | None = None,
    ) -> None:
        self.scale = max(0.5, min(3.0, scale))
        self.skin_dir = skin
        self.perch_enabled = perch_on_windows
        self.model = AvatarModel(sleep_after_s=sleep_after_s)
        self.on_pat = on_pat
        self.on_menu = on_menu
        self.mood = mood
        self._inbox: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.error: str | None = None
        self.closed = threading.Event()
        self.visible = True
        self.pinned = False  # largada pelo dono num lugar: não sai andando
        self._drag: tuple[int, int] | None = None
        self._dragged = False
        self._own: set[int] = set()

    # ----------------------------------------------------- lado do asyncio
    def start(self, timeout_s: float = 5.0) -> bool:
        """Abre a janela. Devolve False (com ``error``) se não deu (sem tkinter, sem tela)."""
        self._thread = threading.Thread(target=self._main, name="akemi-avatar", daemon=True)
        self._thread.start()
        self._ready.wait(timeout_s)
        return self.error is None and self._ready.is_set()

    def post(self, kind: str, payload: dict | None = None) -> None:
        """Manda um evento para o avatar. Pode chamar de qualquer thread."""
        if not self.closed.is_set():
            self._inbox.put((kind, payload or {}))

    def stop(self) -> None:
        self.post("_close")
        if self._thread is not None:
            self._thread.join(2.0)

    # ------------------------------------------------------ thread do Tk
    def _main(self) -> None:
        try:
            import tkinter as tk
            from tkinter import font as tkfont
        except ImportError:
            self.error = "o Python não tem tkinter"
            self._ready.set()
            return
        try:
            root = tk.Tk()
        except Exception as exc:  # sem tela (servidor, SSH)
            self.error = f"não consegui abrir a janela: {exc}"
            self._ready.set()
            return
        self.root = root
        windows = sys.platform == "win32"
        bg = KEY if windows else "#1c1033"
        root.title("Akemi")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        if windows:
            root.attributes("-transparentcolor", KEY)
        w, h = canvas_size(self.scale)
        self.size = (w, h)
        canvas = tk.Canvas(root, width=w, height=h, bg=bg, highlightthickness=0, bd=0)
        canvas.pack()
        self.canvas = canvas
        family = "Segoe UI" if windows else "DejaVu Sans"
        font = tkfont.Font(root=root, family=family, size=max(7, round(10 * self.scale)))
        small = tkfont.Font(root=root, family=family, size=max(6, round(8 * self.scale)), weight="bold")
        skin = None
        if self.skin_dir is not None:
            try:
                skin = load_skin(self.skin_dir, tk.PhotoImage, scale=self.scale)
            except (OSError, FileNotFoundError, tk.TclError) as exc:
                logger.warning("avatar skin not loaded, using the drawing", error=str(exc))
        self.painter = Painter(canvas, self.scale, bg=bg, skin=skin, font=font, small_font=small,
                               measure=lambda text, f: f.measure(text))
        self.work = perch.work_area() or perch.Rect(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        start = perch.perch_spot(None, self.work, self.size)
        self.pos = (start.x, start.y)
        root.geometry(f"{w}x{h}+{self.pos[0]}+{self.pos[1]}")
        root.update_idletasks()
        try:
            self._own = {int(root.wm_frame(), 16), root.winfo_id()}
        except (ValueError, tk.TclError):
            self._own = {root.winfo_id()}
        self._menu = self._build_menu(tk)
        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._motion_drag)
        canvas.bind("<ButtonRelease-1>", self._release)
        canvas.bind("<Motion>", self._hover)
        canvas.bind("<Leave>", lambda e: self.model.pointer(0, False))
        canvas.bind("<Button-3>", self._popup)
        self._perch_at = 0.0
        self._mood_at = 0.0
        self._ready.set()
        root.after(10, self._tick)
        try:
            root.mainloop()
        finally:
            # O Tk só pode ser desmontado na thread dele: solta tudo aqui mesmo.
            self.root = self.canvas = self.painter = self._menu = None  # type: ignore[assignment]
            del root, canvas, font, small, skin
            import gc

            gc.collect()
            self.closed.set()

    def _build_menu(self, tk) -> Any:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Sentar na janela", command=self._perch_again)
        menu.add_command(label="Dormir / acordar", command=self._toggle_sleep)
        menu.add_command(label="Pausar / voltar", command=lambda: self._menu_event("pausar"))
        menu.add_separator()
        menu.add_command(label="Esconder por 1 minuto", command=self._hide_a_bit)
        menu.add_command(label="Fechar o avatar", command=lambda: self.post("_close"))
        return menu

    def _menu_event(self, what: str) -> None:
        if self.on_menu is not None:
            try:
                self.on_menu(what)
            except Exception:
                logger.exception("avatar menu failed")

    def _perch_again(self) -> None:
        self.pinned = False
        self.perch_enabled = True
        self._perch_at = 0.0

    def _toggle_sleep(self) -> None:
        self.model.sleep(not self.model.forced_sleep)

    def _hide_a_bit(self) -> None:
        self.root.withdraw()
        self.visible = False
        self.root.after(60_000, self._show)

    def _show(self) -> None:
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.visible = True

    def _popup(self, event) -> None:
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _press(self, event) -> None:
        self._drag = (event.x_root - self.pos[0], event.y_root - self.pos[1])
        self._dragged = False

    def _motion_drag(self, event) -> None:
        if self._drag is None:
            return
        self._dragged = True
        self.pos = (event.x_root - self._drag[0], event.y_root - self._drag[1])
        self.root.geometry(f"+{self.pos[0]}+{self.pos[1]}")
        self.model.on("drag")

    def _release(self, event) -> None:
        if self._dragged:
            self.pinned = True  # fica onde você largou
        self._drag = None

    def _hover(self, event) -> None:
        if self._drag is not None:
            return
        if self.model.pointer(event.x_root, True) and self.on_pat is not None:
            try:
                self.on_pat()
            except Exception:
                logger.exception("avatar pat callback failed")

    def _drain(self) -> bool:
        while True:
            try:
                kind, payload = self._inbox.get_nowait()
            except queue.Empty:
                return True
            if kind == "_close":
                self.root.destroy()
                return False
            if kind == "_sleep":
                self.model.sleep(bool(payload.get("on", True)))
            else:
                self.model.on(kind, payload)

    def _place(self, now: float) -> None:
        if self.pinned or self._drag is not None or not self.perch_enabled or now - self._perch_at < PERCH_EVERY_S:
            return
        self._perch_at = now
        target = perch.perch_spot(perch.foreground_window(self._own), self.work, self.size)
        self._target = (target.x, target.y)

    def _walk(self) -> None:
        target = getattr(self, "_target", None)
        if target is None or self._drag is not None or self.pinned:
            return
        new, teleported = perch.step_towards(self.pos, target)
        if new != self.pos:
            if teleported:
                self.model.glitch(0.6)
            self.pos = new
            self.root.geometry(f"+{new[0]}+{new[1]}")

    def _tick(self) -> None:
        if not self._drain():
            return
        now = time.monotonic()
        if self.mood is not None and now - self._mood_at >= 1.0:
            self._mood_at = now
            try:
                mood, intensity = self.mood()
                self.model.on("mood", {"mood": mood, "intensity": intensity})
            except Exception:
                pass
        self._place(now)
        self._walk()
        if self.visible:
            try:
                self.painter.draw(self.model.look())
            except Exception:
                logger.exception("avatar draw failed")
        self.root.after(1000 // FPS, self._tick)
