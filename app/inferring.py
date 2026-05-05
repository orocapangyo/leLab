"""Inference mode: drives the SO-101 follower with a trained policy.

Mirrors `app/teleoperating.py` in shape — single global session, mutex
with teleoperation/recording (the follower's serial bus can only be
opened once), `lerobot.scripts.lerobot_rollout` running as a subprocess
for clean cancellation. Hub-checkpoint refs are resolved to a local dir
via huggingface_hub.snapshot_download before we spawn the subprocess.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

from .config import setup_follower_calibration_file

logger = logging.getLogger(__name__)


class InferenceRequest(BaseModel):
    follower_port: str
    follower_config: str
    policy_ref: str          # opaque ref returned by /jobs/{id}/checkpoints
    task: str = ""
    cameras: Dict[str, Dict[str, Any]] = {}
    duration_s: int = 60


inference_active: bool = False
_inference_proc: Optional[subprocess.Popen] = None
_inference_started_at: Optional[float] = None
_inference_meta: Dict[str, Any] = {}
_HUB_REF_RE = re.compile(r"^(?P<repo>[^@]+)@checkpoints/(?P<step>\d+)$")


def _detect_device() -> str:
    """cuda → mps → cpu, picked once at start time."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _resolve_policy_path(policy_ref: str) -> str:
    """Turn a checkpoints API ref into a local path that lerobot accepts.

    Local refs are already absolute paths to a pretrained_model dir.
    Hub refs look like 'user/repo@checkpoints/<step>' and need a
    snapshot_download of just that subdir.
    """
    if Path(policy_ref).is_dir():
        return policy_ref
    m = _HUB_REF_RE.match(policy_ref)
    if not m:
        raise ValueError(f"Unrecognised policy ref: {policy_ref!r}")
    from huggingface_hub import snapshot_download
    repo_id, step = m.group("repo"), m.group("step")
    local_root = snapshot_download(
        repo_id=repo_id,
        repo_type="model",
        allow_patterns=[f"checkpoints/{step}/pretrained_model/*"],
    )
    return str(Path(local_root) / "checkpoints" / step / "pretrained_model")


def _format_cameras_arg(cameras: Dict[str, Dict[str, Any]]) -> str:
    """Convert {name: {type, camera_index, width, height, fps}} into
    lerobot's CLI dict syntax. The frontend key `camera_index` is
    remapped to lerobot's `index_or_path`."""
    parts = []
    for name, cfg in cameras.items():
        remapped = {
            ("index_or_path" if k == "camera_index" else k): v
            for k, v in cfg.items()
            if v is not None
        }
        body = ", ".join(f"{k}: {v}" for k, v in remapped.items())
        parts.append(f"{name}: {{{body}}}")
    return "{" + ", ".join(parts) + "}"


def handle_start_inference(request: InferenceRequest) -> Dict[str, Any]:
    """Start a one-shot rollout subprocess. Returns a dict — the route
    layer turns it into a JSON response or HTTPException as appropriate."""
    global inference_active, _inference_proc, _inference_started_at, _inference_meta

    # Mutex with teleop and recording: all three drive the same serial bus.
    from .teleoperating import teleoperation_active
    from .recording import recording_active

    if teleoperation_active:
        return {"success": False, "status_code": 409,
                "message": "Teleoperation is currently active. Stop it first."}
    if recording_active:
        return {"success": False, "status_code": 409,
                "message": "Recording is currently active. Stop it first."}
    if inference_active:
        return {"success": False, "status_code": 409,
                "message": "Inference is already active. Stop it first."}

    try:
        setup_follower_calibration_file(request.follower_config)
        policy_path = _resolve_policy_path(request.policy_ref)

        cmd = [
            "python", "-m", "lerobot.scripts.lerobot_rollout",
            "--strategy.type=base",
            f"--policy.path={policy_path}",
            f"--policy.device={_detect_device()}",
            "--robot.type=so101_follower",
            f"--robot.port={request.follower_port}",
            f"--robot.id={request.follower_config}",
            f"--task={request.task}",
            f"--duration={request.duration_s}",
        ]
        if request.cameras:
            cmd.append(f"--robot.cameras={_format_cameras_arg(request.cameras)}")

        log_dir = Path.home() / ".cache" / "huggingface" / "lerobot" / "inference_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{int(time.time())}.log"
        log_handle = log_path.open("w", buffering=1)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except Exception as exc:
        logger.exception("Failed to start inference")
        return {"success": False, "status_code": 500,
                "message": f"Failed to start inference: {exc}"}

    inference_active = True
    _inference_proc = proc
    _inference_started_at = time.time()
    _inference_meta = {
        "policy_ref": request.policy_ref,
        "duration_s": request.duration_s,
        "log_path": str(log_path),
    }
    logger.info("Inference started: pid=%s policy=%s", proc.pid, policy_path)
    return {"success": True, "message": "Inference started", "log_path": str(log_path)}


def handle_stop_inference() -> Dict[str, Any]:
    global inference_active, _inference_proc, _inference_started_at, _inference_meta
    if not inference_active or _inference_proc is None:
        return {"success": False, "status_code": 409, "message": "No inference is active"}
    proc = _inference_proc
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Inference did not exit in 5s; killing")
            proc.kill()
            proc.wait()
    except Exception as exc:
        logger.exception("Stop inference: %s", exc)
    inference_active = False
    _inference_proc = None
    _inference_started_at = None
    _inference_meta = {}
    return {"success": True, "message": "Inference stopped"}


def handle_inference_status() -> Dict[str, Any]:
    global inference_active, _inference_proc, _inference_started_at, _inference_meta
    # If the subprocess died on its own, finalize state lazily.
    if _inference_proc is not None and _inference_proc.poll() is not None:
        rc = _inference_proc.returncode
        logger.info("Inference subprocess exited rc=%s", rc)
        inference_active = False
        _inference_proc = None
        finished_meta = _inference_meta
        finished_started = _inference_started_at
        _inference_started_at = None
        _inference_meta = {}
        return {
            "inference_active": False,
            "exited": True,
            "exit_code": rc,
            "policy_ref": finished_meta.get("policy_ref"),
            "duration_s": finished_meta.get("duration_s"),
            "log_path": finished_meta.get("log_path"),
            "started_at": finished_started,
        }
    elapsed = (time.time() - _inference_started_at) if _inference_started_at else 0
    return {
        "inference_active": inference_active,
        "started_at": _inference_started_at,
        "elapsed_s": elapsed,
        "duration_s": _inference_meta.get("duration_s"),
        "policy_ref": _inference_meta.get("policy_ref"),
        "log_path": _inference_meta.get("log_path"),
    }
