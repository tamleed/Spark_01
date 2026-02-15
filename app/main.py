from __future__ import annotations

import os
from typing import Any, Dict

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .config import load_config
from .orchestrator import ModelOrchestrator

CONFIG_PATH = os.getenv("SPARK_ROUTER_CONFIG", "config/models.yaml")

app = FastAPI(title="Spark LLM Router", version="0.1.0")
config = load_config(CONFIG_PATH)
orchestrator = ModelOrchestrator(config)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await orchestrator.stop()


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "running_model": orchestrator.running_model,
        "configured_models": list(config.models.keys()),
    }


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": key, "object": "model"} for key in config.models.keys()],
    }


@app.post("/admin/switch/{model_name}")
async def switch_model(model_name: str) -> Dict[str, str]:
    try:
        await orchestrator.ensure_model(model_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "switched", "model": model_name}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    payload = await request.json()
    model_name = payload.get("model") or config.default_model
    if not model_name:
        raise HTTPException(status_code=400, detail="model is required")

    if model_name not in config.models:
        raise HTTPException(status_code=404, detail=f"Unknown model '{model_name}'")

    if payload.get("stream"):
        raise HTTPException(status_code=400, detail="stream=true is not supported in router v0.1")

    try:
        base_url = await orchestrator.ensure_model(model_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    target_url = f"{base_url}/v1/chat/completions"
    headers = {"content-type": "application/json"}

    async with httpx.AsyncClient(timeout=None) as client:
        upstream = await client.post(target_url, headers=headers, json=payload)

    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "application/json"),
        status_code=upstream.status_code,
    )
