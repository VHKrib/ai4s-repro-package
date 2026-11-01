"""Typed errors so the CLI can distinguish user error from engine failure."""


class Stage3Error(Exception):
    """Base class for all stage-3 errors."""


class ConfigError(Stage3Error):
    """Config file missing keys, bad paths, inconsistent options."""


class ContractError(Stage3Error):
    """Input or output table violates the declared schema."""


class EngineUnavailable(Stage3Error):
    """Requested engine (Vina / RDKit / AF3 parser) is not installed."""

    def __init__(self, engine: str, hint: str = "") -> None:
        message = f"engine not available: {engine}"
        if hint:
            message = f"{message}. {hint}"
        super().__init__(message)
        self.engine = engine
        self.hint = hint


class StageFailed(Stage3Error):
    """A pipeline stage failed in a way that should stop the run."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.message = message
