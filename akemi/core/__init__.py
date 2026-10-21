"""Core runtime: configuração, eventos, pausa e orquestração."""

from .events import Event, EventBus, Priority, Trust
from .pause import PauseSwitch

__all__ = ["Event", "EventBus", "PauseSwitch", "Priority", "Trust"]
