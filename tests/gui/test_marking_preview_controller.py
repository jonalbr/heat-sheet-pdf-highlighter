"""Scheduling and actual spawned-worker lifecycle checks."""

import multiprocessing
import time

from src.core.marking_preview import ExampleText, PreviewRequest, marking_preview_worker
from src.gui.marking_preview import MarkingPreview
from src.models import MarkingMode


class Scheduler:
    def __init__(self):
        self.callbacks = {}
        self.serial = 0

    def after(self, delay, callback, *args):
        self.serial += 1
        self.callbacks[self.serial] = (callback, args)
        return self.serial

    def after_cancel(self, key):
        self.callbacks.pop(key, None)

    def run(self, key):
        callback, args = self.callbacks.pop(key)
        callback(*args)


def request():
    return PreviewRequest(
        None,
        "CLUB",
        True,
        (),
        MarkingMode.ALL_MATCHES,
        ExampleText(
            "Heat 1/3 - approx. 10:30",
            "Lane",
            "Swimmer",
            "YOB",
            "Club",
            "Entry time",
            "Lane {0}",
            "{minutes}:{seconds}.{hundredths}",
        ),
    )


def test_debounce_and_latest_pending_request(monkeypatch):
    scheduler = Scheduler()
    preview = MarkingPreview(scheduler, lambda result: None)
    submissions = []
    monkeypatch.setattr(preview, "_submit", lambda: submissions.append(preview._pending))
    preview.request(request())
    first = preview._debounce
    preview.request(request())
    assert first not in scheduler.callbacks
    scheduler.run(preview._debounce)
    assert submissions[-1][0] == 2
    preview._busy = True
    preview.request(request())
    scheduler.run(preview._debounce)
    assert preview._pending[0] == 3
    assert len(submissions) == 1
    preview.close()
    assert scheduler.callbacks == {}


def test_obsolete_results_are_discarded_and_pending_work_submitted(monkeypatch):
    class Connection:
        def poll(self):
            return True

        def recv(self):
            return (1, "obsolete")

    displayed = []
    preview = MarkingPreview(Scheduler(), displayed.append)
    preview.generation = 2
    preview._connection = Connection()
    submitted = []
    monkeypatch.setattr(preview, "_submit", lambda: submitted.append(True))
    preview._poll()
    assert displayed == []
    assert submitted == [True]


def test_spawn_worker_renders_and_exits():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=marking_preview_worker, args=(child,))
    process.start()
    child.close()
    try:
        parent.send((7, request()))
        assert parent.poll(15), "Preview worker did not return an image"
        generation, result = parent.recv()
        assert generation == 7
        assert result.page_number is None
        assert result.png.startswith(b"\x89PNG")
        parent.send(None)
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        process.close()
        parent.close()


def test_controller_close_terminates_inflight_worker():
    scheduler = Scheduler()
    preview = MarkingPreview(scheduler, lambda result: None)
    preview.request(request())
    scheduler.run(preview._debounce)
    process = preview._process
    pid = process.pid
    start = time.monotonic()
    preview.close()
    assert time.monotonic() - start < 3
    preview.cleanup_worker.join(timeout=5)
    assert not preview.cleanup_worker.is_alive()
    assert all(child.pid != pid for child in multiprocessing.active_children())
    assert scheduler.callbacks == {}
    preview.close()



def test_close_detaches_tk_before_waiting_for_process():
    import threading

    entered, release = threading.Event(), threading.Event()

    class Process:
        pid = 123
        alive = True
        closed = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def join(self, timeout=None):
            assert threading.current_thread() is not threading.main_thread()
            entered.set()
            assert release.wait(2)

        def close(self):
            self.closed = True

    preview = MarkingPreview(Scheduler(), lambda result: None)
    process = preview._process = Process()
    preview._busy = True
    try:
        preview.close()
        assert entered.wait(2)
        assert preview._process is None
        assert preview.cleanup_worker.is_alive()
    finally:
        release.set()
        preview.cleanup_worker.join(2)
    assert process.closed



def test_first_request_can_submit_immediately_while_edits_are_debounced(monkeypatch):
    scheduler = Scheduler()
    preview = MarkingPreview(scheduler, lambda result: None)
    submissions = []
    monkeypatch.setattr(preview, "_submit", lambda: submissions.append(preview._pending))
    preview.request(request(), immediate=True)
    assert submissions[0][0] == 1
    assert scheduler.callbacks == {}
    preview.request(request())
    assert len(submissions) == 1
    scheduler.run(preview._debounce)
    assert submissions[1][0] == 2
    preview.close()


def test_disconnected_worker_reports_failure_without_leaving_hidden_dialog():
    class Connection:
        def poll(self):
            raise EOFError

        def close(self):
            pass

    errors = []
    preview = MarkingPreview(Scheduler(), lambda result: None, lambda: errors.append(True))
    preview._connection = Connection()
    preview._poll()
    assert preview._closed
    assert errors == [True]
    preview.cleanup_worker.join(2)
