"""Terminal spinner UI component."""
import sys
import threading

class Spinner:
    """Simple terminal spinner, compatible with Python 3.9 and iSH."""

    def __init__(self, message="Shai is thinking", interval=0.12):
        self.message = message
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread = None
        self._frames = ("|", "/", "-", "\\")

    def _run(self):
        index = 0
        while not self._stop_event.is_set():
            frame = self._frames[index % len(self._frames)]
            sys.stdout.write("\r{} {}".format(self.message, frame))
            sys.stdout.flush()
            index += 1
            self._stop_event.wait(self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        return self

    def stop(self):
        if self._thread:
            self._stop_event.set()
            self._thread.join()
            self._thread = None
        sys.stdout.write("\r" + (" " * (len(self.message) + 4)) + "\r")
        sys.stdout.flush()
