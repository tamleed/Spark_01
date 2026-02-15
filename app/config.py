from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field


class ModelSpec(BaseModel):
    name: str
    base_url: str = Field(description="URL локального backend-сервера модели")
    start_cmd: str = Field(description="Команда запуска backend")
    stop_cmd: Optional[str] = Field(default=None, description="Опциональная команда остановки backend")
    startup_timeout_sec: int = 420
    health_path: str = "/health"
    env: Dict[str, str] = Field(default_factory=dict)


class ServiceConfig(BaseModel):
    default_model: Optional[str] = None
    models: Dict[str, ModelSpec]


def load_config(path: str | Path) -> ServiceConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid YAML structure")

    raw_models = data.get("models")
    if not isinstance(raw_models, dict) or not raw_models:
        raise ValueError("At least one model must be configured in 'models'")

    normalized = {}
    for model_key, model_data in raw_models.items():
        model_data = dict(model_data)
        model_data.setdefault("name", model_key)
        normalized[model_key] = ModelSpec(**model_data)

    return ServiceConfig(default_model=data.get("default_model"), models=normalized)
