"""Estado interno persistente — o que dá à Akemi sensação de continuidade.

Não é consciência, mas é o que a faz *parecer* contínua: ela lembra do humor,
do que estava pensando, do objetivo atual e das tarefas pendentes entre uma
interação e outra (e entre reinicializações).
"""

from __future__ import annotations

import difflib
import json
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger()

MOODS = ("neutra", "animada", "curiosa", "focada", "entediada", "irritadinha", "sonolenta", "orgulhosa", "triste")
MAX_THOUGHT_CHARS = 240
MAX_WILL_CHARS = 160
MAX_INTERESTS = 8
MAX_OPINIONS = 8
SENSE_LABEL = {
    "voz": "voz",
    "microfone": "ouve o dono",
    "tela": "vê a tela",
    "audio_pc": "ouve o áudio do PC",
    "acoes": "mexe no PC",
    "chamada": "pode falar numa call de voz",
}


def is_repeated(thought: str, previous, threshold: float = 0.55) -> bool:
    """Parecido demais com algum pensamento anterior (mesmas palavras, outra ordem).

    Usa a sobreposição sobre o menor dos dois: reformulações do mesmo
    pensamento ficaram >= 0,58 nos dados reais; pensamentos diferentes, <= 0,44.
    """
    words = set(re.findall(r"\w{3,}", thought.lower()))
    if not words:
        return False
    for old in previous:
        old_words = set(re.findall(r"\w{3,}", old.lower()))
        if old_words and len(words & old_words) / min(len(words), len(old_words)) >= threshold:
            return True
        if difflib.SequenceMatcher(None, thought.lower(), old.lower()).ratio() >= 0.8:
            return True
    return False


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
    # Corpo: onde ela está "encarnada" agora e o que esse corpo deixa ela fazer.
    body: str = ""
    senses: list[str] = field(default_factory=list)
    body_connected: bool = False
    # Vontade própria: o que ela foi formando sozinha (vai junto no chip).
    interests: list[str] = field(default_factory=list)
    opinions: list[str] = field(default_factory=list)
    desire: str = ""  # o que ela está com vontade de fazer ou conversar agora
    goal_is_hers: bool = False  # o objetivo atual foi ela que escolheu
    # Funções que ela sabe que tem (ligada/desligada): percebe quando desligam uma.
    features: dict[str, bool] = field(default_factory=dict)

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

    def remember_thought(self, thought: str) -> bool:
        """Guarda o pensamento, a menos que seja uma repetição dos recentes.

        Pensamentos voltam no prompt; guardar repetições faz o modelo copiar o
        mesmo parágrafo para sempre.
        """
        thought = " ".join(thought.split())[:MAX_THOUGHT_CHARS]
        if not thought or is_repeated(thought, self.recent_thoughts):
            return False
        self.recent_thoughts.append(thought)
        self.touch()
        return True

    def touch(self) -> None:
        self.last_updated_at = time.time()

    # --------------------------------------------------------- vontade própria
    @staticmethod
    def _clip(text: str) -> str:
        return " ".join(str(text or "").split())[:MAX_WILL_CHARS]

    def add_interest(self, text: str) -> bool:
        text = self._clip(text)
        if not text or is_repeated(text, self.interests, threshold=0.7):
            return False
        self.interests = [*self.interests, text][-MAX_INTERESTS:]
        self.touch()
        return True

    def add_opinion(self, text: str) -> bool:
        """Opinião nova. Se for sobre o mesmo assunto de uma antiga, substitui (ela muda de ideia)."""
        text = self._clip(text)
        if not text:
            return False
        kept = [o for o in self.opinions if not is_repeated(text, [o])]
        self.opinions = [*kept, text][-MAX_OPINIONS:]
        self.touch()
        return True

    def set_desire(self, text: str) -> None:
        self.desire = self._clip(text)
        self.touch()

    def set_own_goal(self, text: str) -> bool:
        text = self._clip(text)
        if not text or text == self.current_goal:
            return False
        self.current_goal, self.goal_is_hers = text, True
        self.touch()
        return True

    # ------------------------------------------------------------------ corpo
    def enter_body(self, name: str, senses: list[str]) -> str:
        """Liga a um corpo. Devolve o nome do corpo anterior se ela trocou de corpo."""
        previous = self.body if self.body and self.body != name else ""
        self.body, self.senses, self.body_connected = name, list(senses), True
        self.touch()
        return previous

    def leave_body(self) -> None:
        self.body_connected = False
        self.touch()

    # ------------------------------------------------------------------ prompt
    def describe(self, now: float | None = None) -> str:
        now = now or time.time()
        lines = [
            f"Seu humor agora: {self.mood} (intensidade {self.mood_intensity:.1f}).",
            f"Seu objetivo atual{' (escolhido por você)' if self.goal_is_hers else ''}: {self.current_goal}.",
        ]
        if self.interests:
            lines.append("Coisas que te interessam: " + "; ".join(self.interests[-5:]) + ".")
        if self.opinions:
            lines.append("Opiniões suas: " + "; ".join(self.opinions[-5:]) + ".")
        if self.desire:
            lines.append(f"O que você está com vontade agora: {self.desire}.")
        if self.body:
            if self.body_connected:
                senses = ", ".join(SENSE_LABEL.get(x, x) for x in self.senses) or "nenhum sentido"
                lines.append(f"Seu corpo agora: {self.body} ({senses}).")
            else:
                lines.append(
                    f"Você está sem corpo agora (o último foi {self.body}): só pensa e responde o chat."
                )
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
        thoughts = d.pop("recent_thoughts", []) or []
        known = cls.__dataclass_fields__.keys()
        state = cls(**{k: v for k, v in d.items() if k in known})
        last_updated = state.last_updated_at
        for t in thoughts:  # limpa repetições salvas por versões antigas
            state.remember_thought(str(t))
        state.last_updated_at = last_updated
        return state

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
