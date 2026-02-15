from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import ModelSpec, ServiceConfig


@dataclass
class RunningModel:
    name: str
    process: subprocess.Popen
    spec: ModelSpec


class ModelOrchestrator:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self._running: Optional[RunningModel] = None
        self._lock = asyncio.Lock()

    @property
    def running_model(self) -> Optional[str]:
        if self._running is None:
            return None
        if self._running.process.poll() is not None:
            return None
        return self._running.name

    def model_base_url(self, model_name: str) -> str:
        return self.config.models[model_name].base_url.rstrip("/")

    async def ensure_model(self, model_name: str) -> str:
        if model_name not in self.config.models:
            raise ValueError(f"Unknown model '{model_name}'")

        async with self._lock:
            if self.running_model == model_name:
                return self.model_base_url(model_name)

            await self._stop_running_locked()
            await self._start_locked(model_name)
            return self.model_base_url(model_name)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_running_locked()

    async def _start_locked(self, model_name: str) -> None:
        spec = self.config.models[model_name]
        env = os.environ.copy()
        env.update(spec.env)
        process = subprocess.Popen(  # noqa: S603
            spec.start_cmd,
            shell=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        self._running = RunningModel(name=model_name, process=process, spec=spec)

        ok = await self._wait_for_health(spec)
        if not ok:
            await self._stop_running_locked(force=True)
            raise RuntimeError(f"Model '{model_name}' failed health check during startup")

    async def _stop_running_locked(self, force: bool = False) -> None:
        if self._running is None:
            return

        process = self._running.process
        spec = self._running.spec

        if process.poll() is None:
            if spec.stop_cmd and not force:
                subprocess.run(spec.stop_cmd, shell=True, check=False)  # noqa: S602,S603

            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

            await asyncio.sleep(1)

            if process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

            process.wait(timeout=10)

        self._running = None

    async def _wait_for_health(self, spec: ModelSpec) -> bool:
        deadline = time.monotonic() + spec.startup_timeout_sec
        health_url = f"{spec.base_url.rstrip('/')}{spec.health_path}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.monotonic() < deadline:
                if self._running is None or self._running.process.poll() is not None:
                    return False
                try:
                    response = await client.get(health_url)
                    if response.status_code < 500:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(2)

        return False
