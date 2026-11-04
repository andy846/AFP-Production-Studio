"""The page cache and the background job manager."""

from __future__ import annotations

import time
import threading

from afpstudio.viewer.cache import PageCache
from afpstudio.viewer.jobs import CANCELLED, COMPLETED, FAILED, JobManager


def wait_for(job):
    """Wait for a job to finish."""
    for _ in range(400):
        if job.state in (COMPLETED, FAILED, CANCELLED):
            return job.state
        time.sleep(0.01)
    return job.state


def test_cache_returns_what_was_put():
    cache = PageCache(1024 * 1024)
    cache.put("doc", 1, "<svg/>")
    assert cache.get("doc", 1) == "<svg/>"
    assert cache.get("doc", 2) is None


def test_cache_evicts_when_over_budget():
    cache = PageCache(100)
    cache.put("doc", 1, "x" * 60)
    cache.put("doc", 2, "y" * 60)
    assert cache.get("doc", 1) is None
    assert cache.get("doc", 2) == "y" * 60
    assert cache.stats()["evictions"] == 1


def test_cache_does_not_store_an_oversized_page():
    cache = PageCache(10)
    cache.put("doc", 1, "x" * 100)
    assert cache.get("doc", 1) is None
    assert cache.stats()["entries"] == 0


def test_cache_can_be_cleared_per_document():
    cache = PageCache(1024 * 1024)
    cache.put("a", 1, "one")
    cache.put("b", 1, "two")
    cache.clear("a")
    assert cache.get("a", 1) is None
    assert cache.get("b", 1) == "two"


def test_job_runs_and_completes():
    manager = JobManager(2)
    job = manager.submit("test", "work", lambda context: 42)
    assert wait_for(job) == COMPLETED
    assert job.result == 42
    assert job.progress == 1.0


def test_job_failure_is_reported():
    manager = JobManager(2)

    def boom(context):
        raise RuntimeError("no")

    job = manager.submit("test", "work", boom)
    assert wait_for(job) == FAILED
    assert "no" in job.error


def test_job_can_be_cancelled():
    manager = JobManager(2)
    started = []

    def slow(context):
        started.append(True)
        for _ in range(500):
            context.check_cancelled()
            time.sleep(0.01)
        return "finished"

    job = manager.submit("test", "work", slow)
    for _ in range(400):
        if started:
            break
        time.sleep(0.01)
    assert manager.cancel(job.id) is True
    assert wait_for(job) == CANCELLED


def test_cancelled_job_cleans_up_published_document():
    manager = JobManager(1)
    published = threading.Event()
    release = threading.Event()
    sessions = {"document"}

    def work(context):
        context.on_cancel(lambda: sessions.discard("document"))
        published.set()
        assert release.wait(5)
        return "document"

    job = manager.submit("test", "work", work)
    assert published.wait(5)
    assert manager.cancel(job.id)
    release.set()
    assert wait_for(job) == CANCELLED
    assert not sessions
    assert job.result is None


def test_job_reports_progress():
    manager = JobManager(2)

    def work(context):
        context.progress(0.5, "half way")
        return "done"

    job = manager.submit("test", "work", work)
    assert wait_for(job) == COMPLETED
    assert job.progress == 1.0


def test_concurrent_jobs_are_bounded():
    manager = JobManager(1)
    running = []
    peak = []

    def work(context):
        running.append(True)
        peak.append(len(running))
        time.sleep(0.05)
        running.pop()
        return True

    jobs = [manager.submit("test", "work", work) for _ in range(4)]
    for job in jobs:
        wait_for(job)
    assert max(peak) <= 1
