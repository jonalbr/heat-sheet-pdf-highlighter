"""Tk-main-thread scheduling for one isolated PDF preview worker."""

import logging
import multiprocessing

from ..core.marking_preview import marking_preview_worker


class MarkingPreview:
    """Debounce edits, coalesce pending work, and discard obsolete results."""

    def __init__(self, scheduler, on_result):
        self.scheduler = scheduler
        self.on_result = on_result
        self.generation = 0
        self._debounce = None
        self._poll_id = None
        self._process = None
        self._connection = None
        self._pending = None
        self._busy = False
        self._closed = False

    def request(self, request):
        """Invalidate the displayed generation immediately, then wait for typing."""
        if self._closed:
            return
        self.generation += 1
        self._pending = None
        if self._debounce is not None:
            self.scheduler.after_cancel(self._debounce)
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
            self.close()

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
                self.close()
        except EOFError, OSError:
            logging.getLogger(__name__).debug("Preview worker disconnected", exc_info=True)
            self.close()

    def close(self):
        """Release callbacks, IPC handles and the process, including in-flight work."""
        self._closed = True
        for callback in (self._debounce, self._poll_id):
            if callback is not None:
                self.scheduler.after_cancel(callback)
        self._debounce = self._poll_id = self._pending = None
        if self._process is not None:
            if self._process.pid is not None:
                if not self._busy and self._connection is not None:
                    try:
                        self._connection.send(None)
                    except BrokenPipeError, OSError:
                        pass
                    self._process.join(timeout=0.2)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=0.5)
                self._process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._busy = False
