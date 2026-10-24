"""Tk-main-thread scheduling for one isolated PDF preview worker."""

import logging
import multiprocessing
import threading
from contextlib import suppress
from tkinter import TclError

from ..core.marking_preview import marking_preview_worker


class MarkingPreview:
    """Debounce edits, coalesce pending work, and discard obsolete results."""

    def __init__(self, scheduler, on_result, on_error=None):
        self.scheduler = scheduler
        self.on_result = on_result
        self.on_error = on_error
        self.generation = 0
        self._debounce = None
        self._poll_id = None
        self._process = None
        self._connection = None
        self._pending = None
        self._busy = False
        self._closed = False
        self.cleanup_worker = None

    def request(self, request, *, immediate=False):
        """Invalidate the displayed generation immediately, then wait for typing."""
        if self._closed:
            return
        self.generation += 1
        self._pending = None
        if self._debounce is not None:
            self.scheduler.after_cancel(self._debounce)
        self._debounce = None
        if immediate:
            self._ready(self.generation, request)
        else:
            self._debounce = self.scheduler.after(200, self._ready, self.generation, request)

    def _ready(self, generation, request):
        self._debounce = None
        self._pending = (generation, request)
        if not self._busy:
            self._submit()

    def _submit(self):
        if self._pending is None or self._closed:
            return
        try:
            if self._process is None:
                context = multiprocessing.get_context("spawn")
                self._connection, child = context.Pipe()
                self._process = context.Process(target=marking_preview_worker, args=(child,), daemon=True)
                try:
                    self._process.start()
                finally:
                    child.close()
            self._connection.send(self._pending)
            self._pending = None
            self._busy = True
            self._poll_id = self.scheduler.after(50, self._poll)
        except OSError, ValueError:
            logging.getLogger(__name__).exception("Cannot start preview worker")
            self._failed()

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            if self._connection.poll():
                generation, result = self._connection.recv()
                self._busy = False
                if generation == self.generation:
                    self.on_result(result)
                self._submit()
            elif self._process.is_alive():
                self._poll_id = self.scheduler.after(50, self._poll)
            else:
                self._failed()
        except EOFError, OSError:
            logging.getLogger(__name__).debug("Preview worker disconnected", exc_info=True)
            self._failed()

    def _failed(self):
        """Release a failed worker and let a waiting dialog become usable."""
        self.close()
        if self.on_error is not None:
            self.on_error()

    def close(self):
        """Release callbacks, IPC handles and the process, including in-flight work."""
        self._closed = True
        for callback in (self._debounce, self._poll_id):
            if callback is not None:
                with suppress(TclError):
                    self.scheduler.after_cancel(callback)
        self._debounce = self._poll_id = self._pending = None
        process, connection, busy = self._process, self._connection, self._busy
        self._process = self._connection = None
        self._busy = False

        def cleanup():
            if process is not None and process.pid is not None:
                if not busy and connection is not None:
                    try:
                        connection.send(None)
                    except BrokenPipeError, OSError:
                        pass
                    process.join(timeout=0.2)
                if process.is_alive():
                    process.terminate()
                    process.join()
                process.close()
            if connection is not None:
                connection.close()

        if process is not None or connection is not None:
            self.cleanup_worker = threading.Thread(target=cleanup, daemon=True)
            self.cleanup_worker.start()
