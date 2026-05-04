# Training Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the singleton `TrainingManager` with a per-job `JobRunner` + file-backed `JobRegistry`. Add a `/jobs` API family, surface running and recently-finished jobs as cards on Landing, and split the Training page into a Configuration screen (`/training`) and a per-job Monitoring screen (`/training/:id`).

**Architecture:** Backend introduces `app/jobs.py` owning a registry (in-memory map plus `outputs/train/{id}/job.json` files) and a `JobRunner` Protocol with one implementation today (`LocalJobRunner`, wrapping `subprocess.Popen`). `app/training.py` shrinks to a `TrainingRequest` model + a standalone `build_training_command(request, output_dir)` helper. `app/main.py` swaps the four old training routes for the new `/jobs/...` family. Frontend adds a `JobsSection` to Landing, a `JobCard` component, a `/training/:jobId` route, and refactors `Training.tsx` to render Configuration mode when `:jobId` is absent and Monitoring mode when present.

**Tech Stack:** Python 3.12+ (FastAPI, Pydantic, threading + subprocess), React + TypeScript + Vite, shadcn/ui, recharts, Tailwind, `lucide-react`.

**Spec:** [docs/superpowers/specs/2026-05-04-training-jobs-design.md](../specs/2026-05-04-training-jobs-design.md)

**No test suite exists in this repo** (per `CLAUDE.md`). Verification per task: `npm run build` from `frontend/` for TypeScript, an import sanity check for backend modules (`.venv/bin/python -c "from app.jobs import ..."`), and a final manual end-to-end smoke against the running `lelab --dev`.

**Existing context the implementer needs:**

- `app/training.py` currently holds: a `TrainingRequest` model, a `TrainingStatus` model, a `TrainingManager` class (singleton `training_manager`), a `_build_training_command` method, the `_TQDM_RE` regex, a `_parse_duration` helper, and `_parse_log_line`. After this work, only `TrainingRequest`, `_build_training_command` (as a free function `build_training_command`), `DEFAULT_OUTPUT_DIR`, `_SLUG_RE`, and `_generate_output_dir` remain in that file. Everything else moves to `app/jobs.py`.
- `app/main.py` registers `/start-training`, `/stop-training`, `/training-status`, `/training-logs` (lines around 347–380). All four are removed and replaced.
- `frontend/src/pages/Training.tsx` is one big component handling the whole training UX. It's split into two render modes branching on `useParams<{ jobId?: string }>().jobId`.
- `frontend/src/components/training/{TrainingTabs,TrainingControls,MonitoringTab}.tsx` are deleted. `TrainingHeader.tsx` is kept; its job-status indicator is removed since the page-level header doesn't know about a specific job anymore.
- The frontend uses `useApi()` from `@/contexts/ApiContext` for `baseUrl` + `fetchWithHeaders`. All new fetches go through it.
- The `lelab --dev` server runs uvicorn with `--reload` from the repo root, so backend file changes hot-reload.

**Task ordering rationale:** Backend types → runner → registry → training.py refactor → endpoints, so each backend task leaves a green import. Frontend API helpers → presentational `JobCard` → `JobsSection` → Landing wiring → router → `Training.tsx` rework → delete dead components, so each frontend task leaves a buildable tree. Final task is a manual smoke.

---

### Task 1: Create `app/jobs.py` with core types and metric parser

**Files:**
- Create: `app/jobs.py`

This task introduces the file with the typed primitives and one pure helper. No subprocess management yet — that's Task 2. No registry — that's Task 3.

- [ ] **Step 1: Create the file**

Create `/Users/nicolasrabault/Projects/Hackathon/leLab/app/jobs.py` with this exact content:

```python
"""Job lifecycle and registry for trainings (and, in future, other long-running
work). One JobRunner instance owns one subprocess; the JobRegistry owns the
overall state, including history persisted to disk under outputs/train/."""

from __future__ import annotations

import logging
import re
import threading
from queue import Empty, Queue
from typing import List, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from .training import TrainingRequest

logger = logging.getLogger(__name__)


JobState = Literal["running", "done", "failed", "interrupted"]


class TrainingMetrics(BaseModel):
    current_step: int = 0
    total_steps: int = 0
    current_loss: Optional[float] = None
    current_lr: Optional[float] = None
    grad_norm: Optional[float] = None
    eta_seconds: Optional[float] = None


class LogLine(BaseModel):
    timestamp: float
    message: str


class JobRecord(BaseModel):
    id: str
    name: str
    state: JobState
    config: TrainingRequest
    output_dir: str
    started_at: float
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    metrics: TrainingMetrics = TrainingMetrics()
    runner: Literal["local"] = "local"


@runtime_checkable
class JobRunner(Protocol):
    """Backend interface for running one job. LocalJobRunner is the only impl
    today; remote runners (SSH, Slurm) drop in here later. @runtime_checkable
    lets `isinstance(r, JobRunner)` work in tests / sanity checks."""

    def start(self, job_id: str, config: TrainingRequest, output_dir: str) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def returncode(self) -> Optional[int]: ...
    def stream_log_lines(self) -> List[LogLine]: ...


# tqdm progress: "Training:   1%|▏         | 125/10000 [02:02<2:36:10,  1.05step/s]"
_TQDM_RE = re.compile(
    r"Training:\s*\d+%[^|]*\|[^|]*\|\s*(\d+)/(\d+)\s*\[(?:[\d:]+)<([\d:]+)"
)


def _parse_duration(s: str) -> Optional[float]:
    """Parse tqdm's HH:MM:SS or MM:SS into seconds. Returns None on '?'."""
    parts = s.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None


def parse_metrics_into(line: str, metrics: TrainingMetrics) -> None:
    """Update `metrics` in-place from one stdout line.

    Two complementary sources:
      * tqdm progress for current_step + total_steps + ETA (~1s cadence).
      * 'INFO ... step:N smpl:... loss:X grdn:Y lr:Z ...' for loss/lr/grdn
        (only at log_freq cadence, default every 250 steps).
    """
    try:
        tqdm_match = _TQDM_RE.search(line)
        if tqdm_match:
            try:
                metrics.current_step = int(tqdm_match.group(1))
                total = int(tqdm_match.group(2))
                if total > 0:
                    metrics.total_steps = total
                eta = _parse_duration(tqdm_match.group(3))
                if eta is not None:
                    metrics.eta_seconds = eta
            except (ValueError, IndexError):
                pass

        if "step:" in line and "loss:" in line:
            try:
                metrics.current_step = int(line.split("step:")[1].split()[0].replace(",", ""))
            except ValueError:
                pass
            try:
                metrics.current_loss = float(line.split("loss:")[1].split()[0])
            except ValueError:
                pass
            if "lr:" in line:
                try:
                    metrics.current_lr = float(line.split("lr:")[1].split()[0])
                except ValueError:
                    pass
            if "grdn:" in line:
                try:
                    metrics.grad_norm = float(line.split("grdn:")[1].split()[0])
                except ValueError:
                    pass

    except Exception as exc:
        logger.debug("Error parsing log line %r: %s", line, exc)


# Re-exported here so callers don't need to know they came from training.py.
# Filled in by Task 2 (LocalJobRunner) and Task 3 (JobRegistry).
__all__ = [
    "JobState",
    "TrainingMetrics",
    "LogLine",
    "JobRecord",
    "JobRunner",
    "parse_metrics_into",
]
```

- [ ] **Step 2: Sanity-check the import**

Run from `/Users/nicolasrabault/Projects/Hackathon/leLab/`:

```bash
.venv/bin/python -c "
from app.jobs import (
    JobState, TrainingMetrics, LogLine, JobRecord, JobRunner, parse_metrics_into
)
m = TrainingMetrics()
parse_metrics_into('Training:  10%|██        | 1000/10000 [00:30<04:30, 33.3step/s]', m)
print('after tqdm:', m.current_step, m.total_steps, m.eta_seconds)
parse_metrics_into('INFO 2026-05-04 14:33:06 ot_train.py:483 step:250 smpl:500 loss:23.05 grdn:642.257 lr:1.0e-05', m)
print('after metric line:', m.current_step, m.current_loss, m.current_lr, m.grad_norm)
"
```

