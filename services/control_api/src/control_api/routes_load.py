"""API routes for triggering synthetic load tests."""

import asyncio
import logging
import socket
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/load", tags=["load"])
log = logging.getLogger(__name__)

class LoadStart(BaseModel):
    count: int
    eps: int
    mix: str = "ssh"

class LoadState:
    def __init__(self) -> None:
        self.running = False
        self.sent = 0
        self.total = 0
        self._task: asyncio.Task[None] | None = None

state = LoadState()

def _generate_syslog(user: str, mix: str) -> bytes:
    if mix == "firewall":
        return f"<86>Sep 26 14:05:12 fw-dmz-01 kernel: DROP IN=eth0 OUT= MAC=00:00 SRC=45.12.3.9 DST=10.0.0.1 LEN=40 TOS=0x00 PREC=0x00 TTL=241 ID=123 PROTO=TCP SPT=52144 DPT=22 WINDOW=1024 RES=0x00 SYN URGP=0".encode("utf-8")
    return f"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user {user} from 45.12.3.9 port 52144 ssh2".encode("utf-8")

async def background_load(count: int, eps: int, mix: str) -> None:
    state.running = True
    state.total = count
    state.sent = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    target = ("edge-dmz", 5514) if mix == "firewall" else ("edge-core", 5524)
    batch_delay = 0.1
    batch_size = max(1, int(eps * batch_delay)) if eps > 0 else 1
    
    try:
        while state.running and state.sent < count:
            start_batch = time.monotonic()
            to_send = min(batch_size, count - state.sent)
            
            for _ in range(to_send):
                user = f"probe{uuid.uuid4().hex[:8]}"
                msg = _generate_syslog(user, mix)
                try:
                    sock.sendto(msg, target)
                except Exception as e:
                    log.error(f"UDP send failed: {e}")
            
            state.sent += to_send
            elapsed = time.monotonic() - start_batch
            
            if eps > 0:
                if elapsed < batch_delay:
                    await asyncio.sleep(batch_delay - elapsed)
                else:
                    await asyncio.sleep(0) # yield to event loop
            else:
                await asyncio.sleep(0)
    finally:
        sock.close()
        state.running = False
        state._task = None

@router.post("/start")
async def start_load(req: LoadStart) -> dict[str, str]:
    if state.running:
        raise HTTPException(status_code=400, detail="Load test already running")
    state._task = asyncio.create_task(background_load(req.count, req.eps, req.mix))
    return {"status": "started"}

@router.post("/stop")
async def stop_load() -> dict[str, str]:
    state.running = False
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
    }
