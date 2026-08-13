from __future__ import annotations

import threading

from fleasion.qml_api.tasks import TaskState


def test_task_worker_is_daemon_and_receives_shutdown_cancellation():
    task = TaskState()
    started = threading.Event()
    cancelled = threading.Event()

    def operation(cancel_event: threading.Event) -> None:
        started.set()
        if cancel_event.wait(timeout=2.0):
            cancelled.set()

    assert task.run_cancellable('Working', operation)
    assert started.wait(timeout=1.0)
    worker = task._thread
    assert worker is not None
    assert worker.daemon

    task.shutdown(wait=True, timeout=1.0)

    assert cancelled.wait(timeout=1.0)
    assert not worker.is_alive()


def test_task_rejects_new_work_after_shutdown():
    task = TaskState()
    task.shutdown()

    assert not task.run('Too late', lambda: None)