Expected output (exact numbers may differ):

```
after tqdm: 1000 10000 270
after metric line: 250 23.05 1e-05 642.257
```

If the import raises, fix the typo. If the numbers look wrong (e.g. `current_step` stays 0 after the metric line), the parse logic is broken — re-check the splits.

- [ ] **Step 3: Commit**

```bash
git add app/jobs.py
git commit -m "feat(jobs): add JobRecord/Runner protocol and metric parser"
```

---

### Task 2: Implement `LocalJobRunner` in `app/jobs.py`

**Files:**
- Modify: `app/jobs.py` (append new class)

The runner owns one `subprocess.Popen` plus a daemon thread that pumps stdout into an internal queue and updates a `TrainingMetrics` reference passed in.

- [ ] **Step 1: Append the implementation**

Add the following imports near the top of `app/jobs.py` if they aren't already present (the order should be alphabetical inside each group):

```python
import os
import subprocess
import sys
import time
```

(`logging`, `re`, `threading`, `Empty`, `Queue`, `Iterator`, `List`, `Literal`, `Optional`, `Protocol`, `BaseModel`, `TrainingRequest` are already imported from Task 1.)

Then, after the `parse_metrics_into` function, append:

```python
class LocalJobRunner:
    """Run a training as a local subprocess.

    The runner is single-shot: instantiate a fresh one per job. Lifetime of
    the underlying subprocess is bounded by this object's existence in memory.
    """

    def __init__(self, metrics: TrainingMetrics) -> None:
        self._metrics = metrics
        self._process: Optional[subprocess.Popen] = None
        self._log_queue: "Queue[LogLine]" = Queue()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(
        self,
        job_id: str,
        config: TrainingRequest,
        output_dir: str,
    ) -> None:
        if self._process is not None:
            raise RuntimeError("LocalJobRunner already started")

        # Build the command via the helper that lives in training.py.
        from .training import build_training_command  # avoid import cycle at module load
        cmd = build_training_command(config, output_dir)
        logger.info("Starting job %s: %s", job_id, " ".join(cmd))

        # PYTHONUNBUFFERED makes the child's stdout flush per line. Without it
        # block-buffering hides log lines from our parser for many seconds.
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            env=child_env,
        )

        self._monitor_thread = threading.Thread(
            target=self._pump_stdout, name=f"job-{job_id}-stdout", daemon=True
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        self._stop_event.set()
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Subprocess did not terminate in 10s, killing")
                self._process.kill()
                self._process.wait()
        except Exception as exc:
            logger.exception("Error stopping subprocess: %s", exc)

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def returncode(self) -> Optional[int]:
        if self._process is None:
            return None
        return self._process.poll()

    def stream_log_lines(self) -> List[LogLine]:
        """Drain whatever has accumulated since the last call."""
        out: List[LogLine] = []
        try:
            while True:
                out.append(self._log_queue.get_nowait())
        except Empty:
            pass
        return out

    # -- internals --

    def _pump_stdout(self) -> None:
        assert self._process is not None
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._stop_event.is_set():
                    break
                stripped = line.rstrip()
                if not stripped:
                    continue
                parse_metrics_into(stripped, self._metrics)
                # Cap queue so a chatty subprocess can't grow memory unbounded.
                if self._log_queue.qsize() >= 1000:
                    try:
                        self._log_queue.get_nowait()
                    except Empty:
                        pass
                self._log_queue.put(LogLine(timestamp=time.time(), message=stripped))
        except Exception as exc:
            logger.exception("Error reading subprocess stdout: %s", exc)
```

- [ ] **Step 2: Sanity-check the import**

Run from `/Users/nicolasrabault/Projects/Hackathon/leLab/`:

```bash
.venv/bin/python -c "
from app.jobs import LocalJobRunner, TrainingMetrics, JobRunner
m = TrainingMetrics()
r = LocalJobRunner(m)
print('initial:', r.is_running(), r.returncode(), r.stream_log_lines())
print('is JobRunner:', isinstance(r, JobRunner))
"
```

Expected:

```
initial: False None []
is JobRunner: True
```

The `isinstance(r, JobRunner)` check works because `JobRunner` was decorated with `@runtime_checkable` in Task 1.

(Note: `build_training_command` imported lazily inside `start()` doesn't exist yet — that's added in Task 4. Do not call `r.start(...)` here.)

- [ ] **Step 3: Commit**

```bash
git add app/jobs.py
git commit -m "feat(jobs): add LocalJobRunner subprocess wrapper"
```

---

### Task 3: Implement `JobRegistry` in `app/jobs.py`

**Files:**
- Modify: `app/jobs.py` (append new class + module-level singleton + `_generate_id` helper)

This is the heart of the feature. The registry persists each job to `outputs/train/{id}/job.json`, manages the in-memory runner for the current `lelab` session, and runs a single watchdog thread that finalises a job's record when its subprocess exits.

- [ ] **Step 1: Append the registry implementation**

In `app/jobs.py`, append at the bottom (after `LocalJobRunner`):

