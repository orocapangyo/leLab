# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import importlib.util
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# Cached at module load — never re-checked. After install, the user must
# restart lelab for a freshly-installed package to be importable.
TRAINING_AVAILABLE: bool = importlib.util.find_spec("accelerate") is not None
TRAINING_INSTALL_HINT: str = "pip install accelerate"

WANDB_AVAILABLE: bool = importlib.util.find_spec("wandb") is not None
WANDB_INSTALL_HINT: str = "pip install wandb"


def _build_install_cmd(package: str) -> list[str]:
    """Pick the best installer for the running Python.

    Venvs created with `uv venv` don't ship pip, so `python -m pip` fails with
    `No module named pip`. Detect uv on PATH and use it with --python pinned to
    sys.executable so the install lands in this Python's site-packages.
    Otherwise fall back to `python -m pip`.
    """
    if shutil.which("uv"):
        return ["uv", "pip", "install", "--python", sys.executable, package]
    return [sys.executable, "-m", "pip", "install", package]


class ExtraStatus(BaseModel):
    available: bool
    install_hint: str


class InstallStartResponse(BaseModel):
    started: bool
    message: str


class InstallStatusResponse(BaseModel):
    state: str  # "idle" | "installing" | "done" | "error"
    error: str | None = None
    logs: list[dict[str, Any]] = []


class InstallManager:
    def __init__(self, package: str) -> None:
        self.package = package
        self.state: str = "idle"
        self.error: str | None = None
        self.process: subprocess.Popen | None = None
        self.log_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "installing":
                return {"started": False, "message": "Install already in progress"}
            # Reset for a fresh attempt (covers retry from done/error/idle).
            self.state = "installing"
            self.error = None
            self._drain_queue()

        try:
            self.process = subprocess.Popen(
                _build_install_cmd(self.package),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
        except Exception as exc:
            logger.exception("Failed to spawn pip subprocess")
            with self._lock:
                self.state = "error"
                self.error = f"Failed to spawn pip: {exc}"
            return {"started": False, "message": str(exc)}

        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        return {"started": True, "message": "Install started"}

    def get_status(self) -> dict[str, Any]:
        logs: list[dict[str, Any]] = []
        try:
            while not self.log_queue.empty():
                logs.append(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        return {"state": self.state, "error": self.error, "logs": logs}

    def _monitor(self) -> None:
        assert self.process is not None
        try:
            for line in iter(self.process.stdout.readline, ""):
                if not line:
                    break
                self._enqueue(line.rstrip())
        except Exception as exc:
            logger.exception("Error reading pip output")
            self._enqueue(f"[install-monitor] error reading output: {exc}")

        self.process.wait()
        return_code = self.process.returncode
        with self._lock:
            if return_code == 0:
                self.state = "done"
                self.error = None
            else:
                self.state = "error"
                self.error = f"pip exited with code {return_code}"

    def _enqueue(self, message: str) -> None:
        # Cap queue size so a chatty pip can't grow memory unbounded.
        if self.log_queue.qsize() >= 1000:
            with contextlib.suppress(queue.Empty):
                self.log_queue.get_nowait()
        self.log_queue.put({"timestamp": time.time(), "message": message})

    def _drain_queue(self) -> None:
        try:
            while not self.log_queue.empty():
                self.log_queue.get_nowait()
        except queue.Empty:
            pass


training_install_manager = InstallManager("accelerate")
wandb_install_manager = InstallManager("wandb")


def handle_get_training_extra() -> dict[str, Any]:
    return {"available": TRAINING_AVAILABLE, "install_hint": TRAINING_INSTALL_HINT}


def handle_install_training_extra() -> dict[str, Any]:
    return training_install_manager.start()


def handle_install_training_extra_status() -> dict[str, Any]:
    return training_install_manager.get_status()


def handle_get_wandb_extra() -> dict[str, Any]:
    return {"available": WANDB_AVAILABLE, "install_hint": WANDB_INSTALL_HINT}


def handle_install_wandb_extra() -> dict[str, Any]:
    return wandb_install_manager.start()


def handle_install_wandb_extra_status() -> dict[str, Any]:
    return wandb_install_manager.get_status()
