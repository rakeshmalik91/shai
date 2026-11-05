"""Compact in-place tool activity display.

Conversation text remains ordinary scrolling output. Only a consecutive batch
of tool calls is rendered on one transient terminal line, rewritten in place
as each tool starts/completes. Context and task state are retained internally
for diagnostics but never reserve or freeze terminal rows.
"""
import shutil
import sys
import threading

from modules.terminal_style import colorize


class LiveStatusPanel:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.lock = threading.RLock()
        self.active = False
        self.enabled = False
        self.context = "Context: not calculated"
        self.phase = "Idle"
        self.current_tool = "—"
        self.tool_count = 0
        self.last_tool = "—"
        self.tasks = []
        self.iteration = 0
        self.max_iterations = 0
        self.batch_size = 0
        self.batch_position = 0
        self.line_open = False
        self.batch_tool_counts = {}

    def start(self):
        with self.lock:
            self.active = True
            # No reserved screen area: normal chat output always scrolls.
            self.enabled = bool(getattr(self.stream, "isatty", lambda: False)())

    def stop(self):
        with self.lock:
            self.end_tool_batch()
            self.active = False
            self.enabled = False

    def _render_tool_line(self, state):
        if not self.active or not self.enabled:
            return
        size = shutil.get_terminal_size((80, 24))
        total = self.batch_size or "?"
        visible = "[Shai] Tools {}/{}: {} — {}".format(
            self.batch_position, total, self.current_tool, state
        )
        prefix = colorize("[Shai]", "system", self.stream)
        count = colorize("Tools {}/{}".format(self.batch_position, total),
                         "tool_count", self.stream)
        tool_name = self.current_tool
        status_role = "error" if state == "ERROR" else (
            "success" if state == "done" else "warning"
        )
        status = colorize(state, status_role, self.stream)
        # Keep the status to one physical terminal row. Reserve room for
        # the prefix, counter, separators and state label.
        available_tool_chars = max(8, size.columns - 38)
        if len(tool_name) > available_tool_chars:
            tool_name = tool_name[:available_tool_chars - 1] + "…"
        text = "{} {}: {} — {}".format(
            prefix, count, colorize(tool_name, "tool", self.stream), status
        )
        # Carriage return + erase-to-end rewrites this one line only.
        self.stream.write("\r\033[2K" + text)
        self.stream.flush()
        self.line_open = True

    def begin_tool_batch(self, count):
        with self.lock:
            self.batch_size = max(0, int(count or 0))
            self.batch_position = 0
            self.batch_tool_counts = {}

    def suspend_line(self):
        """End the transient line before prompts or ordinary terminal output."""
        with self.lock:
            if self.line_open:
                self.stream.write("\\n")
                self.stream.flush()
                self.line_open = False

    def end_tool_batch(self):
        with self.lock:
            if self.line_open:
                self.stream.write("\n")
                self.stream.flush()
                self.line_open = False
            self.batch_size = 0
            self.batch_position = 0

    def update(self, **values):
        with self.lock:
            for key, value in values.items():
                if key == "task_plan":
                    self.tasks = [dict(item) for item in (value or [])]
                elif hasattr(self, key):
                    setattr(self, key, value)

    def set_context(self, context):
        self.update(context=context)

    def set_phase(self, phase, iteration=None, max_iterations=None):
        values = {"phase": phase}
        if iteration is not None:
            values["iteration"] = iteration
        if max_iterations is not None:
            values["max_iterations"] = max_iterations
        self.update(**values)

    def tool_started(self, name, detail=None):
        with self.lock:
            self.phase = "Executing tools"
            self.current_tool = name if not detail else "{} ({})".format(name, detail)
            self.tool_count += 1
            self.last_tool = name
            self.batch_position += 1
            self.batch_tool_counts[name] = self.batch_tool_counts.get(name, 0) + 1
            if self.batch_tool_counts[name] > 1:
                self.current_tool = "{} ({} calls)".format(
                    name, self.batch_tool_counts[name]
                )
            self._render_tool_line("running")

    def tool_finished(self, name, error=False):
        with self.lock:
            count = self.batch_tool_counts.get(name, 1)
            # Preserve the parameter preview captured by tool_started().
            # Resetting this to the bare name here made every completed tool
            # line lose its arguments, even though the formatter had them.
            if count > 1:
                self.current_tool = "{} ({} calls)".format(name, count)
            self.phase = "Executing tools"
            self._render_tool_line("ERROR" if error else "done")


STATUS = LiveStatusPanel()
