"""Engine adapters.

All engines return rows in the normalized `poses` table shape, so QC, ranking
and gating never need to know where a pose came from.
"""

from .base import Engine, EngineResult, get_engine
from .af3 import AF3Engine
from .replay import ReplayEngine
from .vina import VinaEngine

__all__ = [
    "Engine",
    "EngineResult",
    "get_engine",
    "AF3Engine",
    "ReplayEngine",
    "VinaEngine",
]
