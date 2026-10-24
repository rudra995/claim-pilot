"""Runs agent work in the background, so a request returns at once and the live trace shows each step.

One job at a time per claim, so two clicks can't race each other through the state machine. A job
that fails is written to the audit log, where the trace and the claim's timeline show it.
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from backend.db import audit

ACTOR = "api"


class Busy(RuntimeError):
    """The claim already has a job running."""


class Runner:
    def __init__(self, db_path: Path | str, workers: int = 4) -> None:
        self.db_path = db_path
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="claim")
        self._jobs: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, claim_id: str, label: str, work: Callable[[], object]) -> None:
        with self._lock:
            if self.busy(claim_id):
                raise Busy(f"{claim_id} is still working on its last step")
            self._jobs[claim_id] = self._pool.submit(self._run, claim_id, label, work)

    def busy(self, claim_id: str) -> bool:
        job = self._jobs.get(claim_id)
        return job is not None and not job.done()

    def wait(self, claim_id: str, timeout: float = 120) -> None:
        """Block until the claim's current job is finished (used by tests and the CLI)."""
        if job := self._jobs.get(claim_id):
            job.result(timeout=timeout)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)

    def _run(self, claim_id: str, label: str, work: Callable[[], object]) -> None:
        try:
            work()
        except Exception as error:  # any failure is put on the record rather than lost in a thread
            audit.log(self.db_path, claim_id, ACTOR, "run_error", {"job": label, "error": repr(error)})
