"""Conversa do app em Rust com o servidor de verdade do celular (akemi/net/companion.py).

A Akemi aqui é de mentira (sem IA), mas o servidor, o protocolo e o token são
os de verdade. Roda o exemplo `conversa` e confere os dois lados.

    cargo build --manifest-path rust-core/Cargo.toml -p akemi-celular --example conversa
    python rust-core/akemi-celular/tests/nucleo_de_teste.py
"""

import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from akemi.net.companion import CompanionHub  # noqa: E402

TOKEN = "token-de-teste-123456"
EXE = ROOT / "rust-core" / "target" / "debug" / "examples" / ("conversa.exe" if sys.platform == "win32" else "conversa")


class Bus:
    def subscribe(self, name, fn):
        pass

    def publish(self, event):
        pass


class Pause:
    paused = False

    def pause(self, src):
        self.paused = True

    def resume(self):
        self.paused = False


async def main() -> int:
    said: list[str] = []
    statuses: list[str] = []
    akemi = types.SimpleNamespace(
        bus=Bus(),
        pause=Pause(),
        settings=types.SimpleNamespace(owner_name="Dono"),
        mind=types.SimpleNamespace(
            persona=types.SimpleNamespace(name="Akemi"),
            ambient=[],
            history=[types.SimpleNamespace(role="assistant", content="Oi! Sumiu, hein.")],
        ),
    )
    hub = CompanionHub(akemi, TOKEN)

    def owner_say(text, source):
        said.append(text)

        async def reply():
            fut = await hub.link.say(f"Recebi: {text}")
            statuses.append(await asyncio.wait_for(fut, 10))
            await hub.link.send("fala", text="Lembrete: beber água")

        asyncio.get_running_loop().create_task(reply())

    akemi.owner_say = owner_say
    server = await hub.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    proc = await asyncio.create_subprocess_exec(str(EXE), f"ws://127.0.0.1:{port}", TOKEN)
    code = await asyncio.wait_for(proc.wait(), 60)
    server.close()

    ok = code == 0 and said == ["oi do teste"] and statuses == ["done"] and akemi.pause.paused
    print(f"app saiu com {code}; o núcleo ouviu {said}; spoken={statuses}; pausada={akemi.pause.paused}")
    print("PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
