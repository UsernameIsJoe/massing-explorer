from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import GrossingConfig


def load_project_config(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def grossing_from_config(config: dict[str, Any]) -> GrossingConfig:
    grossing = config.get("grossing", {})
    return GrossingConfig(
        area_adjustment=float(grossing.get("area_adjustment", 1.15)),
        grossing_factor=float(grossing.get("grossing_factor", 1.50)),
    )


def gsf_tolerance_from_config(config: dict[str, Any]) -> float:
    return float(config.get("gsf_tolerance", 0.03))
