"""Latest-only control, generation isolation, reuse and real spawned lifecycle."""

from collections import deque
import multiprocessing
import queue
import threading
from types import SimpleNamespace

from src.core.marking_preview import ExampleText, PreviewRequest, marking_preview_worker
from src.gui.marking_preview import MarkingPreview
from src.models import MarkingMode


class Scheduler:
    def __init__(self):
        self.callbacks = {}
        self.serial = 0

    def after(self, delay, callback, *args):
        self.serial += 1
        self.callbacks[self.serial] = (delay, callback, args)
        return self.serial

    def after_cancel(self, key):
        self.callbacks.pop(key, None)

    def run(self, key):
        _, callback, args = self.callbacks.pop(key)
        callback(*args)


def request():
    return PreviewRequest(None, "CLUB", True, (), MarkingMode.ALL_MATCHES, ExampleText(
        "Heat 1/3 - approx. 10:30", "Lane", "Swimmer", "YOB", "Club", "Entry time",
        "Lane {0}", "{minutes}:{seconds}.{hundredths}"))


class Connection:
    def __init__(self, messages=()):
        self.messages = deque(messages)

    def poll(self):
        return bool(self.messages)

    def recv(self):
        return self.messages.popleft()


def test_debounce_keeps_latest_and_general_search_uses_300_ms():
    scheduler = Scheduler()
    preview = MarkingPreview(scheduler)
    preview.request(request())
    first = preview._debounce
    preview.request(request(), delay_ms=300)
    assert first not in scheduler.callbacks
    assert scheduler.callbacks[preview._debounce][0] == 300
    scheduler.run(preview._debounce)
    assert preview._query == request()
    preview.close()
    assert scheduler.callbacks == {}


def test_first_request_and_colour_change_can_submit_immediately():
    scheduler = Scheduler()
    preview = MarkingPreview(scheduler)
    preview.request(request(), immediate=True)
    assert preview._query == request()
    assert preview._debounce is None
    preview.close()


def test_worker_handshake_sends_only_latest_snapshot_without_pipe_write_in_tk():
    preview = MarkingPreview(Scheduler())
    preview.request(request(), immediate=True)
    assert preview._outgoing.empty()
    preview._ready_worker = True
    preview._flush()
    snapshot = preview._outgoing.get_nowait()
    assert snapshot == ("sync", 0, None, False, preview.generation, request())
    preview.close()


def test_unsent_snapshots_are_coalesced_even_if_sender_is_blocked():
    preview = MarkingPreview(Scheduler())
    preview._ready_worker = True
    for _ in range(10):
        preview.request(request(), immediate=True)
        preview._flush()
    assert preview._outgoing.qsize() == 1
    assert preview._outgoing.get_nowait()[4] == preview.generation
    preview.close()


def test_stale_document_and_request_results_are_ignored():
    shown = []
    preview = MarkingPreview(Scheduler(), shown.append)
    preview._ready_worker = True
    preview._query = request()
    preview.document_generation = 3
    preview.generation = 9
    preview._connection = Connection([
        ("result", 2, 9, "old document"), ("result", 3, 8, "old options"), ("result", 3, 9, "current"),
        ("progress", 2, 100, 100), ("progress", 3, 7, 10),
    ])
    preview._process = SimpleNamespace(is_alive=lambda: True)
    preview._poll()
    assert shown == ["current"]
    assert (preview.prepared_pages, preview.total_pages) == (7, 10)
    preview._connection = preview._process = None
    preview.close()


def test_document_change_invalidates_cached_result_and_queued_request(tmp_path):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"old")
    preview = MarkingPreview(Scheduler())
    preview.prepare(str(path))
    before = preview.document_generation
    preview.last_request = request()
    preview.last_result = "cached"
    path.write_bytes(b"new source")
    preview.prepare(str(path))
    assert preview.document_generation == before + 1
    assert preview.last_result is preview.last_request is None
    preview.close()


def test_detach_keeps_worker_and_reopen_can_reuse_cached_result():
    shown = []
    preview = MarkingPreview(Scheduler(), shown.append)
    process = object()
    preview._process = process
    preview.last_request, preview.last_result = request(), "cached"
    preview.detach()
    assert preview._process is process
    assert not preview._closed
    assert preview.on_result is None
    preview.subscribe(shown.append)
    preview.request(request(), immediate=True)
    assert shown == ["cached"]
    preview._process = None
    preview.close()


def test_pause_and_resume_are_explicit_control_snapshots():
    preview = MarkingPreview(Scheduler())
    preview._ready_worker = True
    preview.set_paused(True)
    preview._flush()
    assert preview._outgoing.get_nowait()[3] is True
    preview.set_paused(False)
    preview._flush()
    assert preview._outgoing.get_nowait()[3] is False
    preview.close()


def test_pipe_sender_runs_outside_tk_and_cooperates_with_shutdown():
    entered, release = threading.Event(), threading.Event()
    outgoing = queue.Queue()
    failed = threading.Event()
    sent = []

    class Pipe:
        def send(self, value):
            assert threading.current_thread() is not threading.main_thread()
            if value is not None:
                entered.set()
                assert release.wait(2)
            sent.append(value)

    sender = threading.Thread(target=MarkingPreview._send_messages, args=(Pipe(), outgoing, failed))
    sender.start()
    outgoing.put("snapshot")
    try:
        assert entered.wait(2)
    finally:
        outgoing.put(None)
        release.set()
        sender.join(2)
    assert sent == ["snapshot", None]
    assert not failed.is_set()


