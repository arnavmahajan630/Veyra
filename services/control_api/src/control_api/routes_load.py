"""API routes for triggering synthetic load tests."""

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from control_api.events import EventHub

router = APIRouter(prefix="/load", tags=["load"])
log = logging.getLogger(__name__)


class LoadStart(BaseModel):
    count: int
    # Allow eps and mix to be sent for backward compatibility, but ignore them
    eps: int | None = None
    mix: str | None = None


class LoadState:
    def __init__(self) -> None:
        self.running = False
        self.sent = 0
        self.total = 0
        self.outcome: str | None = None
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self.start_time: float = 0.0
        self.actual_eps: float = 0.0


state = LoadState()


async def background_load(count: int, hub: EventHub) -> None:
    state.running = True
    state.total = count
    state.sent = 0
    state.outcome = None
    state.error = None
    state.start_time = time.monotonic()
    state.actual_eps = 0.0
    
    def _publish_status():
        hub.publish("load", {
            "running": state.running,
            "sent": state.sent,
            "total": state.total,
            "outcome": state.outcome,
            "error": state.error,
            "actual_eps": state.actual_eps,
        })
        
    _publish_status()
    
    try:
        proc = await asyncio.create_subprocess_exec(
            "python", "/app/tools/bench/load_raw.py", 
            "--events", str(count), 
            "--workers", "8", 
            "--interval", "0.2",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        state._proc = proc
        
        # Read output line by line to parse the 'normalized' count and rate
        if proc.stdout:
            while state.running:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        break
                    continue
                if not line:
                    break
                line_str = line.decode('utf-8').strip()
                # Try to match the output line which looks like:
                # 1s         5,000         5,000     995,000
                if "s " in line_str:
                    parts = line_str.split()
                    if len(parts) >= 4 and parts[0].endswith("s"):
                        try:
                            normalized = int(parts[1].replace(",", ""))
                            rate = float(parts[2].replace(",", ""))
                            state.sent = normalized
                            state.actual_eps = rate
                            _publish_status()
                        except ValueError:
                            pass
        
        if state.running:
            await proc.wait()
            if proc.returncode != 0 and proc.returncode is not None:
                state.outcome = "failed"
                state.error = f"Load test process exited with code {proc.returncode}"
                _publish_status()
    except Exception as e:
        state.outcome = "failed"
        state.error = f"Failed to start load test: {e}"
        log.error(state.error)
        _publish_status()
    finally:
        state.running = False
        state._task = None
        if state._proc:
            try:
                state._proc.terminate()
            except ProcessLookupError:
                pass
            state._proc = None
        _publish_status()


@router.post("/start")
async def start_load(req: LoadStart, request: Request) -> dict[str, str]:
    if state.running:
        raise HTTPException(status_code=400, detail="Load test already running")
    hub: EventHub = request.app.state.ctx.hub
    state._task = asyncio.create_task(background_load(req.count, hub))
    return {"status": "started"}


@router.post("/stop")
async def stop_load() -> dict[str, str]:
    state.running = False
    if state._proc:
        try:
            state._proc.terminate()
        except ProcessLookupError:
            pass
    if state._task:
        state._task.cancel()
        state._task = None
    return {"status": "stopped"}


@router.get("/status")
async def get_status() -> dict[str, Any]:
    return {
        "running": state.running,
        "sent": state.sent,
        "total": state.total,
        "outcome": state.outcome,
        "error": state.error,
        "actual_eps": state.actual_eps,
    }
