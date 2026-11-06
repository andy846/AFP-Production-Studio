"""Background jobs with progress and cancellation.

Indexing a large document, running the Alpheus parser over it or
building a production report all take longer than a request should.
They run in a worker thread instead, report their progress, and can be
cancelled by the operator.

The number of concurrent jobs is bounded so a user who clicks import
five times cannot start five full-document parses at the same time.
"""

from __future__ import annotations

import threading
import time
import queue
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

#: Job states.
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATES = (COMPLETED, FAILED, CANCELLED)


class JobCancelled(Exception):
    """Raised inside a job when the operator cancels it."""


class JobQueueFull(RuntimeError):
    """The bounded queue cannot accept another job."""


class JobTimeout(RuntimeError):
    """A cooperative task exceeded its execution deadline."""


@dataclass
class Job:
    """One background job."""

    id: str
    kind: str
    label: str
    state: str = QUEUED
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    committed: bool = False
    created: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished: str | None = None
    document_id: str | None = None
    related_document_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "created": self.created,
            "finished": self.finished,
            "has_result": self.result is not None,
        }


class JobContext:
    """Handed to a job so it can report progress and check for cancellation."""

    def __init__(self, job: Job, cancel: threading.Event, timeout_seconds=900, commit_lock=None) -> None:
        self.job = job
        self._cancel = cancel
        self._cancel_cleanup = None
        self._deadline = time.monotonic() + timeout_seconds
        self._commit_lock = commit_lock or threading.Lock()

    def publish(self, action):
        """Serialize the final atomic publication with cancellation requests."""
        with self._commit_lock:
            self.check_cancelled()
            action()
            self.job.committed = True

    def on_cancel(self, callback: Callable[[], None]) -> None:
        self._cancel_cleanup = callback

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set() and not self.job.committed

    def check_cancelled(self) -> None:
        """Raise :class:`JobCancelled` when the operator asked to stop."""
        if self.job.committed:
            return
        if self._cancel.is_set():
            raise JobCancelled()
        if time.monotonic() >= self._deadline:
            raise JobTimeout("Task exceeded its execution deadline.")

    def progress(self, fraction: float, message: str = "") -> None:
        """Report progress; the fraction is clamped to 0..1."""
        self.check_cancelled()
        self.job.progress = max(0.0, min(1.0, float(fraction)))
        if message:
            self.job.message = message


class JobManager:
    """Runs jobs on a bounded pool of worker threads."""

    def __init__(self, max_concurrent: int = 2, timeout_seconds=900, max_pending=64) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self._jobs: dict[str, Job] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.timeout_seconds = timeout_seconds
        self.max_pending = max_pending
        self._queue = queue.Queue()
        self._workers = set()

    def submit(
        self,
        kind: str,
        label: str,
        work: Callable[[JobContext], Any],
        *,
        document_id: str | None = None,
        related_document_ids: tuple[str, ...] = (),
    ) -> Job:
        """Queue a job and return its handle."""
        job_id = uuid.uuid4().hex
        job = Job(id=job_id, kind=kind, label=label, document_id=document_id,
                  related_document_ids=related_document_ids)
        cancel = threading.Event()
        self.prune(keep=128)
        with self._lock:
            if sum(job.state not in TERMINAL_STATES for job in self._jobs.values()) >= self.max_pending + self.max_concurrent:
                raise JobQueueFull("The job queue is full. Wait for a task to finish.")
            self._jobs[job_id] = job
            self._cancel[job_id] = cancel
            self._queue.put((job_id, work))
            if len(self._workers) < self.max_concurrent:
                thread = threading.Thread(target=self._worker, name=f"afp-job-worker-{job_id[:8]}", daemon=True)
                self._workers.add(thread)
                thread.start()
        return job

    def _worker(self):
        while True:
            try:
                job_id, work = self._queue.get(timeout=1)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._workers.discard(threading.current_thread())
                        return
                continue
            try:
                self._run(job_id, work)
            finally:
                self._queue.task_done()

    def _run(self, job_id: str, work: Callable[[JobContext], Any]) -> None:
        context = None
        try:
            job = self.get(job_id)
            if job is None:
                return
            cancel = self._cancel.get(job_id)
            if cancel is not None and cancel.is_set():
                self._finish(job_id, CANCELLED, "Cancelled before it started.")
                return
            job.state = RUNNING
            context = JobContext(job, cancel or threading.Event(), self.timeout_seconds, self._lock)
            result = work(context)
            context.check_cancelled()
            with self._lock:
                cancelled = context.cancelled
                if not cancelled:
                    job.result = result
                    job.state = COMPLETED
                    job.message = "Done."
                    job.finished = datetime.now(timezone.utc).isoformat()
                    job.progress = 1.0
            if cancelled:
                if context._cancel_cleanup:
                    context._cancel_cleanup()
                self._finish(job_id, CANCELLED, "Cancelled by the operator.")
        except JobCancelled:
            if context and context._cancel_cleanup:
                context._cancel_cleanup()
            self._finish(job_id, CANCELLED, "Cancelled by the operator.")
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            if context and context._cancel_cleanup:
                context._cancel_cleanup()
            job = self.get(job_id)
            detail = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            if job is not None:
                job.error = detail or exc.__class__.__name__
            self._finish(
                job_id, FAILED, job.error if job else "Job failed."
            )

    def _finish(self, job_id: str, state: str, message: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.state = state
        job.message = message
        job.finished = datetime.now(timezone.utc).isoformat()
        if state == COMPLETED:
            job.progress = 1.0

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(
                self._jobs.values(), key=lambda j: j.created, reverse=True
            )

    def cancel(self, job_id: str) -> bool:
        """Ask a job to stop. Returns ``False`` when the job is unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            cancel = self._cancel.get(job_id)
            if job is None or cancel is None or job.state in TERMINAL_STATES or job.committed:
                return False
            cancel.set()
            job.message = "Cancellation requested."
            return True

    def discard_document(self, document_id: str) -> None:
        """Cancel and remove document-owned jobs and their retained results."""
        with self._lock:
            for job_id in [key for key, job in self._jobs.items()
                           if job.document_id == document_id or document_id in job.related_document_ids]:
                event = self._cancel.pop(job_id, None)
                if event is not None:
                    event.set()
                self._jobs.pop(job_id).result = None

    def prune(self, keep: int = 50) -> None:
        """Drop the oldest finished jobs so the table cannot grow forever."""
        with self._lock:
            finished = [
                job
                for job in self._jobs.values()
                if job.state in TERMINAL_STATES
            ]
            finished.sort(key=lambda j: j.finished or j.created)
            for job in finished[:-keep] if len(finished) > keep else []:
                self._jobs.pop(job.id, None)
                self._cancel.pop(job.id, None)
