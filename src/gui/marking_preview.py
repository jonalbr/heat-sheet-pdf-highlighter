"""App-owned, latest-only scheduling for one warm PDF preview process."""

import logging
import multiprocessing
import queue
import threading
from contextlib import suppress
from tkinter import TclError

from ..core.marking_preview import marking_preview_worker
from ..core.preview_index import source_identity


class MarkingPreview:
    """Keep prepared data across dialogs; never deliver another document's result."""

    def __init__(self, scheduler, on_result=None, on_error=None):
        self.scheduler = scheduler
        self.on_result = on_result
        self.on_error = on_error
        self.on_progress = None
        self.generation = 0
        self.document_generation = 0
        self._identity = None
        self._source_path = None
        self._debounce = self._poll_id = self._flush_id = None
        self._process = self._connection = None
        self._pending = self._query = None
        self._closed = False
        self._ready_worker = False
        self._dirty = False
        self._paused = False
        self._busy = False
        self.cleanup_worker = None
        self.last_request = self.last_result = None
        self.prepared_pages = self.total_pages = 0
        self._outgoing = queue.Queue(maxsize=1)
        self._sender = None
        self._send_failed = threading.Event()

    def warm(self):
        """Spawn after the app is visible; no PDF work is performed by Tk."""
        if self._closed or self._process is not None:
            return
        try:
            context = multiprocessing.get_context("spawn")
            self._connection, child = context.Pipe()
            self._process = context.Process(target=marking_preview_worker, args=(child,), daemon=True)
            try:
                self._process.start()
            finally:
                child.close()
            self._sender = threading.Thread(target=self._send_messages,
                args=(self._connection, self._outgoing, self._send_failed), daemon=True)
            self._sender.start()
            self._poll_id = self.scheduler.after(16, self._poll)
        except OSError, ValueError:
            logging.getLogger(__name__).exception("Cannot start preview worker")
            self._failed()

    def subscribe(self, on_result, on_error=None, on_progress=None):
        self.on_result, self.on_error, self.on_progress = on_result, on_error, on_progress

    def prepare(self, source_path):
        """Begin preparing a new document as soon as file selection finishes."""
        identity = source_identity(source_path)
        if identity != self._identity:
            self.document_generation += 1
            self._identity = identity
            self._source_path = source_path
            self.generation += 1
            self._pending = self._query = None
            self.last_request = self.last_result = None
            self.prepared_pages = self.total_pages = 0
            self._queue_sync()

    def request(self, request, *, immediate=False, delay_ms=200):
        """Invalidate immediately; debounce only the requested kind of editing."""
        if self._closed:
            return
        self.prepare(request.source_path)
        self.generation += 1
        self._pending = self._query = None
        if self._debounce is not None:
            self.scheduler.after_cancel(self._debounce)
        self._debounce = None
        if self.last_request == request and self.last_result is not None and self.on_result:
            self.on_result(self.last_result)
        if immediate:
            self._ready(self.generation, request)
        else:
            self._queue_sync()  # Cancel obsolete work during the typing pause.
            self._debounce = self.scheduler.after(delay_ms, self._ready, self.generation, request)

    def _ready(self, generation, request):
        self._debounce = None
        if generation == self.generation:
            self._pending = (generation, request)
            self._submit()

    def _submit(self):
        if self._pending is not None:
            _, self._query = self._pending
            self._pending = None
        self._queue_sync()

    def _queue_sync(self):
        if self._closed:
            return
        self._dirty = True
        if self._ready_worker and self._flush_id is None:
            self._flush_id = self.scheduler.after(0, self._flush)

    @staticmethod
    def _send_messages(connection, outgoing, failed):
        """Pipe writes may block during native work, so never perform them in Tk."""
        try:
            while True:
                message = outgoing.get()
                connection.send(message)
                if message is None:
                    return
        except OSError, ValueError:
            failed.set()

    def _enqueue(self, message):
        """Retain just the newest unsent control snapshot without blocking Tk."""
        try:
            self._outgoing.put_nowait(message)
        except queue.Full:
            with suppress(queue.Empty):
                self._outgoing.get_nowait()
            self._outgoing.put_nowait(message)

    def _flush(self):
        self._flush_id = None
        if self._closed or not self._ready_worker or not self._dirty:
            return
        self._enqueue(("sync", self.document_generation, self._source_path,
                       self._paused, self.generation, self._query))
        self._dirty = False

    def set_paused(self, paused):
        if self._closed:
            return
        if not paused:
            self.prepare(self._source_path)
        if paused != self._paused:
            self._paused = paused
            self._queue_sync()

    def detach(self):
        """Cancel dialog work while keeping the current document index warm."""
        self.on_result = self.on_error = self.on_progress = None
        self.generation += 1
        self._pending = self._query = None
        if self._debounce is not None:
            self.scheduler.after_cancel(self._debounce)
            self._debounce = None
        self._queue_sync()

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            if self._send_failed.is_set():
                self._failed()
                return
            for _ in range(100):
                if not self._connection.poll():
                    break
                if not self._handle_message(self._connection.recv()):
                    return
            if self._closed:
                return
            if self._process.is_alive():
                self._poll_id = self.scheduler.after(16, self._poll)
            else:
                self._failed()
        except EOFError, OSError:
            logging.getLogger(__name__).debug("Preview worker disconnected", exc_info=True)
            self._failed()

    def _handle_message(self, message):
        """Deliver current worker messages; return false after a worker error."""
        kind = message[0]
        if kind == "ready":
            self._ready_worker = True
            self._flush()
        elif kind == "result":
            _, doc_id, request_id, result = message
            if doc_id == self.document_generation and request_id == self.generation and self._query is not None:
                self.last_request, self.last_result = self._query, result
                if self.on_result:
                    self.on_result(result)
        elif kind == "progress" and message[1] == self.document_generation:
            self.prepared_pages, self.total_pages = message[2:]
            if self.on_progress:
                self.on_progress(self.prepared_pages, self.total_pages)
        elif kind == "error" and message[1:3] == (self.document_generation, self.generation):
            self._failed()
            return False
        return True

    def _failed(self):
        on_error = self.on_error
        self.close()
        if on_error:
            on_error()

    def close(self):
        """Release callbacks, IPC handles and the process, including in-flight work."""
        self._closed = True
        for callback in (self._debounce, self._poll_id, self._flush_id):
            if callback is not None:
                with suppress(TclError):
                    self.scheduler.after_cancel(callback)
        self._debounce = self._poll_id = self._flush_id = self._pending = None
        process, connection, sender = self._process, self._connection, self._sender
        self._process = self._connection = self._sender = None
        self._busy = False
        self._enqueue(None)

        def cleanup():
            if process is not None and process.pid is not None:
                process.join(timeout=0.2)
                if process.is_alive():
                    process.terminate()
                    process.join()
                process.close()
            if connection is not None:
                connection.close()
            if sender is not None:
                sender.join()

        if process is not None or connection is not None:
            self.cleanup_worker = threading.Thread(target=cleanup, daemon=True)
            self.cleanup_worker.start()