def test_close_detaches_tk_before_waiting_for_process():
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

    preview = MarkingPreview(Scheduler())
    process = preview._process = Process()
    try:
        preview.close()
        assert entered.wait(2)
        assert preview._process is None
        assert preview.cleanup_worker.is_alive()
    finally:
        release.set()
        preview.cleanup_worker.join(2)
    assert process.closed


def test_disconnected_worker_reports_failure():
    class Pipe:
        def poll(self):
            raise EOFError

        def close(self):
            pass

    errors = []
    preview = MarkingPreview(Scheduler(), on_error=lambda: errors.append(True))
    preview._connection = Pipe()
    preview._poll()
    assert preview._closed
    assert errors == [True]
    preview.cleanup_worker.join(2)


def test_spawn_worker_renders_and_exits():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=marking_preview_worker, args=(child,))
    process.start()
    child.close()
    try:
        assert parent.poll(15)
        assert parent.recv() == ("ready",)
        parent.send(("sync", 1, None, False, 7, request()))
        while True:
            assert parent.poll(15), "Worker did not return an image"
            message = parent.recv()
            if message[0] == "result":
                break
        assert message[1:3] == (1, 7)
        assert message[3].png.startswith(b"\x89PNG")
        parent.send(None)
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
        parent.close()


def test_app_controller_can_warm_close_and_reap_real_worker():
    preview = MarkingPreview(Scheduler())
    preview.warm()
    process = preview._process
    assert process.pid is not None
    preview.close()
    preview.cleanup_worker.join(5)
    assert not preview.cleanup_worker.is_alive()
    assert preview.scheduler.callbacks == {}



def test_worker_checks_new_control_between_occurrences_and_keeps_generations(monkeypatch):
    from src.core import marking_preview

    events = []
    first, second = request(), request()
    incoming = deque([("sync", 1, "first.pdf", False, 10, first)])
    sent = []

    class Pipe:
        def poll(self, timeout=None):
            return bool(incoming)

        def recv(self):
            return incoming.popleft()

        def send(self, message):
            sent.append(message)

        def close(self):
            pass

    class Renderer:
        def __init__(self):
            self.index = SimpleNamespace(pages=[], total=2, complete=False)
            self.query = None
            self.steps = 0

        def prepare(self, path):
            events.append(("prepare", path))

        def start(self, req):
            self.query = SimpleNamespace(can_step=lambda index: True)
            events.append(("start", self.steps))

        def step_query(self):
            self.steps += 1
            events.append(("step", self.steps))
            incoming.append(("sync", 2, "second.pdf", False, 11, second) if self.steps == 1 else None)
            return "image"

        def close(self):
            events.append(("closed",))

    monkeypatch.setattr(marking_preview, "MarkingPreviewRenderer", Renderer)
    marking_preview.marking_preview_worker(Pipe())
    assert events == [("prepare", "first.pdf"), ("start", 0), ("step", 1),
                      ("prepare", "second.pdf"), ("start", 1), ("step", 2), ("closed",)]
    assert [message[1:3] for message in sent if message[0] == "result"] == [(1, 10), (2, 11)]


def test_worker_pause_defers_native_work_until_resume(monkeypatch):
    from src.core import marking_preview

    events = []
    req = request()
    incoming = deque([("sync", 1, None, True, 1, req)])

    class Pipe:
        def poll(self, timeout=None):
            if timeout and not incoming:
                incoming.append(("sync", 1, None, False, 1, req))
            return bool(incoming)

        def recv(self):
            return incoming.popleft()

        def send(self, message):
            pass

        def close(self):
            pass

    class Renderer:
        def __init__(self):
            self.index = SimpleNamespace(pages=[], total=1, complete=False, pause=lambda: events.append("pause"))
            self.query = None

        def prepare(self, path):
            events.append("prepare")

        def start(self, req):
            events.append("start")
            self.query = SimpleNamespace(can_step=lambda index: True)

        def step_query(self):
            events.append("step")
            incoming.append(None)

        def close(self):
            events.append("close")

    monkeypatch.setattr(marking_preview, "MarkingPreviewRenderer", Renderer)
    marking_preview.marking_preview_worker(Pipe())
    assert events == ["prepare", "pause", "start", "step", "close"]


def test_worker_coalesces_control_snapshots_and_reports_error_with_latest_ids(monkeypatch):
    from src.core import marking_preview

    events, sent = [], []
    incoming = deque([
        ("sync", 1, "old.pdf", False, 1, request()),
        ("sync", 2, "current.pdf", False, 7, request()),
    ])

    class Pipe:
        def poll(self):
            return bool(incoming)

        def recv(self):
            return incoming.popleft()

        def send(self, message):
            sent.append(message)

        def close(self):
            events.append("pipe closed")

    class Renderer:
        def prepare(self, path):
            events.append(path)

        def start(self, req):
            raise RuntimeError("render failed")

        def close(self):
            events.append("renderer closed")

    monkeypatch.setattr(marking_preview, "MarkingPreviewRenderer", Renderer)
    marking_preview.marking_preview_worker(Pipe())
    assert sent == [("ready",), ("error", 2, 7)]
    assert events == ["current.pdf", "renderer closed", "pipe closed"]