```python
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict


_PERSIST_THROTTLE_SECONDS = 1.0


def _generate_job_id(policy_type: str, dataset_repo_id: str) -> str:
    """Mirror of training._generate_output_dir's leaf — same slug logic."""
    from .training import _SLUG_RE
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dataset_slug = _SLUG_RE.sub("_", dataset_repo_id).strip("_") or "dataset"
    return f"{policy_type}_{dataset_slug}_{timestamp}"


def _job_dir(output_root: Path, job_id: str) -> Path:
    return output_root / job_id


def _job_meta_path(output_root: Path, job_id: str) -> Path:
    return _job_dir(output_root, job_id) / "job.json"


class JobAlreadyRunningError(Exception):
    """Raised when start() is called while another local job is running."""


class JobNotFoundError(Exception):
    """Raised when a lookup hits an unknown id."""


class JobNotRunningError(Exception):
    """Raised when stop() is called on a non-running job."""


class JobRegistry:
    """Owns the registry of training jobs and their persistence.

    On instantiation, scans outputs/train/ for existing job.json files and
    rewrites any record marked 'running' to 'interrupted' (since this is a
    fresh lelab process — we no longer own those subprocesses).
    """

    def __init__(self, output_root: Path) -> None:
        self._output_root = output_root
        self._output_root.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._records: Dict[str, JobRecord] = {}
        self._runners: Dict[str, LocalJobRunner] = {}
        self._last_persist_at: Dict[str, float] = {}

        self._stop_watchdog = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None

        self._load_from_disk()
        self._start_watchdog()

    # -- public API --

    def list(self, limit: int = 10) -> List[JobRecord]:
        with self._lock:
            records = list(self._records.values())
        records.sort(key=lambda r: r.started_at, reverse=True)
        return records[:limit]

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._records.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        return record

    def start(self, config: TrainingRequest) -> JobRecord:
        with self._lock:
            for r in self._records.values():
                if r.state == "running":
                    raise JobAlreadyRunningError(r.id)

            job_id = _generate_job_id(config.policy_type, config.dataset_repo_id)
            output_dir = str(_job_dir(self._output_root, job_id))
            name = f"{config.policy_type.upper()} · {config.dataset_repo_id}"
            record = JobRecord(
                id=job_id,
                name=name,
                state="running",
                config=config,
                output_dir=output_dir,
                started_at=time.time(),
            )

            _job_dir(self._output_root, job_id).mkdir(parents=True, exist_ok=True)
            self._records[job_id] = record
            self._persist(record, force=True)

            runner = LocalJobRunner(record.metrics)
            try:
                runner.start(job_id, config, output_dir)
            except Exception as exc:
                logger.exception("Failed to start subprocess for job %s", job_id)
                record.state = "failed"
                record.ended_at = time.time()
                record.error_message = f"Failed to spawn subprocess: {exc}"
                self._persist(record, force=True)
                raise

            self._runners[job_id] = runner
            return record

    def stop(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            runner = self._runners.get(job_id)
        if record.state != "running" or runner is None:
            raise JobNotRunningError(job_id)
        runner.stop()
        # The watchdog will finalise the record (state, ended_at, exit_code).
        # Wait briefly so the caller sees the new state in the response.
        for _ in range(20):
            time.sleep(0.1)
            with self._lock:
                if record.state != "running":
                    return record
        return record

    def drain_logs(self, job_id: str) -> List[LogLine]:
        with self._lock:
            if job_id not in self._records:
                raise JobNotFoundError(job_id)
            runner = self._runners.get(job_id)
        if runner is None:
            return []
        return runner.stream_log_lines()

    def delete(self, job_id: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            if record.state == "running":
                raise JobNotRunningError(job_id)
            self._records.pop(job_id, None)
            self._runners.pop(job_id, None)
            self._last_persist_at.pop(job_id, None)
        try:
            shutil.rmtree(_job_dir(self._output_root, job_id))
        except FileNotFoundError:
            pass

    def shutdown(self) -> None:
        """For tests / orderly process exit. Not wired to FastAPI lifespan today."""
        self._stop_watchdog.set()

    # -- internals --

    def _load_from_disk(self) -> None:
        for job_dir in self._output_root.glob("*/"):
            meta = job_dir / "job.json"
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text())
                record = JobRecord.model_validate(data)
            except Exception as exc:
                logger.warning("Skipping malformed job.json at %s: %s", meta, exc)
                continue
            if record.state == "running":
                record.state = "interrupted"
                if record.ended_at is None:
                    record.ended_at = time.time()
                self._write_meta(record)
            self._records[record.id] = record

    def _start_watchdog(self) -> None:
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="job-registry-watchdog", daemon=True
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        while not self._stop_watchdog.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Watchdog tick failed: %s", exc)
            self._stop_watchdog.wait(1.0)

    def _tick(self) -> None:
        with self._lock:
            running_ids = [jid for jid, r in self._records.items() if r.state == "running"]

        for jid in running_ids:
            with self._lock:
                runner = self._runners.get(jid)
                record = self._records.get(jid)
            if runner is None or record is None:
                continue
            if runner.is_running():
                # Persist metric snapshot at most once per second.
                self._persist(record, force=False)
                continue

            # Subprocess exited since the last tick. Finalise.
            rc = runner.returncode()
            with self._lock:
                record.state = "done" if rc == 0 else "failed"
                record.ended_at = time.time()
                record.exit_code = rc
                if rc != 0 and record.error_message is None:
                    record.error_message = f"Subprocess exited with code {rc}"
                self._runners.pop(jid, None)
            self._persist(record, force=True)

    def _persist(self, record: JobRecord, force: bool) -> None:
        now = time.time()
        last = self._last_persist_at.get(record.id, 0.0)
        if not force and (now - last) < _PERSIST_THROTTLE_SECONDS:
            return
        self._last_persist_at[record.id] = now
        self._write_meta(record)

    def _write_meta(self, record: JobRecord) -> None:
        path = _job_meta_path(self._output_root, record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2))


# Module-level singleton. The output root is the project's outputs/train/.
_DEFAULT_OUTPUT_ROOT = Path("outputs/train")
job_registry = JobRegistry(_DEFAULT_OUTPUT_ROOT)
```

Also update the `__all__` list at the very top of the file to include the new public names. Find:

```python
__all__ = [
    "JobState",
    "TrainingMetrics",
    "LogLine",
    "JobRecord",
    "JobRunner",
    "parse_metrics_into",
]
```

Replace with:

```python
__all__ = [
    "JobState",
    "TrainingMetrics",
    "LogLine",
    "JobRecord",
    "JobRunner",
    "LocalJobRunner",
    "JobRegistry",
    "JobAlreadyRunningError",
    "JobNotFoundError",
    "JobNotRunningError",
    "job_registry",
    "parse_metrics_into",
]
```

- [ ] **Step 2: Sanity-check the registry**

Run from `/Users/nicolasrabault/Projects/Hackathon/leLab/`:

```bash
.venv/bin/python -c "
from app.jobs import job_registry, JobNotFoundError
print('initial list:', job_registry.list())
try:
    job_registry.get('does-not-exist')
except JobNotFoundError:
    print('JobNotFoundError raised correctly')
"
```

Expected: a list of any pre-existing `outputs/train/*/job.json` records (very likely an empty list since none exist yet — only auto-generated subdirectories without `job.json`), then `JobNotFoundError raised correctly`.

(Note: the registry's `start()` method uses `build_training_command` lazily — that helper is added in Task 4. Don't call `start()` here.)

- [ ] **Step 3: Commit**

```bash
git add app/jobs.py
git commit -m "feat(jobs): add JobRegistry with file-backed persistence and watchdog"
```

---

### Task 4: Refactor `app/training.py` to a thin helper

