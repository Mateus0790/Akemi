"""Estado interno persistente — o que dá à Akemi sensação de continuidade.

Não é consciência, mas é o que a faz *parecer* contínua: ela lembra do humor,
do que estava pensando, do objetivo atual e das tarefas pendentes entre uma
interação e outra (e entre reinicializações).
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger()

MOODS = ("neutra", "animada", "curiosa", "focada", "entediada", "irritadinha", "sonolenta", "orgulhosa")


@dataclass
class Task:
    id: int
    description: str
    created_at: float = field(default_factory=time.time)
    done: bool = False


@dataclass
class InnerState:
    mood: str = "neutra"
    mood_intensity: float = 0.3  # 0..1, decai com o tempo
    current_goal: str = "acompanhar o que o Mateus está fazendo"
    tasks: list[Task] = field(default_factory=list)
    recent_thoughts: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    last_spoke_at: float = 0.0
    last_owner_interaction_at: float = 0.0
    last_updated_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------ humor
    def set_mood(self, mood: str, intensity: float = 0.6) -> None:
        mood = (mood or "").strip().lower()
        if mood not in MOODS:
            return
        self.mood = mood
        self.mood_intensity = max(0.0, min(1.0, intensity))
        self.touch()

    def decay(self, now: float | None = None, half_life_s: float = 1800.0) -> None:
        """Humor volta lentamente para "neutra" (meia-vida de 30 min)."""
        now = now or time.time()
        elapsed = max(0.0, now - self.last_updated_at)
        if elapsed <= 0:
            return
        self.mood_intensity *= 0.5 ** (elapsed / half_life_s)
        if self.mood_intensity < 0.1:
            self.mood, self.mood_intensity = "neutra", 0.3
        self.last_updated_at = now

    # ---------------------------------------------------------------- tarefas
    def add_task(self, description: str) -> Task:
        next_id = max((t.id for t in self.tasks), default=0) + 1
        task = Task(id=next_id, description=description.strip())
        self.tasks.append(task)
        self.touch()
        return task

    def complete_task(self, task_id: int) -> bool:
        for t in self.tasks:
            if t.id == task_id and not t.done:
                t.done = True
                self.touch()
                return True
        return False

    @property
    def open_tasks(self) -> list[Task]:
        return [t for t in self.tasks if not t.done]

    def remember_thought(self, thought: str) -> None:
        thought = thought.strip()
        if thought:
            self.recent_thoughts.append(thought)
            self.touch()

    def touch(self) -> None:
        self.last_updated_at = time.time()

    # ------------------------------------------------------------------ prompt
    def describe(self, now: float | None = None) -> str:
        now = now or time.time()
        lines = [
            f"Seu humor agora: {self.mood} (intensidade {self.mood_intensity:.1f}).",
            f"Seu objetivo atual: {self.current_goal}.",
        ]
        if self.open_tasks:
            lines.append(
                "Tarefas pendentes: "
                + "; ".join(f"#{t.id} {t.description}" for t in self.open_tasks[:5])
            )
        if self.recent_thoughts:
            lines.append("Seus últimos pensamentos: " + " | ".join(list(self.recent_thoughts)[-3:]))
        if self.last_owner_interaction_at:
            mins = int((now - self.last_owner_interaction_at) / 60)
            lines.append(f"Última conversa com o dono: há {mins} min.")
        return "\n".join(lines)

    # ---------------------------------------------------------- persistência
    def to_dict(self) -> dict:
        d = asdict(self)
        d["recent_thoughts"] = list(self.recent_thoughts)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "InnerState":
        d = dict(d)
        d["tasks"] = [Task(**t) for t in d.get("tasks", [])]
        d["recent_thoughts"] = deque(d.get("recent_thoughts", []), maxlen=8)
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "InnerState":
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return cls()
        except (ValueError, TypeError) as exc:
            logger.warning("state file corrupted, starting fresh", error=str(exc))
            return cls()
