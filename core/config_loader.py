"""
Config loader for Jobee.
Loads config.yaml → AppConfig, saves back, validates with Pydantic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from core.models import AppConfig

log = logging.getLogger(__name__)

# Project root is one level above this file (core/)
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> AppConfig:
    """Load config.yaml and return a validated AppConfig.
    If the file is missing, write defaults and return them.
    """
    if not CONFIG_PATH.exists():
        log.warning("config.yaml not found — writing defaults.")
        cfg = AppConfig()
        save_config(cfg)
        return cfg

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    try:
        return AppConfig(**raw)
    except ValidationError as exc:
        log.error("config.yaml validation error:\n%s", exc)
        log.warning("Falling back to default config.")
        return AppConfig()


def save_config(cfg: AppConfig) -> None:
    """Write an AppConfig back to config.yaml (human-readable YAML)."""
    data = cfg.model_dump()
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    log.info("config.yaml saved.")


def update_config(updates: dict[str, Any]) -> AppConfig:
    """Merge a dict of updates into the current config and save."""
    cfg = load_config()
    patched = cfg.model_dump()
    patched.update(updates)
    new_cfg = AppConfig(**patched)
    save_config(new_cfg)
    return new_cfg