**Files:**
- Modify: `app/training.py` (full rewrite — remove the singleton, keep only what's needed)

After this refactor, `app/training.py` exposes: `TrainingRequest`, `DEFAULT_OUTPUT_DIR`, `_SLUG_RE`, `_generate_output_dir` (unchanged), and a new free function `build_training_command(request, output_dir)` that produces the same CLI arg list as today's `TrainingManager._build_training_command`. No singleton, no `TrainingStatus`, no `TrainingManager`, no `_TQDM_RE`/`_parse_duration`/`_parse_log_line` — those moved to `app/jobs.py` in Task 1.

- [ ] **Step 1: Replace the file**

Overwrite `/Users/nicolasrabault/Projects/Hackathon/leLab/app/training.py` with:

```python
"""Training-specific helpers: the request schema and the LeRobot CLI builder.

The actual job lifecycle (subprocess management, registry, log streaming)
lives in app/jobs.py.
"""

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


DEFAULT_OUTPUT_DIR = "outputs/train"
_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _generate_output_dir(policy_type: str, dataset_repo_id: str) -> str:
    """Build a sortable, collision-free path under outputs/train/.

    LeRobot refuses to write into an existing directory, so each run needs a
    unique leaf. Timestamp + policy + dataset slug makes runs discoverable on
    disk.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dataset_slug = _SLUG_RE.sub("_", dataset_repo_id).strip("_") or "dataset"
    return f"{DEFAULT_OUTPUT_DIR}/{policy_type}_{dataset_slug}_{timestamp}"


class TrainingRequest(BaseModel):
    # Dataset configuration
    dataset_repo_id: str
    dataset_revision: Optional[str] = None
    dataset_root: Optional[str] = None
    dataset_episodes: Optional[List[int]] = None

    # Policy configuration
    policy_type: str = "act"

    # Core training parameters
    steps: int = 10000
    batch_size: int = 8
    seed: Optional[int] = 1000
    num_workers: int = 4

    # Logging and checkpointing
    log_freq: int = 250
    save_freq: int = 1000
    eval_freq: int = 0
    save_checkpoint: bool = True

    # Output configuration
    output_dir: str = "outputs/train"
    resume: bool = False
    job_name: Optional[str] = None

    # Weights & Biases
    wandb_enable: bool = False
    wandb_project: Optional[str] = None
    wandb_entity: Optional[str] = None
    wandb_notes: Optional[str] = None
    wandb_run_id: Optional[str] = None
    wandb_mode: Optional[str] = "online"
    wandb_disable_artifact: bool = False

    # Environment / evaluation
    env_type: Optional[str] = None
    env_task: Optional[str] = None
    eval_n_episodes: int = 10
    eval_batch_size: int = 50
    eval_use_async_envs: bool = False

    # Policy-specific
    policy_device: Optional[str] = "cuda"
    policy_use_amp: bool = False

    # Optimizer
    optimizer_type: Optional[str] = "adam"
    optimizer_lr: Optional[float] = None
    optimizer_weight_decay: Optional[float] = None
    optimizer_grad_clip_norm: Optional[float] = None

    # Advanced
    use_policy_training_preset: bool = True
    config_path: Optional[str] = None


def build_training_command(request: TrainingRequest, output_dir: str) -> List[str]:
    """Build the argv list to invoke `python -m lerobot.scripts.lerobot_train`.

    `output_dir` is supplied separately from the request so the caller (the
    JobRegistry) can pin it to the per-job directory rather than relying on
    request.output_dir, which the frontend doesn't even send in the new world.
    """
    cmd: List[str] = ["python", "-m", "lerobot.scripts.lerobot_train"]

    # Dataset
    cmd.extend(["--dataset.repo_id", request.dataset_repo_id])
    if request.dataset_revision:
        cmd.extend(["--dataset.revision", request.dataset_revision])
    if request.dataset_root:
        cmd.extend(["--dataset.root", request.dataset_root])
    if request.dataset_episodes:
        cmd.extend(["--dataset.episodes"] + [str(ep) for ep in request.dataset_episodes])

    # Policy
    cmd.extend(["--policy.type", request.policy_type])

    # Core training params
    cmd.extend(["--steps", str(request.steps)])
    cmd.extend(["--batch_size", str(request.batch_size)])
    cmd.extend(["--num_workers", str(request.num_workers)])
    if request.seed is not None:
        cmd.extend(["--seed", str(request.seed)])

    # Policy device / AMP / hub
    if request.policy_device:
        cmd.extend(["--policy.device", request.policy_device])
    cmd.extend(["--policy.use_amp", "true" if request.policy_use_amp else "false"])
    # LeRobot defaults push_to_hub=True and then demands --policy.repo_id.
    # Keep training local by default; uploading is a deliberate action.
    cmd.extend(["--policy.push_to_hub", "false"])

    # Logging / checkpointing
    cmd.extend(["--log_freq", str(request.log_freq)])
    cmd.extend(["--save_freq", str(request.save_freq)])
    cmd.extend(["--eval_freq", str(request.eval_freq)])
    cmd.extend(["--save_checkpoint", "true" if request.save_checkpoint else "false"])

    # Output
    cmd.extend(["--output_dir", output_dir])
    cmd.extend(["--resume", "true" if request.resume else "false"])
    if request.job_name:
        cmd.extend(["--job_name", request.job_name])

    # W&B
    cmd.extend(["--wandb.enable", "true" if request.wandb_enable else "false"])
    if request.wandb_enable:
        if request.wandb_project:
            cmd.extend(["--wandb.project", request.wandb_project])
        if request.wandb_entity:
            cmd.extend(["--wandb.entity", request.wandb_entity])
        if request.wandb_notes:
            cmd.extend(["--wandb.notes", request.wandb_notes])
        if request.wandb_run_id:
            cmd.extend(["--wandb.run_id", request.wandb_run_id])
        if request.wandb_mode:
            cmd.extend(["--wandb.mode", request.wandb_mode])
        cmd.extend(["--wandb.disable_artifact", "true" if request.wandb_disable_artifact else "false"])

    # Env
    if request.env_type:
        cmd.extend(["--env.type", request.env_type])
    if request.env_task:
        cmd.extend(["--env.task", request.env_task])

    # Eval
    cmd.extend(["--eval.n_episodes", str(request.eval_n_episodes)])
    cmd.extend(["--eval.batch_size", str(request.eval_batch_size)])
    cmd.extend(["--eval.use_async_envs", "true" if request.eval_use_async_envs else "false"])

    # Optimizer
    if request.optimizer_type:
        cmd.extend(["--optimizer.type", request.optimizer_type])
    if request.optimizer_lr is not None:
        cmd.extend(["--optimizer.lr", str(request.optimizer_lr)])
    if request.optimizer_weight_decay is not None:
        cmd.extend(["--optimizer.weight_decay", str(request.optimizer_weight_decay)])
    if request.optimizer_grad_clip_norm is not None:
        cmd.extend(["--optimizer.grad_clip_norm", str(request.optimizer_grad_clip_norm)])

    # Advanced
    cmd.extend(["--use_policy_training_preset", "true" if request.use_policy_training_preset else "false"])
    if request.config_path:
        cmd.extend(["--config_path", request.config_path])

    return cmd
```

- [ ] **Step 2: Sanity-check both modules import cleanly together**

Run:

```bash
.venv/bin/python -c "
from app.training import TrainingRequest, build_training_command
from app.jobs import job_registry, LocalJobRunner, TrainingMetrics

req = TrainingRequest(dataset_repo_id='lerobot/pusht', policy_type='act', policy_device='mps')
cmd = build_training_command(req, '/tmp/example-output')
print('cmd has output_dir:', '--output_dir' in cmd, '/tmp/example-output' in cmd)
print('registry initial:', job_registry.list())
"
```

Expected:

```
cmd has output_dir: True True
registry initial: []
```

If the import fails, the most likely cause is a stale circular reference — `app/jobs.py` imports `TrainingRequest` from `app.training`, and `LocalJobRunner.start` lazily imports `build_training_command`. The import order should be: `app.training` → standalone, no imports from `app.jobs`. `app.jobs` imports from `app.training`. That's a clean dependency graph.

- [ ] **Step 3: Commit**

```bash
git add app/training.py
git commit -m "refactor(training): shrink to TrainingRequest + build_training_command"
```

---

### Task 5: Wire the `/jobs` API in `app/main.py`

**Files:**
- Modify: `app/main.py`

Remove the four old training endpoints (`/start-training`, `/stop-training`, `/training-status`, `/training-logs`) and the imports they used. Add the six new `/jobs/...` endpoints driven by `job_registry`.

- [ ] **Step 1: Find and remove the old import block**

Open `app/main.py`. Find this import block (around line 41–47):

```python
# Import our custom training functionality
from .training import (
    TrainingRequest,
    handle_start_training,
    handle_stop_training,
    handle_training_status,
    handle_training_logs,
)
```

Replace it with:

```python
# Training is now job-based; see app/jobs.py.
from .training import TrainingRequest
from .jobs import (
    job_registry,
    JobAlreadyRunningError,
    JobNotFoundError,
    JobNotRunningError,
)
```

- [ ] **Step 2: Find and remove the old training endpoints**

Find the section bracketed by `# TRAINING ENDPOINTS` (around line 342). It contains four route definitions: `@app.post("/start-training")`, `@app.post("/stop-training")`, `@app.get("/training-status")`, `@app.get("/training-logs")`. Delete the entire block — the comment header, the four route functions, and any trailing blank lines that aren't needed for separation.

- [ ] **Step 3: Add the new /jobs endpoints**

In the same place where you removed the TRAINING ENDPOINTS section, insert:

```python
# ============================================================================
# JOB ENDPOINTS
# ============================================================================


@app.post("/jobs/training", status_code=201)
def create_training_job(request: TrainingRequest):
    try:
        record = job_registry.start(request)
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=f"A training job is already running: {exc}")
    return record


@app.get("/jobs")
def list_jobs(limit: int = 10):
    return {"jobs": job_registry.list(limit=limit)}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return job_registry.get(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")


@app.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str):
    try:
        logs = job_registry.drain_logs(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return {"logs": logs}


@app.post("/jobs/{job_id}/stop")
def stop_job(job_id: str):
    try:
        return job_registry.stop(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    except JobNotRunningError:
        raise HTTPException(status_code=409, detail=f"Job {job_id!r} is not running")


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    try:
        job_registry.delete(job_id)
    except JobNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    except JobNotRunningError:
        raise HTTPException(status_code=409, detail=f"Job {job_id!r} is running; stop it first")
```

If `HTTPException` isn't already imported in this file, add it to the existing FastAPI import. Search for `from fastapi import` near the top — if it doesn't already include `HTTPException`, add it.

- [ ] **Step 4: Restart `lelab --dev` and curl-verify**

uvicorn `--reload` should pick up the change automatically; if it doesn't, ask the human controller to restart `lelab --dev`.

```bash
rtk proxy curl -s http://localhost:8000/jobs
```

Expected: `{"jobs":[]}` (or pre-existing records from earlier `outputs/train/*/job.json`).

```bash
rtk proxy curl -s http://localhost:8000/jobs/does-not-exist | head -c 200
```

Expected: `{"detail":"Job 'does-not-exist' not found"}` with status 404.

```bash
rtk proxy curl -s -X POST http://localhost:8000/jobs/training -H 'Content-Type: application/json' -d '{"dataset_repo_id":"lerobot/pusht","policy_type":"act","steps":3,"batch_size":2,"num_workers":0,"log_freq":1,"policy_device":"mps","save_checkpoint":false}' | head -c 400
```

Expected: a JSON `JobRecord` with `"state":"running"`, an auto-generated id matching `act_lerobot_pusht_<timestamp>`, and `"runner":"local"`. Followed within seconds by `step:` lines if you re-curl `/jobs/{id}/logs`.

After the run finishes, `rtk proxy curl -s http://localhost:8000/jobs` should show the record with `"state":"done"` and the final metric values preserved (`current_step` == `total_steps`).

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "feat(jobs): expose /jobs API family, drop old training routes"
```

---

### Task 6: Frontend API helpers (`lib/jobsApi.ts`)

**Files:**
- Create: `frontend/src/lib/jobsApi.ts`

A focused module mirroring the new backend types and exposing tiny fetch helpers built on the project's `useApi` pattern. The frontend never reaches into the page component to construct URLs by hand.

- [ ] **Step 1: Create the file**

Create `/Users/nicolasrabault/Projects/Hackathon/leLab/frontend/src/lib/jobsApi.ts`:

```ts
export type JobState = "running" | "done" | "failed" | "interrupted";

export interface TrainingMetrics {
  current_step: number;
  total_steps: number;
  current_loss: number | null;
  current_lr: number | null;
  grad_norm: number | null;
  eta_seconds: number | null;
}

export interface LogLine {
  timestamp: number;
  message: string;
}

// Mirror of the backend TrainingRequest. The frontend doesn't send all of
// these; defaults on the server fill in the rest.
export interface TrainingRequest {
  dataset_repo_id: string;
  policy_type: string;
  steps: number;
  batch_size: number;
  seed?: number;
  num_workers: number;
  log_freq: number;
  save_freq: number;
  save_checkpoint: boolean;
  resume: boolean;
  wandb_enable: boolean;
  wandb_project?: string;
  wandb_entity?: string;
  wandb_notes?: string;
  wandb_mode?: string;
  wandb_disable_artifact: boolean;
  policy_device?: string;
  policy_use_amp: boolean;
  optimizer_type?: string;
  optimizer_lr?: number;
  optimizer_weight_decay?: number;
  optimizer_grad_clip_norm?: number;
  use_policy_training_preset: boolean;
}

export interface JobRecord {
  id: string;
  name: string;
  state: JobState;
  config: TrainingRequest;
  output_dir: string;
  started_at: number;
  ended_at: number | null;
  exit_code: number | null;
  error_message: string | null;
  metrics: TrainingMetrics;
  runner: "local";
}

type Fetcher = (url: string, options?: RequestInit) => Promise<Response>;

async function expectOk(r: Response, action: string): Promise<Response> {
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      const body = await r.json();
      detail = body.detail || detail;
    } catch {
      // ignore
    }
    throw new Error(`${action} failed: ${detail}`);
  }
  return r;
}

