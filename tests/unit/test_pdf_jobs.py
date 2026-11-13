import threading
import time

from afpstudio.viewer.jobs import JobManager


def wait(job):
    end = time.monotonic() + 5
    while job.state not in ("completed", "failed", "cancelled") and time.monotonic() < end:
        time.sleep(.01)
    return job.state


def test_pdf_queue_serializes_pdf_without_blocking_other_work():
    manager = JobManager(2)
    started, release = threading.Event(), threading.Event()
    second_started = threading.Event()
    def first(context):
        started.set()
        assert release.wait(4)
    first_job = manager.submit("pdf-export", "first", first)
    assert started.wait(2)
    second = manager.submit("pdf-export", "second", lambda context: second_started.set())
    other = manager.submit("other", "responsive", lambda context: 42)
    try:
        assert wait(other) == "completed"
        assert not second_started.is_set()
    finally:
        release.set()
    assert wait(first_job) == wait(second) == "completed"


def test_pdf_override_timeout_and_structured_failure():
    manager = JobManager(1, timeout_seconds=.01)
    def work(context):
        time.sleep(.03)
        context.check_cancelled()
        class ApprovalError(ValueError):
            code = "incomplete_preview"
            details = {"page": 2, "issues": []}
        raise ApprovalError("Preview is incomplete.")
    job = manager.submit("pdf-export", "export", work, timeout_seconds=1)
    assert wait(job) == "failed"
    assert job.as_dict()["error_code"] == "incomplete_preview"
    assert job.as_dict()["error_details"]["page"] == 2


def test_queued_pdf_can_cancel_before_the_running_pdf_finishes():
    manager = JobManager(2)
    release, started = threading.Event(), threading.Event()
    def running(context):
        started.set()
        release.wait(4)
    first = manager.submit("pdf-export", "first", running)
    assert started.wait(2)
    other = manager.submit("pdf-export", "queued", lambda context: None)
    try:
        assert manager.cancel(other.id)
        assert wait(other) == "cancelled"
        assert first.state == "running"
    finally:
        release.set()
