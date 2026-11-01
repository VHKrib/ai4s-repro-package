"""Config loading, defaults and validation.

Paths in a config file are resolved relative to the repository root (the
parent of the directory holding the config), so a config can be moved as long
as the tree around it stays intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .io_utils import read_json, sha256_file
from .version import CONFIG_SCHEMA_VERSION

DEFAULT_CRITERIA: dict[str, Any] = {
    "primary_receptor": "WT",
    "min_site_overlap": 0.70,
    "min_catalytic_contacts": 1,
    "max_clashes": 0,
    "max_affinity_kcal_mol": -7.0,
    "min_ligand_efficiency": 0.18,
    "max_flex_delta": 1.0,
    "max_consensus_std": 0.08,
    "require_reference_pass": True,
    "allow_af3_only_advance": False,
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "pose": 0.30,
    "affinity": 0.20,
    "consistency": 0.20,
    "developability": 0.15,
    "resistance": 0.15,
}

DEFAULT_TIERS = {"A": 0.70, "B": 0.55}

DEFAULT_WINDOWS = {
    "affinity": {"weak": -6.0, "strong": -10.0},
    "ligand_efficiency": {"weak": 0.15, "strong": 0.35},
}

VALID_ENGINES = ("replay", "vina", "af3")


@dataclass
class Config:
    raw: dict[str, Any]
    path: Path | None
    base_dir: Path

    @property
    def case_id(self) -> str:
        return self.raw["case_id"]

    @property
    def engine(self) -> str:
        return self.raw.get("engine", {}).get("name", "replay")

    @property
    def engine_options(self) -> dict[str, Any]:
        return self.raw.get("engine", {}).get("options", {})

    @property
    def criteria(self) -> dict[str, Any]:
        return self.raw["criteria"]

    @property
    def weights(self) -> dict[str, float]:
        return self.raw["weights"]

    @property
    def tiers(self) -> dict[str, float]:
        return self.raw["tiers"]

    @property
    def windows(self) -> dict[str, dict[str, float]]:
        return self.raw["windows"]

    @property
    def reference_checks(self) -> list[dict[str, Any]]:
        return self.raw.get("reference", {}).get("checks", [])

    @property
    def target(self) -> dict[str, Any]:
        return self.raw.get("target", {})

    @property
    def runs_dir(self) -> Path:
        return self.resolve(self.raw.get("runs_dir", "stage3_runs"))

    def resolve(self, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (self.base_dir / path).resolve()

    def input_path(self, key: str) -> Path | None:
        value = self.raw.get("inputs", {}).get(key)
        if not value:
            return None
        return self.resolve(value)

    @property
    def config_hash(self) -> str:
        if self.path and self.path.exists():
            return sha256_file(self.path)
        return ""

    def summary(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "target": self.target,
            "engine": self.engine,
            "engine_options": self.engine_options,
            "criteria": self.criteria,
            "weights": self.weights,
            "tiers": self.tiers,
            "windows": self.windows,
            "reference_checks": self.reference_checks,
        }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw = read_json(path)

    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"{path.name}: schema_version must be {CONFIG_SCHEMA_VERSION}, "
            f"got {raw.get('schema_version')!r}"
        )
    for key in ("case_id", "inputs", "engine"):
        if key not in raw:
            raise ConfigError(f"{path.name}: missing required key {key!r}")

    engine = raw.get("engine", {}).get("name")
    if engine not in VALID_ENGINES:
        raise ConfigError(
            f"{path.name}: engine.name must be one of {VALID_ENGINES}, got {engine!r}"
        )

    inputs = raw.get("inputs", {})
    if not inputs.get("candidates") or not inputs.get("receptors"):
        raise ConfigError(f"{path.name}: inputs.candidates and inputs.receptors are required")
    if engine == "replay" and not inputs.get("poses"):
        raise ConfigError(f"{path.name}: replay engine requires inputs.poses")

    raw["criteria"] = _deep_merge(DEFAULT_CRITERIA, raw.get("criteria", {}))
    raw["weights"] = _deep_merge(DEFAULT_WEIGHTS, raw.get("weights", {}))
    raw["tiers"] = _deep_merge(DEFAULT_TIERS, raw.get("tiers", {}))
    raw["windows"] = _deep_merge(DEFAULT_WINDOWS, raw.get("windows", {}))

    weight_sum = sum(float(value) for value in raw["weights"].values())
    if abs(weight_sum - 1.0) > 1e-6:
        raise ConfigError(f"{path.name}: weights must sum to 1.0, got {weight_sum:.4f}")
    if raw["tiers"]["A"] <= raw["tiers"]["B"]:
        raise ConfigError(f"{path.name}: tiers.A must be greater than tiers.B")

    for check in raw.get("reference", {}).get("checks", []):
        if "id" not in check or "type" not in check:
            raise ConfigError(f"{path.name}: reference check missing id/type: {check}")

    return Config(raw=raw, path=path, base_dir=path.parent.parent)