export async function listJobs(
  baseUrl: string,
  fetcher: Fetcher,
  limit = 10,
): Promise<JobRecord[]> {
  const r = await fetcher(`${baseUrl}/jobs?limit=${limit}`);
  await expectOk(r, "List jobs");
  const body = await r.json();
  return body.jobs;
}

export async function getJob(
  baseUrl: string,
  fetcher: Fetcher,
  id: string,
): Promise<JobRecord> {
  const r = await fetcher(`${baseUrl}/jobs/${id}`);
  await expectOk(r, "Get job");
  return r.json();
}

export async function getJobLogs(
  baseUrl: string,
  fetcher: Fetcher,
  id: string,
): Promise<LogLine[]> {
  const r = await fetcher(`${baseUrl}/jobs/${id}/logs`);
  await expectOk(r, "Get job logs");
  const body = await r.json();
  return body.logs;
}

export async function startTrainingJob(
  baseUrl: string,
  fetcher: Fetcher,
  request: TrainingRequest,
): Promise<JobRecord> {
  const r = await fetcher(`${baseUrl}/jobs/training`, {
    method: "POST",
    body: JSON.stringify(request),
  });
  if (r.status === 409) {
    throw new Error("Another training is already running. Stop it first.");
  }
  await expectOk(r, "Start training");
  return r.json();
}

export async function stopJob(
  baseUrl: string,
  fetcher: Fetcher,
  id: string,
): Promise<JobRecord> {
  const r = await fetcher(`${baseUrl}/jobs/${id}/stop`, { method: "POST" });
  await expectOk(r, "Stop job");
  return r.json();
}

