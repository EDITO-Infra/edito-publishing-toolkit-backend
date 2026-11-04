"""In-process runner and lifecycle state for EPT job-runner runs.

Runs live in the API process memory: they do not survive a restart and are not
visible across replicas.

honey: in-memory, single-process by design; move run state to a shared store
(Redis/DB) if EPT is ever deployed with multiple replicas behind one router.

Cancellation is cooperative at await points: the run's asyncio task is
cancelled, so in-flight HTTP fetches and threadpool batches end at their next
await boundary rather than being hard-killed mid-computation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from typing import Any

from .models import RunStatus

logger = logging.getLogger(__name__)

RUN_ID_PREFIX = "run-"
# honey: terminal runs are pruned lazily on submit; revisit if poll traffic or
# result payloads grow enough to matter.
RUN_RETENTION_SECONDS = 3600.0
MAX_TERMINAL_RUNS = 100
CANCEL_WAIT_SECONDS = 10.0

_TERMINAL: tuple[RunStatus, ...] = ("succeeded", "failed", "cancelled")


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class FeatureRun:
    run_id: str
    operation: str
    owner: str
    status: RunStatus = "queued"
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def public_view(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "operation": self.operation,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class RunManager:
    """Owns run state and the asyncio tasks executing feature operations."""

    def __init__(self) -> None:
        self._runs: dict[str, FeatureRun] = {}

    def submit(
        self,
        *,
        operation: str,
        runner: Any,
        payload: dict[str, Any],
        access_token: str,
        owner: str,
    ) -> FeatureRun:
        """Register a run and schedule its execution on the running event loop."""
        self._prune()
        run = FeatureRun(run_id=RUN_ID_PREFIX + uuid.uuid4().hex, operation=operation, owner=owner)
        self._runs[run.run_id] = run
        run.task = asyncio.get_running_loop().create_task(
            self._execute(run, runner, payload, access_token),
            name=f"job-runner:{run.run_id}",
        )
        logger.info(
            "Run submitted run_id=%s operation=%s owner=%s",
            run.run_id,
            run.operation,
            run.owner,
        )
        return run

    def get(self, run_id: str, *, owner: str) -> FeatureRun | None:
        """Return the run when it exists and belongs to ``owner``; None otherwise.

        Foreign run IDs answer 404 rather than 403 so the ID space is not
        enumerable across callers.
        """
        run = self._runs.get(run_id)
        if run is None or run.owner != owner:
            return None
        return run

    def list_runs(self, owner: str) -> list[FeatureRun]:
        """Return caller-owned runs, newest first."""
        return sorted(
            (run for run in self._runs.values() if run.owner == owner),
            key=lambda run: run.created_at,
            reverse=True,
        )

    async def cancel(self, run: FeatureRun) -> FeatureRun:
        """Cancel a queued or running run; terminal runs are returned unchanged."""
        if run.status in _TERMINAL:
            return run
        if run.task is None or run.task.done():
            run.status = "cancelled"
            run.finished_at = utcnow()
            return run
        run.task.cancel()
        if run.status == "queued":
            # A task cancelled before its coroutine first runs never enters
            # _execute, so the terminal bookkeeping has to happen here.
            run.status = "cancelled"
            run.finished_at = utcnow()
            return run
        done, _ = await asyncio.wait({run.task}, timeout=CANCEL_WAIT_SECONDS)
        if not done:
            logger.warning(
                "Run did not acknowledge cancellation run_id=%s operation=%s",
                run.run_id,
                run.operation,
            )
        return run

    async def _execute(
        self,
        run: FeatureRun,
        runner: Any,
        payload: dict[str, Any],
        access_token: str,
    ) -> None:
        run.status = "running"
        run.started_at = utcnow()
        try:
            run.result = await runner(payload, access_token=access_token)
            run.status = "succeeded"
            logger.info("Run succeeded run_id=%s operation=%s", run.run_id, run.operation)
        except asyncio.CancelledError:
            run.status = "cancelled"
            logger.info("Run cancelled run_id=%s operation=%s", run.run_id, run.operation)
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc) or exc.__class__.__name__
            logger.exception(
                "Run failed run_id=%s operation=%s error=%s",
                run.run_id,
                run.operation,
                run.error,
            )
        run.finished_at = utcnow()

    def _prune(self) -> None:
        """Drop terminal runs past retention, then cap the terminal-run count."""
        now = utcnow()
        terminal = [run for run in self._runs.values() if run.status in _TERMINAL]
        stale = [
            run.run_id
            for run in terminal
            if run.finished_at is not None
            and (now - run.finished_at).total_seconds() > RUN_RETENTION_SECONDS
        ]
        for run_id in stale:
            del self._runs[run_id]
        terminal = [run for run in terminal if run.run_id not in set(stale)]
        excess = sorted(terminal, key=lambda run: run.finished_at or run.created_at)[
            # Keep room for the run about to be submitted.
            : max(0, len(terminal) - MAX_TERMINAL_RUNS + 1)
        ]
        for run in excess:
            del self._runs[run.run_id]


run_manager = RunManager()
