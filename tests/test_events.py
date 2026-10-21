import pytest

from akemi.core.events import Event, EventBus, Trust
from akemi.core.pause import PauseSwitch


@pytest.mark.asyncio
async def test_bus_glob_dispatch_and_isolation():
    bus = EventBus()
    got = []

    async def ok(e):
        got.append(e.kind)

    def boom(e):
        raise RuntimeError("handler quebrado não derruba o resto")

    bus.subscribe("chat.*", ok)
    bus.subscribe("chat.*", boom)
    bus.subscribe("owner.message", ok)
    bus.start()
    bus.publish(Event("chat.batch"))
    bus.publish(Event("owner.message", trust=Trust.OWNER))
    bus.publish(Event("tick.idle"))
    await bus.drain()
    await bus.stop()
    assert sorted(got) == ["chat.batch", "owner.message"]


@pytest.mark.asyncio
async def test_bus_drops_oldest_when_full():
    bus = EventBus(max_queue=2)
    for i in range(3):
        bus.publish(Event(f"e{i}"))
    assert bus.dropped == 1


def test_pause_with_timeout(monkeypatch):
    import akemi.core.pause as pause_mod

    now = [100.0]
    monkeypatch.setattr(pause_mod.time, "monotonic", lambda: now[0])
    p = PauseSwitch()
    p.pause("teste", seconds=10)
    assert p.paused
    now[0] = 111.0
    assert not p.paused