export async function deleteJob(
  baseUrl: string,
  fetcher: Fetcher,
  id: string,
): Promise<void> {
  const r = await fetcher(`${baseUrl}/jobs/${id}`, { method: "DELETE" });
  await expectOk(r, "Delete job");
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /Users/nicolasrabault/Projects/Hackathon/leLab/frontend && npm run build 2>&1 | tail -3
```

Expected: build succeeds. The new module is unused at this point (Tasks 7–11 wire it in).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/jobsApi.ts
git commit -m "feat(jobs): frontend types and fetch helpers"
```

---

### Task 7: `JobCard` component

**Files:**
- Create: `frontend/src/components/jobs/JobCard.tsx`

One presentational card per job. Clicking the body navigates to the job's monitoring page; the action button (Stop or Delete) handles its own click and stops propagation.

- [ ] **Step 1: Create the file**

Create `/Users/nicolasrabault/Projects/Hackathon/leLab/frontend/src/components/jobs/JobCard.tsx`:

```tsx
import React from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { JobRecord } from "@/lib/jobsApi";
import { Square, X, AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";

interface Props {
  job: JobRecord;
  onStop: (id: string) => void;
  onDelete: (id: string) => void;
}

function relativeTime(epochSec: number): string {
  const diff = Math.max(0, Date.now() / 1000 - epochSec);
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const statePresentation: Record<
  JobRecord["state"],
  { label: string; color: string; Icon: React.ComponentType<{ className?: string }> }
> = {
  running: { label: "Running", color: "text-green-400", Icon: Loader2 },
  done: { label: "Done", color: "text-slate-400", Icon: CheckCircle2 },
  failed: { label: "Failed", color: "text-red-400", Icon: XCircle },
  interrupted: { label: "Interrupted", color: "text-amber-400", Icon: AlertTriangle },
};

const JobCard: React.FC<Props> = ({ job, onStop, onDelete }) => {
  const navigate = useNavigate();
  const present = statePresentation[job.state];
  const Icon = present.Icon;
  const progressPct =
    job.metrics.total_steps > 0
      ? Math.min(100, (job.metrics.current_step / job.metrics.total_steps) * 100)
      : 0;

  const isRunning = job.state === "running";
  const subtitle = isRunning
    ? `started ${relativeTime(job.started_at)}`
    : job.ended_at != null
    ? `ended ${relativeTime(job.ended_at)}`
    : present.label.toLowerCase();

  const handleAction = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isRunning) {
      if (window.confirm("Stop this run?")) onStop(job.id);
    } else {
      if (window.confirm("Delete this run? This wipes the output directory.")) onDelete(job.id);
    }
  };

  return (
    <Card
      onClick={() => navigate(`/training/${job.id}`)}
      className="bg-slate-800/50 border-slate-700 rounded-xl cursor-pointer hover:border-slate-500 transition-colors"
    >
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className={`flex items-center gap-1.5 text-xs font-semibold ${present.color}`}>
            <Icon className={`w-3.5 h-3.5 ${isRunning ? "animate-spin" : ""}`} />
            {present.label}
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleAction}
            className="h-7 w-7 text-slate-400 hover:text-white"
            aria-label={isRunning ? "Stop job" : "Delete job"}
          >
            {isRunning ? <Square className="w-3.5 h-3.5" /> : <X className="w-3.5 h-3.5" />}
          </Button>
        </div>
        <div>
          <div
            className="text-white font-semibold truncate"
            title={job.name}
          >
            {job.name}
          </div>
          <div className="text-xs text-slate-400">{subtitle}</div>
        </div>
        <div className="relative h-5 w-full overflow-hidden rounded-md bg-slate-900 border border-slate-700">
          <div
            className="h-full bg-gradient-to-r from-blue-500 to-sky-400 transition-[width] duration-500"
            style={{ width: `${progressPct}%` }}
          />
          <div className="absolute inset-0 flex items-center justify-center text-xs font-semibold text-white tabular-nums drop-shadow">
            {progressPct.toFixed(1)}%
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default JobCard;
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/nicolasrabault/Projects/Hackathon/leLab/frontend && npm run build 2>&1 | tail -3
```

Expected: build succeeds. (Component unused at this point.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/jobs/JobCard.tsx
git commit -m "feat(jobs): JobCard component"
```

---

### Task 8: `JobsSection` component

**Files:**
- Create: `frontend/src/components/jobs/JobsSection.tsx`

Owns the polling, fetch error handling, and the grid of cards.

- [ ] **Step 1: Create the file**

Create `/Users/nicolasrabault/Projects/Hackathon/leLab/frontend/src/components/jobs/JobsSection.tsx`:

```tsx
import React, { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useApi } from "@/contexts/ApiContext";
import { useToast } from "@/hooks/use-toast";
import { JobRecord, listJobs, stopJob, deleteJob } from "@/lib/jobsApi";
import JobCard from "./JobCard";
import { RefreshCw } from "lucide-react";

const POLL_INTERVAL_MS = 5000;
const LIMIT = 10;

const JobsSection: React.FC = () => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();

  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await listJobs(baseUrl, fetchWithHeaders, LIMIT);
      setJobs(next);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => {
    let cancelled = false;
    refresh();
    const id = setInterval(() => {
      if (!cancelled) refresh();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [refresh]);

  const handleStop = async (id: string) => {
    try {
      await stopJob(baseUrl, fetchWithHeaders, id);
      toast({ title: "Job stopping" });
      refresh();
    } catch (e) {
      toast({
        title: "Stop failed",
        description: e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteJob(baseUrl, fetchWithHeaders, id);
      toast({ title: "Job removed" });
      refresh();
    } catch (e) {
      toast({
        title: "Delete failed",
        description: e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Jobs</h2>
        <Button
          variant="ghost"
          size="icon"
          onClick={refresh}
          className="h-7 w-7 text-slate-400 hover:text-white"
          aria-label="Refresh jobs"
        >
          <RefreshCw className="w-4 h-4" />
        </Button>
      </div>
      {error ? (
        <p className="text-sm text-red-300">Couldn't load jobs: {error}</p>
      ) : jobs.length === 0 ? (
        <p className="text-sm text-slate-500">
          No training jobs yet. Start one from the Training page.
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} onStop={handleStop} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </section>
  );
};

export default JobsSection;
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/nicolasrabault/Projects/Hackathon/leLab/frontend && npm run build 2>&1 | tail -3
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/jobs/JobsSection.tsx
git commit -m "feat(jobs): JobsSection with polling, stop, delete"
```

---

### Task 9: Mount `JobsSection` on Landing

**Files:**
- Modify: `frontend/src/pages/Landing.tsx`

Add a single `<JobsSection />` at the top of the feature region. Your branch already has separate WIP on the Landing page from the recording redesign — only insert the new section, don't touch unrelated content.

- [ ] **Step 1: Add the import**

At the top of `frontend/src/pages/Landing.tsx`, add:

```tsx
import JobsSection from "@/components/jobs/JobsSection";
```

near the existing `@/components/...` imports.

- [ ] **Step 2: Render the section**

Find the JSX `return (...)` of the `Landing` component. Locate the outermost wrapper that renders the page content (search for the existing `<HfAuthBanner` or the main feature tile grid). Insert `<JobsSection />` inside that wrapper, **above** the feature grid. Suggested placement (look for the heading that introduces the feature tiles, then put `<JobsSection />` before it):

```tsx
<JobsSection />
```

Inside whatever container className wraps the rest of the page (so it inherits the same horizontal padding and max-width). If you find multiple candidate containers, choose the one with `space-y-*` so the section spaces naturally between siblings.

- [ ] **Step 3: Verify build**

```bash
cd /Users/nicolasrabault/Projects/Hackathon/leLab/frontend && npm run build 2>&1 | tail -3
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Landing.tsx
git commit -m "feat(jobs): show Jobs section on Landing"
```

---

### Task 10: Add `/training/:jobId` route

**Files:**
- Modify: `frontend/src/App.tsx`

The route renders the same `Training` component; the component branches on `useParams<{ jobId?: string }>().jobId` to choose configuration vs monitoring mode (Task 11).

- [ ] **Step 1: Add the route**

Open `frontend/src/App.tsx`. Find the existing `<Route path="/training" element={<Training />} />`. Immediately after it, add:

```tsx
<Route path="/training/:jobId" element={<Training />} />
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/nicolasrabault/Projects/Hackathon/leLab/frontend && npm run build 2>&1 | tail -3
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(training): route /training/:jobId to Training page"
```

---

### Task 11: Refactor `Training.tsx` for two-mode rendering

**Files:**
- Modify: `frontend/src/pages/Training.tsx`

Branch on `useParams().jobId`: when absent, show the existing Configuration UI but POST to `/jobs/training` and navigate to `/training/{id}`. When present, fetch the job, show the existing `MonitoringStats` + `TrainingLogs` components driven by the job's metrics and logs, plus a contextual Stop/Delete button. Keep `TrainingExtraGate` wrapping the configuration mode only.

- [ ] **Step 1: Replace `Training.tsx` with this content**

The existing file is ~280 lines and built around the old TrainingManager polling. Replace it wholesale with:

```tsx
import React, { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useToast } from "@/hooks/use-toast";
import { useApi } from "@/contexts/ApiContext";

import { TrainingConfig, TrainingStatus, LogEntry } from "@/components/training/types";
import TrainingHeader from "@/components/training/TrainingHeader";
import ConfigurationTab from "@/components/training/ConfigurationTab";
import MonitoringStats from "@/components/training/monitoring/MonitoringStats";
import TrainingLogs from "@/components/training/monitoring/TrainingLogs";
import TrainingExtraGate from "@/components/training/TrainingExtraGate";

import { Button } from "@/components/ui/button";
import { Loader2, Play, Square, Trash2, ArrowLeft } from "lucide-react";

import { DatasetItem, listDatasets } from "@/lib/replayApi";
import {
  JobRecord,
  TrainingRequest,
  getJob,
  getJobLogs,
  listJobs,
  startTrainingJob,
  stopJob,
  deleteJob,
} from "@/lib/jobsApi";

const POLL_INTERVAL_MS = 1000;

function jobToStatus(job: JobRecord | null, isStarting: boolean): TrainingStatus {
  // Adapter so MonitoringStats can keep its current prop shape.
  if (!job) {
    return {
      training_active: isStarting,
      current_step: 0,
      total_steps: 0,
      available_controls: { stop_training: false, pause_training: false, resume_training: false },
    };
  }
  return {
    training_active: job.state === "running",
    current_step: job.metrics.current_step,
    total_steps: job.metrics.total_steps,
    current_loss: job.metrics.current_loss ?? undefined,
    current_lr: job.metrics.current_lr ?? undefined,
    grad_norm: job.metrics.grad_norm ?? undefined,
    eta_seconds: job.metrics.eta_seconds ?? undefined,
    available_controls: {
      stop_training: job.state === "running",
      pause_training: false,
      resume_training: false,
    },
  };
}

function configToRequest(c: TrainingConfig): TrainingRequest {
  // The backend's TrainingRequest has more optional fields; the form covers
  // the user-meaningful subset.
  return {
    dataset_repo_id: c.dataset_repo_id,
    policy_type: c.policy_type,
    steps: c.steps,
    batch_size: c.batch_size,
    seed: c.seed,
    num_workers: c.num_workers,
    log_freq: c.log_freq,
    save_freq: c.save_freq,
    save_checkpoint: c.save_checkpoint,
    resume: c.resume,
    wandb_enable: c.wandb_enable,
    wandb_project: c.wandb_project,
    wandb_entity: c.wandb_entity,
    wandb_notes: c.wandb_notes,
    wandb_mode: c.wandb_mode,
    wandb_disable_artifact: c.wandb_disable_artifact,
    policy_device: c.policy_device,
    policy_use_amp: c.policy_use_amp,
    optimizer_type: c.optimizer_type,
    optimizer_lr: c.optimizer_lr,
    optimizer_weight_decay: c.optimizer_weight_decay,
    optimizer_grad_clip_norm: c.optimizer_grad_clip_norm,
    use_policy_training_preset: c.use_policy_training_preset,
  };
}

const ConfigurationMode: React.FC = () => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();
  const navigate = useNavigate();

  const [trainingConfig, setTrainingConfig] = useState<TrainingConfig>({
    dataset_repo_id: "",
    policy_type: "act",
    steps: 10000,
    batch_size: 8,
    seed: 1000,
    num_workers: 4,
    log_freq: 250,
    save_freq: 1000,
    save_checkpoint: true,
    resume: false,
    wandb_enable: false,
    wandb_mode: "online",
    wandb_disable_artifact: false,
    policy_device: "cuda",
    policy_use_amp: false,
    optimizer_type: "adam",
    use_policy_training_preset: true,
  });

  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);
  const [trainingExtraAvailable, setTrainingExtraAvailable] = useState<boolean | null>(null);
  const [trainingExtraInstallHint, setTrainingExtraInstallHint] = useState<string>("pip install accelerate");
  const [runningJobExists, setRunningJobExists] = useState<boolean>(false);
  const [isStarting, setIsStarting] = useState(false);

  useEffect(() => {
    setDatasetsLoading(true);
    listDatasets(baseUrl, fetchWithHeaders)
      .then(setDatasets)
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false));
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => {
    fetchWithHeaders(`${baseUrl}/system/training-extra`)
      .then((r) => r.json())
      .then((data: { available: boolean; install_hint: string }) => {
        setTrainingExtraAvailable(data.available);
        setTrainingExtraInstallHint(data.install_hint);
      })
      .catch(() => setTrainingExtraAvailable(true));
  }, [baseUrl, fetchWithHeaders]);

  useEffect(() => {
    listJobs(baseUrl, fetchWithHeaders, 1)
      .then((j) => setRunningJobExists(j.some((r) => r.state === "running")))
      .catch(() => setRunningJobExists(false));
  }, [baseUrl, fetchWithHeaders]);

  const updateConfig = <T extends keyof TrainingConfig>(key: T, value: TrainingConfig[T]) => {
    setTrainingConfig((prev) => ({ ...prev, [key]: value }));
  };

  const handleStart = async () => {
    if (!trainingConfig.dataset_repo_id.trim()) {
      toast({ title: "Error", description: "Dataset repository ID is required", variant: "destructive" });
      return;
    }
    setIsStarting(true);
    try {
      const job = await startTrainingJob(baseUrl, fetchWithHeaders, configToRequest(trainingConfig));
      toast({ title: "Training Started", description: job.name });
      navigate(`/training/${job.id}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast({ title: "Error", description: msg, variant: "destructive" });
      // If the failure was the 409 case, refresh our running-job knowledge.
      listJobs(baseUrl, fetchWithHeaders, 1)
        .then((j) => setRunningJobExists(j.some((r) => r.state === "running")))
        .catch(() => {});
    } finally {
      setIsStarting(false);
    }
  };

  if (trainingExtraAvailable === null) {
    return (
      <div className="min-h-screen bg-slate-900 text-white p-4">
        <div className="max-w-7xl mx-auto">
          <TrainingHeader trainingStatus={jobToStatus(null, false)} />
          <div className="flex items-center justify-center py-24 text-slate-400">
            <Loader2 className="w-6 h-6 animate-spin mr-3" />
            Checking training environment…
          </div>
        </div>
      </div>
    );
  }

  if (trainingExtraAvailable === false) {
    return (
      <div className="min-h-screen bg-slate-900 text-white p-4">
        <div className="max-w-7xl mx-auto">
          <TrainingHeader trainingStatus={jobToStatus(null, false)} />
          <TrainingExtraGate installHint={trainingExtraInstallHint} />
        </div>
      </div>
    );
  }

  const startDisabled = isStarting || !trainingConfig.dataset_repo_id.trim() || runningJobExists;
  const startTooltip = runningJobExists ? "Another training is already running" : undefined;

  return (
    <div className="min-h-screen bg-slate-900 text-white p-4">
      <div className="max-w-7xl mx-auto">
        <TrainingHeader trainingStatus={jobToStatus(null, false)} />
        <ConfigurationTab
          config={trainingConfig}
          updateConfig={updateConfig}
          datasets={datasets}
          datasetsLoading={datasetsLoading}
        />
        <div className="max-w-3xl mx-auto mt-6 flex justify-end">
          <Button
            onClick={handleStart}
            disabled={startDisabled}
            title={startTooltip}
            size="lg"
            className="bg-green-500 hover:bg-green-600 text-white font-semibold px-6"
          >
            {isStarting ? (
              <>
                <Loader2 className="w-5 h-5 mr-2 animate-spin" /> Starting…
              </>
            ) : (
              <>
                <Play className="w-5 h-5 mr-2" /> Start Training
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
};

const MonitoringMode: React.FC<{ jobId: string }> = ({ jobId }) => {
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();
  const navigate = useNavigate();

  const [job, setJob] = useState<JobRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const logContainerRef = useRef<HTMLDivElement>(null);

  // Poll the job + its logs while running.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await getJob(baseUrl, fetchWithHeaders, jobId);
        if (cancelled) return;
        setJob(next);
        if (next.state === "running") {
          const newLogs = await getJobLogs(baseUrl, fetchWithHeaders, jobId);
          if (!cancelled && newLogs.length > 0) {
            setLogs((prev) => [...prev, ...newLogs]);
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    tick();
    const id = setInterval(() => {
      if (!cancelled) {
        if (job?.state && job.state !== "running") return; // pause polling once finished
        tick();
      }
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [baseUrl, fetchWithHeaders, jobId, job?.state]);

  const formatTime = (seconds: number): string => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    return `${hours.toString().padStart(2, "0")}:${minutes
      .toString()
      .padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  };

  const getProgressPercentage = () => {
    if (!job || job.metrics.total_steps === 0) return 0;
    return (job.metrics.current_step / job.metrics.total_steps) * 100;
  };

  const handleStop = async () => {
    if (!job) return;
    if (!window.confirm("Stop this run?")) return;
    try {
      const next = await stopJob(baseUrl, fetchWithHeaders, job.id);
      setJob(next);
      toast({ title: "Stopping…" });
    } catch (e) {
      toast({
        title: "Stop failed",
        description: e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    }
  };

  const handleDelete = async () => {
    if (!job) return;
    if (!window.confirm("Delete this run? This wipes the output directory.")) return;
    try {
      await deleteJob(baseUrl, fetchWithHeaders, job.id);
      toast({ title: "Job removed" });
      navigate("/");
    } catch (e) {
      toast({
        title: "Delete failed",
        description: e instanceof Error ? e.message : String(e),
        variant: "destructive",
      });
    }
  };

  if (error && !job) {
    return (
      <div className="min-h-screen bg-slate-900 text-white p-4">
        <div className="max-w-7xl mx-auto space-y-4">
          <Button variant="ghost" onClick={() => navigate("/")} className="text-slate-400">
            <ArrowLeft className="w-4 h-4 mr-2" /> Back to Jobs
          </Button>
          <p className="text-red-300">Couldn't load job {jobId}: {error}</p>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="min-h-screen bg-slate-900 text-white p-4">
        <div className="max-w-7xl mx-auto flex items-center justify-center py-24 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin mr-3" /> Loading job…
        </div>
      </div>
    );
  }

  const isRunning = job.state === "running";

  return (
    <div className="min-h-screen bg-slate-900 text-white p-4">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" onClick={() => navigate("/")} className="text-slate-400 hover:text-white">
              <ArrowLeft className="w-4 h-4 mr-2" /> Jobs
            </Button>
            <div>
              <h1 className="text-xl font-semibold text-white">{job.name}</h1>
              <p className="text-xs text-slate-400">
                {job.state}
                {job.error_message ? ` — ${job.error_message}` : ""}
              </p>
            </div>
          </div>
          {isRunning ? (
            <Button onClick={handleStop} className="bg-red-500 hover:bg-red-600 text-white">
              <Square className="w-4 h-4 mr-2" /> Stop
            </Button>
          ) : (
            <Button onClick={handleDelete} variant="ghost" className="text-slate-400 hover:text-white">
              <Trash2 className="w-4 h-4 mr-2" /> Delete
            </Button>
          )}
        </div>

        <MonitoringStats
          trainingStatus={jobToStatus(job, false)}
          getProgressPercentage={getProgressPercentage}
          formatTime={formatTime}
        />
        <TrainingLogs logs={logs} logContainerRef={logContainerRef} />
      </div>
    </div>
  );
};

const Training: React.FC = () => {
  const { jobId } = useParams<{ jobId?: string }>();
  return jobId ? <MonitoringMode jobId={jobId} /> : <ConfigurationMode />;
};

export default Training;
```

- [ ] **Step 2: Verify the type adapter handles missing fields**

`MonitoringStats` reads `trainingStatus.current_loss?.toFixed(4)` etc. The adapter `jobToStatus` exposes `metrics.current_loss ?? undefined` — that maps `null` → `undefined`, which the optional-chained `.toFixed` handles correctly.

The `TrainingStatus` interface in `frontend/src/components/training/types.ts` defines those metric fields as `Optional<number>` already (i.e. `number | undefined`). Verify by skimming the file. If the existing types declare `current_loss: number | null`, they'll still accept `undefined` via the optional marker — TypeScript should not complain.

Run:

```bash
cd /Users/nicolasrabault/Projects/Hackathon/leLab/frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds.

If TypeScript complains about a property mismatch between the imported `TrainingConfig`/`TrainingStatus` and what the form components expect, the most likely cause is a stale `output_dir`/`job_name`/`eval_*` reference somewhere — those were removed in a prior refactor. Search for the offending property name in `frontend/src/components/training/` and remove the stale binding.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Training.tsx
git commit -m "feat(training): two-mode page (configure vs monitor by :jobId)"
```

---

### Task 12: Delete the now-unused training components

**Files:**
- Delete: `frontend/src/components/training/TrainingTabs.tsx`
- Delete: `frontend/src/components/training/TrainingControls.tsx`
- Delete: `frontend/src/components/training/MonitoringTab.tsx`

The new Training page no longer renders any of these. Remove them so the directory only contains files that are actually used.

- [ ] **Step 1: Confirm no remaining references**

Run from the repo root:

```bash
grep -rn "TrainingTabs\|TrainingControls\|MonitoringTab" frontend/src --include='*.tsx' --include='*.ts'
```

Expected: no output. If anything matches, fix the stale import before deleting.

- [ ] **Step 2: Delete the files**

```bash
git rm frontend/src/components/training/TrainingTabs.tsx \
       frontend/src/components/training/TrainingControls.tsx \
       frontend/src/components/training/MonitoringTab.tsx
```

- [ ] **Step 3: Verify build**

```bash
cd /Users/nicolasrabault/Projects/Hackathon/leLab/frontend && npm run build 2>&1 | tail -3
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(training): drop dead Tabs/Controls/MonitoringTab components"
```

---

### Task 13: End-to-end smoke test

This task makes no code changes; it confirms the feature works in `lelab --dev`.

**Prerequisite:** restart `lelab --dev` so the new backend module loads cleanly and the registry rebuilds from `outputs/train/`. Ask the human controller to do this; the implementer cannot.

- [ ] **Step 1: Empty state**

Visit `http://localhost:8080/`. Expect a "Jobs" section with "No training jobs yet. Start one from the Training page." (or, if pre-existing `outputs/train/*/job.json` files exist from previous tests, cards for those — that's also fine, just verify they render).

- [ ] **Step 2: Start a training**

Click Training in the feature grid. The Configuration screen appears with EssentialsCard + AdvancedCard + Start button. Pick a small dataset (e.g. `lerobot/pusht`), set `steps` to ~50, set `policy.device` to `mps` (Apple) or `cuda`. Click **Start Training**.

- [ ] **Step 3: Verify navigation and live updates**

The page should immediately navigate to `/training/{some_id}` showing the Monitoring view: the header strip with the job name, the big Progress card, the Loss + LR charts, the Logs panel. Within ~10 seconds (after MPS/CUDA warmup) the Progress percentage should start climbing.

- [ ] **Step 4: Verify Landing reflects the running job**

In a second tab, open `http://localhost:8080/`. The Jobs section should show one card with `Running` badge, the same name, a progress bar climbing in step with the Monitoring view (refreshes every 5 s).

- [ ] **Step 5: Stop from the card**

Click the Stop icon on the running card. Confirm the dialog. Within ~1 s the card should flip to `Done` (or `Failed` if the subprocess returned non-zero).

- [ ] **Step 6: Click the finished card**

Click the body of the finished card. The Monitoring view should open with frozen final metrics and the Delete button visible.

- [ ] **Step 7: Delete and confirm cleanup**

Click Delete. Confirm. The page navigates back to Landing; the card is gone. Verify the directory is gone:

```bash
ls outputs/train/ | grep -c <the job id>
```

Expected: `0`.

- [ ] **Step 8: Restart-survives-as-Interrupted check**

Start a fresh training (with steps high enough to stay running for ~30 s). While it's running, kill `lelab` (Ctrl+C in the terminal running `lelab --dev`). Check that the subprocess died:

```bash
ps -eo pid,command | grep lerobot_train | grep -v grep
```

Expected: no output (the daemonless local subprocess died with its parent).

Restart `lelab --dev`. Visit Landing. The Jobs section should show the previous run's card with `Interrupted` (amber) status. Click into it: the Monitoring view shows the last-persisted metrics; no logs (the runner is gone), no Stop button, but a Delete button.

- [ ] **Step 9: No commit**

This task makes no permanent code changes.

---

## Out-of-scope reminders

These were explicitly excluded by the spec — do not pull them into this plan:

- Remote runners (SSH, Slurm). The `JobRunner` Protocol exists for future plug-in; the only implementation is `LocalJobRunner`.
- A job queue. Concurrency is 1; second `POST /jobs/training` returns 409.
- Recording / teleop / calibration as jobs. Only training.
- Detached local subprocesses that survive `lelab` restart (`Interrupted` is the contract).
- A dedicated history page or filter UI. Last-10 on Landing only.
- User-supplied job names at creation, or post-creation rename. Auto-naming only.
- Resuming a checkpointed training from the UI.
