"""Interruptible and bounded subprocess execution primitives."""
import os
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
import _thread

class BoundedOutputCapture:
    """Capture small output completely; for large output retain head and tail."""

    def __init__(self, max_bytes, head_bytes, tail_bytes):
        self.max_bytes = max(1, int(max_bytes))
        self.head_bytes = min(max(0, int(head_bytes)), self.max_bytes)
        self.tail_bytes = min(
            max(0, int(tail_bytes)), self.max_bytes - self.head_bytes
        )
        self.total_bytes = 0
        self._complete = bytearray()
        self._head = None
        self._tail = bytearray()

    def feed(self, chunk):
        if not chunk:
            return
        self.total_bytes += len(chunk)

        if self._head is None:
            if len(self._complete) + len(chunk) <= self.max_bytes:
                self._complete.extend(chunk)
                return

            combined = bytes(self._complete) + chunk
            self._head = combined[:self.head_bytes]
            if self.tail_bytes:
                self._tail.extend(combined[-self.tail_bytes:])
            self._complete = bytearray()
            return

        if self.tail_bytes:
            self._tail.extend(chunk)
            if len(self._tail) > self.tail_bytes:
                del self._tail[:-self.tail_bytes]

    @property
    def truncated(self):
        return self.total_bytes > self.max_bytes

    def get_bytes(self):
        if self._head is None:
            return bytes(self._complete)
        return bytes(self._head) + bytes(self._tail)

    def get_text(self):
        value = self.get_bytes().decode("utf-8", errors="replace")
        if self.truncated:
            value += (
                "\n... OUTPUT TRUNCATED: captured beginning and end only; "
                "original stream was {} bytes ...\n"
            ).format(self.total_bytes)
        return value

def _terminate_process_tree(process, process_group):
    """Terminate the test process and, when possible, its child processes."""
    if process.poll() is not None and not process_group:
        return

    if process_group:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        # Kill any descendants that ignored SIGTERM, even if the group leader
        # (the test runner itself) has already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    else:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

def run_bounded_interruptible_command(
    command, cwd, timeout, output_settings
):
    """Run a command with bounded stdout/stderr, timeout and Ctrl+D support."""
    terminate_children = output_settings["terminate_child_processes"]
    process_group = bool(terminate_children and hasattr(os, "killpg"))
    popen_options = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0
    }
    if process_group:
        popen_options["start_new_session"] = True

    process = subprocess.Popen(command, **popen_options)
    stdout_capture = BoundedOutputCapture(
        output_settings["max_stdout_bytes"],
        output_settings["preserve_output_head_bytes"],
        output_settings["preserve_output_tail_bytes"]
    )
    stderr_capture = BoundedOutputCapture(
        output_settings["max_stderr_bytes"],
        output_settings["preserve_output_head_bytes"],
        output_settings["preserve_output_tail_bytes"]
    )

    def drain(stream, capture):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                capture.feed(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    readers = [
        threading.Thread(
            target=drain, args=(process.stdout, stdout_capture),
            name="shai-test-stdout", daemon=True
        ),
        threading.Thread(
            target=drain, args=(process.stderr, stderr_capture),
            name="shai-test-stderr", daemon=True
        )
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    timed_out = False
    interrupted = False

    try:
        with CtrlDMonitor() as monitor:
            while process.poll() is None:
                if monitor.cancelled.is_set():
                    interrupted = True
                    _terminate_process_tree(process, process_group)
                    break

                if timeout and time.monotonic() - started >= timeout:
                    timed_out = True
                    _terminate_process_tree(process, process_group)
                    break
                time.sleep(0.05)
    except BaseException:
        _terminate_process_tree(process, process_group)
        raise

    if interrupted:
        for reader in readers:
            reader.join(timeout=1.0)
        raise IterationInterrupted()

    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process, process_group)

    for reader in readers:
        reader.join(timeout=2.0)

    # If descendants inherited the pipes, do not wait forever for their EOF.
    readers_still_open = any(reader.is_alive() for reader in readers)
    if readers_still_open and process_group:
        _terminate_process_tree(process, process_group)
        for reader in readers:
            reader.join(timeout=1.0)

    stdout_text = stdout_capture.get_text()
    stderr_text = stderr_capture.get_text()
    truncated = stdout_capture.truncated or stderr_capture.truncated

    return {
        "timed_out": timed_out,
        "interrupted": interrupted,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "returncode": process.returncode,
        "output_truncated": truncated,
        "stdout_total_bytes": stdout_capture.total_bytes,
        "stderr_total_bytes": stderr_capture.total_bytes,
        "duration_seconds": round(time.monotonic() - started, 2)
    }

class IterationInterrupted(Exception):
    """Raised when the user presses Ctrl+D during an agent iteration."""

class CtrlDMonitor:
    """Watch for Ctrl+D (or Ctrl+C) while Shai is processing.

    Other keystrokes are ignored while this monitor is active.
    """

    def __init__(self):
        self.fd = None
        self.original_settings = None
        self.stop_event = threading.Event()
        self.cancelled = threading.Event()
        self.thread = None
        self.enabled = False

    def __enter__(self):
        try:
            if not sys.stdin.isatty():
                return self
            self.fd = sys.stdin.fileno()
            self.original_settings = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            self.enabled = True
            self.thread = threading.Thread(
                target=self._watch, name="shai-ctrl-d", daemon=True
            )
            self.thread.start()
        except (AttributeError, OSError, termios.error):
            self._restore()
        return self

    def _watch(self):
        while not self.stop_event.is_set():
            try:
                readable, _, _ = select.select([self.fd], [], [], 0.1)
                if not readable:
                    continue
                value = os.read(self.fd, 1)
                if value in (b"\x04", b"\x03"):
                    self.cancelled.set()
                    return
            except (OSError, ValueError):
                return

    def _restore(self):
        if self.enabled and self.original_settings is not None:
            try:
                termios.tcsetattr(
                    self.fd, termios.TCSADRAIN, self.original_settings
                )
            except (OSError, termios.error):
                pass
        self.enabled = False

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.3)
        self._restore()
        return False

def run_interruptible_call(function, *args):
    """Run a blocking API call in a daemon thread so Ctrl+D stays responsive."""
    result = {}

    def target():
        try:
            result["value"] = function(*args)
        except BaseException as error:
            result["error"] = error

    with CtrlDMonitor() as monitor:
        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(timeout=0.1)
            if monitor.cancelled.is_set():
                raise IterationInterrupted()

    if "error" in result:
        raise result["error"]
    return result.get("value")

def run_interruptible_command(command, cwd, timeout):
    """Run a command with a timeout and terminate it on Ctrl+D."""
    process = subprocess.Popen(
        command, cwd=str(cwd), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True
    )
    started = time.monotonic()
    try:
        with CtrlDMonitor() as monitor:
            while True:
                if monitor.cancelled.is_set():
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                    raise IterationInterrupted()

                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    process.kill()
                    stdout, stderr = process.communicate()
                    return {
                        "timed_out": True, "stdout": stdout,
                        "stderr": stderr, "returncode": process.returncode
                    }
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(0.2, remaining)
                    )
                    return {
                        "timed_out": False, "stdout": stdout,
                        "stderr": stderr, "returncode": process.returncode
                    }
                except subprocess.TimeoutExpired:
                    continue
    except IterationInterrupted:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
