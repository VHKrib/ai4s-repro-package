"""Engine protocol and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import Config
from ..intake import IntakeResult


@dataclass
class EngineResult:
    rows: list[dict[str, Any]]
    engine: str
    notes: list[str] = field(default_factory=list)
    uncertainties: list[dict[str, Any]] = field(default_factory=list)


ProgressCb = Callable[[int, int, str], None]


class Engine(Protocol):
    name: str

    def generate(
        self,
        config: Config,
        intake: IntakeResult,
        run_dir: Path,
        progress: ProgressCb | None = None,
    ) -> EngineResult: ...


def get_engine(name: str) -> Engine:
    from .af3 import AF3Engine
    from .replay import ReplayEngine
    from .vina import VinaEngine

    registry = {
        "replay": ReplayEngine(),
        "vina": VinaEngine(),
        "af3": AF3Engine(),
    }
    if name not in registry:
        raise ValueError(f"unknown engine {name!r}")
    return registry[name]
