"""Stage 3 agent: computational modelling, pose QC, ranking and gating.

The package is deliberately dependency-light. Core pipeline only needs the
standard library; RDKit / Vina are imported lazily by the engines that use
them, so a replay run works on a bare Python install.
"""

from .version import PIPELINE_VERSION, SCHEMA_VERSION

__all__ = ["PIPELINE_VERSION", "SCHEMA_VERSION"]
