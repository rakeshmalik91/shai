"""Unit tests for the Shai Tkinter GUI and .pyw launcher."""
import os
import pathlib
import queue
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.ui import gui


class GuiHelpersTests(unittest.TestCase):
    def test_strip_ansi_removes_escape_sequences(self):
        colored = "\033[1;32m[ShAI]\033[0m Hello \033[31mworld\033[0m"
        self.assertEqual(gui.strip_ansi(colored), "[ShAI] Hello world")

    def test_format_tool_label_for_ui_formats_json_params(self):
        self.assertEqual(gui.format_tool_label_for_ui("git_status ({})"), "git_status")
        self.assertEqual(
            gui.format_tool_label_for_ui('read_file ({"limit":200,"offset":0,"path":"a.py"})'),
            "read_file (path=a.py, offset=0, limit=200)",
        )
        self.assertEqual(gui.format_tool_label_for_ui("read_file (2 calls)"), "read_file (2 calls)")

    def test_queue_text_stream_captures_and_reports_non_tty(self):
        q = queue.Queue()
        stream = gui.QueueTextStream(q, tag="system")
        self.assertFalse(stream.isatty())
        self.assertTrue(stream.supports_ansi_colors)
        written = stream.write("\033[34mhello\033[0m\n")
        stream.flush()
        self.assertGreater(written, 0)
        kind, payload, tag = q.get_nowait()
        self.assertEqual((kind, payload, tag), ("output", "\033[34mhello\033[0m\n", "system"))

    def test_parse_ansi_segments_extracts_posix_colors_and_bold(self):
        segments, state = gui.parse_ansi_segments("\033[1;32mOK\033[0m plain \033[2;37mdim\033[0m")
        self.assertEqual(segments[0], ("OK", {"fg": 32, "bg": None, "bold": True, "dim": False, "underline": False}))
        self.assertEqual(segments[1], (" plain ", {"fg": None, "bg": None, "bold": False, "dim": False, "underline": False}))
        self.assertEqual(segments[2], ("dim", {"fg": 37, "bg": None, "bold": False, "dim": True, "underline": False}))
        self.assertFalse(any(state.values()))

    def test_split_markdown_code_blocks(self):
        text = "Intro\n```python\na = 1\nb = 2\n```\nOutro"
        parts = gui.split_markdown_code_blocks(text)
        self.assertEqual(parts, [
            ("text", "Intro\n"),
            ("code", "python", ["a = 1", "b = 2"]),
            ("text", "\nOutro"),
        ])

        diff_text = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n"
            '-s = "```python\\na\\n```"\n+s = "```python\\nb\\n```"\n'
        )
        diff_parts = gui.split_markdown_code_blocks(diff_text)
        self.assertEqual(len(diff_parts), 1)
        self.assertEqual(diff_parts[0][0], "code")
        self.assertEqual(diff_parts[0][1], "diff")
        self.assertEqual(len(diff_parts[0][2]), 6)

    def test_parse_markdown_inline_formats_bold_italic_code_and_links(self):
        spans = gui.parse_markdown_inline("Use **bold**, **`boldcode`**, *italic*, `code`, and [docs](https://example.com).")
        self.assertIn(("bold", ("md_bold",)), spans)
        self.assertIn(("boldcode", ("md_bold", "md_code")), spans)
        self.assertIn(("italic", ("md_italic",)), spans)
        self.assertIn(("code", ("md_code",)), spans)
        self.assertIn(("docs", ("md_link",)), spans)
        # Intraword underscores (e.g. env var names) must not become italics
        env_spans = gui.parse_markdown_inline("Active: OPENROUTER_API_KEY, GEMINI_API_KEY")
        self.assertEqual(env_spans, [("Active: OPENROUTER_API_KEY, GEMINI_API_KEY", ())])

    def test_split_markdown_tables_extracts_headers_and_rows(self):
        md = (
            "Summary:\n"
            "| Area | Details |\n"
            "|------|---------|\n"
            "| Models | Kilo, Ollama |\n"
            "| GUI | Tkinter |\n"
            "Done.\n"
        )
        parts = gui.split_markdown_tables(md)
        self.assertEqual(parts[0], ("text", "Summary:\n"))
        self.assertEqual(parts[1], ("table", ["Area", "Details"], [["Models", "Kilo, Ollama"], ["GUI", "Tkinter"]]))
        self.assertEqual(parts[2], ("text", "Done.\n"))

    def test_queue_text_stream_routes_carriage_return_spinner_to_progress(self):
        q = queue.Queue()
        stream = gui.QueueTextStream(q, tag="system")
        stream.write("\rShai is thinking | Prompt estimate: 100 tokens |")
        stream.write("\rShai is thinking | Prompt estimate: 100 tokens /")
        stream.write("\r" + (" " * 40) + "\r")
        stream.write("Hi! How can I help you today?\n")

        events = []
        while not q.empty():
            events.append(q.get_nowait())

        self.assertEqual(events, [
            ("progress", "Shai is thinking | Prompt estimate: 100 tokens |", None),
            ("progress", "Shai is thinking | Prompt estimate: 100 tokens /", None),
            ("progress", "", None),
            ("output", "Hi! How can I help you today?\n", "system"),
        ])

    def test_gui_interaction_handler_confirm_action_and_all(self):
        handler = gui.GuiInteractionHandler(lambda fn: "yes")
        with patch.object(gui, "_ChoiceDialog", create=True):
            self.assertTrue(handler.confirm_action("Proceed?"))

        fake_settings = {"all": False}
        handler_all = gui.GuiInteractionHandler(lambda fn: "all")
        with patch.object(gui.engine, "load_approval_settings", return_value=fake_settings), \
             patch.object(gui.engine, "save_approval_settings") as save_mock:
            self.assertTrue(handler_all.confirm_action("Proceed?"))
            self.assertTrue(fake_settings["all"])
            save_mock.assert_called_once_with(fake_settings)

    def test_gui_interaction_handler_os_command_permission(self):
        handler = gui.GuiInteractionHandler(lambda fn: "subcommand")
        self.assertEqual(handler.request_os_command_permission("git status", "status"), "subcommand")

        handler_deny = gui.GuiInteractionHandler(lambda fn: None)
        self.assertEqual(handler_deny.request_os_command_permission("git status", "status"), "deny")

    def test_gui_launchers_compile(self):
        for filename in ("gui.py", "gui.pyw"):
            launcher = ROOT / filename
            self.assertTrue(launcher.is_file(), f"Missing {filename}")
            source = launcher.read_text(encoding="utf-8")
            compile(source, str(launcher), "exec")

    def test_token_store_save_and_load_into_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = pathlib.Path(tmp) / "tokens.json"
            gui.save_token_store({
                "KILO_API_KEY": "  kilo-secret  ",
                "OPENAI_API_KEY": "",
            }, path=store_path)
            self.assertTrue(store_path.is_file())

            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(gui, "load_windows_user_env_vars", return_value=[]):
                loaded = gui.load_saved_tokens_into_env(path=store_path)
                self.assertEqual(loaded, ["KILO_API_KEY"])
                self.assertEqual(os.environ.get("KILO_API_KEY"), "kilo-secret")
                self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_token_env_specs_and_apply_token_env_vars(self):
        specs = dict(gui.get_token_env_specs())
        self.assertIn("OPENAI_API_KEY", specs)
        self.assertIn("GEMINI_API_KEY", specs)
        self.assertIn("ANTHROPIC_API_KEY", specs)
        self.assertIn("OPENROUTER_API_KEY", specs)
        self.assertIn("KILO_API_KEY", specs)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "old-key"}, clear=False):
            active = gui.apply_token_env_vars({
                "OPENAI_API_KEY": "  new-openai-key  ",
                "GEMINI_API_KEY": "",
            })
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "new-openai-key")
            self.assertNotIn("GEMINI_API_KEY", os.environ)
            self.assertEqual(active, ["OPENAI_API_KEY"])

            active_cleared = gui.apply_token_env_vars({"OPENAI_API_KEY": "   "})
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertEqual(active_cleared, [])


class ShaiGuiAppTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tk_root = tk.Tk()
            self.tk_root.withdraw()
        except tk.TclError as exc:
            self.skipTest(f"Tk display not available: {exc}")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_root = gui.engine.ROOT
        self.orig_provider = gui.engine.PROVIDER
        self.orig_model = gui.engine.MODEL
        gui.engine.set_project_root(self.temp_dir.name)
        self.state_patch = patch.object(gui, "APP_STATE_ROOT", pathlib.Path(self.temp_dir.name))
        self.state_patch.start()
        self.wake_patch = patch.object(gui, "WakeWordSession")
        self.wake_patch.start()
        self.sound_patch = patch.object(gui, "play_speech_cue")
        self.sound_patch.start()

    def tearDown(self):
        self.sound_patch.stop()
        self.wake_patch.stop()
        self.state_patch.stop()
        gui.engine.set_project_root(self.orig_root)
        gui.engine.PROVIDER = self.orig_provider
        gui.engine.MODEL = self.orig_model
        self.temp_dir.cleanup()
        if hasattr(self, "tk_root"):
            try:
                self.tk_root.destroy()
            except tk.TclError:
                pass

    def test_mode_switching_and_ask_vs_agent_turns(self):
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}):
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            try:
                self.tk_root.update_idletasks()
                expected_w = max(420, int(self.tk_root.winfo_screenwidth() * 0.30))
                expected_h = max(420, int(self.tk_root.winfo_screenheight() * 0.80))
                expected_x = max(0, self.tk_root.winfo_screenwidth() - expected_w)
                expected_y = max(0, self.tk_root.winfo_screenheight() - expected_h)
                self.assertEqual(
                    self.tk_root.geometry(),
                    f"{expected_w}x{expected_h}+{expected_x}+{expected_y}",
                )
                self.assertEqual(app._mode, "agent")
                self.assertEqual(app.transcript_scrollbar.cget("style"), "ShAI.Vertical.TScrollbar")
                self.assertFalse(hasattr(app, "model_btn"))
                self.assertEqual(app.provider_var.get(), "openai")
                self.assertEqual(app.model_var.get(), "gpt-4o")
                app.provider_var.set("gemini")
                app.model_var.set("gemini-2.5-pro")
                with patch.object(gui, "save_provider_config"), \
                     patch.object(gui, "get_selected_model_details", return_value={}):
                    app._on_model_combo()
                self.assertEqual(gui.engine.PROVIDER, "gemini")
                self.assertEqual(gui.engine.MODEL, "gemini-2.5-pro")
                resolved_project = pathlib.Path(self.temp_dir.name).resolve()
                self.assertEqual(self.tk_root.title(), f"ShAI - {resolved_project}")
                self.assertIn(resolved_project.name, app.project_btn.cget("text"))
                self.assertIn(str(resolved_project), app._project_tooltip.text)
                self.assertFalse(hasattr(app, "header_info"))

                app._set_busy(True, "Working...")
                self.assertEqual(str(app.send_btn.cget("text")), "Stop")
                self.assertEqual(app._send_tooltip.text, "Stop current request")
                self.assertEqual(str(app.send_btn.cget("state")), "normal")
                self.assertTrue(all(str(b.cget("state")) == "disabled" for b in app._action_buttons))
                app._on_stop_clicked()
                self.assertTrue(app._cancel_event.is_set())
                self.assertEqual(str(app.send_btn.cget("state")), "disabled")
                app._set_busy(False, "Idle")
                self.assertEqual(str(app.send_btn.cget("text")), "Send")
                self.assertEqual(str(app.send_btn.cget("state")), "normal")
                self.assertFalse(app._cancel_event.is_set())
                self.assertTrue(all(str(b.cget("state")) == "normal" for b in app._action_buttons))

                app._submit_text("/ask")
                self.assertEqual(app._mode, "ask")

                with patch.object(gui.messagebox, "askyesno", return_value=False):
                    app._submit_text("/reset")
                self.assertIn("ShAI GUI ready.", app.transcript.get("1.0", "end-1c"))

                with patch.object(gui.messagebox, "askyesno", return_value=True):
                    app._submit_text("/reset")
                self.assertIn("Conversation history cleared.", app.transcript.get("1.0", "end-1c"))

                with patch.object(gui.engine, "api_request_for_role", return_value={"choices": [{"message": {"content": "Hello from ask"}}]}) as api_mock, \
                     patch.object(gui, "response_text", return_value="Hello from ask"), \
                     patch.object(gui, "log_and_display_usage"), \
                     patch.object(gui, "display_reasoning_summary"):
                    app._run_ask_turn("hi")
                    api_mock.assert_called_once()
                    self.assertEqual(api_mock.call_args[0][1], "chat")
                    self.assertEqual(app.history[-1], {"role": "assistant", "content": "Hello from ask"})

                # Short user bubble should be 1 line tall, not stretched by unmapped displaylines
                app._append_output("You: check last 3 commits\n", "user")
                canvases = [w for w in app.transcript.winfo_children() if isinstance(w, gui.tk.Canvas)]
                self.assertTrue(canvases)
                last_inner = canvases[-1].winfo_children()[0]
                last_body = None
                for child in last_inner.winfo_children():
                    if isinstance(child, gui.tk.Text):
                        last_body = child
                        break
                    for sub in child.winfo_children():
                        if isinstance(sub, gui.tk.Text):
                            last_body = sub
                            break
                self.assertIsNotNone(last_body)
                self.assertEqual(int(last_body.cget("height")), 1)

                # Precision touchpad / small delta mousewheel events over bubbles still scroll transcript smoothly
                ev = MagicMock()
                ev.delta = -30
                with patch.object(app.transcript, "yview_scroll") as scroll_mock:
                    self.assertEqual(app._on_child_mousewheel(ev), "break")
                    scroll_mock.assert_called_once_with(15, "pixels")

                # Middle-mouse drag scrolls vertically only
                ev_drag1 = MagicMock()
                ev_drag1.y_root = 200
                ev_drag2 = MagicMock()
                ev_drag2.y_root = 175
                with patch.object(app.transcript, "yview_scroll") as scroll_mock:
                    self.assertEqual(app._on_transcript_drag_start(ev_drag1), "break")
                    self.assertEqual(app._on_transcript_drag_move(ev_drag2), "break")
                    scroll_mock.assert_called_once_with(25, "pixels")

                # Global mousewheel routes any widget inside transcript_frame to transcript scroll
                ev_global = MagicMock()
                ev_global.delta = 120
                ev_global.x_root = 10
                ev_global.y_root = 10
                with patch.object(app.root, "winfo_containing", return_value=last_body), \
                     patch.object(app.transcript, "yview_scroll") as scroll_mock:
                    self.assertEqual(app._on_global_mousewheel(ev_global), "break")
                    scroll_mock.assert_called_once_with(-60, "pixels")
                with patch.object(app.root, "winfo_containing", return_value=app.input_box), \
                     patch.object(app.transcript, "yview_scroll") as scroll_mock:
                    self.assertIsNone(app._on_global_mousewheel(ev_global))
                    scroll_mock.assert_not_called()

                app._submit_text("/code")
                self.assertEqual(app._mode, "agent")
                self.assertEqual(app.transcript_scrollbar.cget("style"), "ShAI.Vertical.TScrollbar")
                resolved_project = pathlib.Path(self.temp_dir.name).resolve()
                self.assertEqual(self.tk_root.title(), f"ShAI - {resolved_project}")
                self.assertIn(resolved_project.name, app.project_btn.cget("text"))
                self.assertIn(str(resolved_project), app._project_tooltip.text)
                self.assertFalse(hasattr(app, "header_info"))

                app._set_busy(True, "Working...")
                self.assertEqual(str(app.send_btn.cget("text")), "Stop")
                self.assertEqual(app._send_tooltip.text, "Stop current request")
                self.assertEqual(str(app.send_btn.cget("state")), "normal")
                self.assertTrue(all(str(b.cget("state")) == "disabled" for b in app._action_buttons))
                app._on_stop_clicked()
                self.assertTrue(app._cancel_event.is_set())
                self.assertEqual(str(app.send_btn.cget("state")), "disabled")
                app._set_busy(False, "Idle")
                self.assertEqual(str(app.send_btn.cget("text")), "Send")
                self.assertEqual(str(app.send_btn.cget("state")), "normal")
                self.assertFalse(app._cancel_event.is_set())
                self.assertTrue(all(str(b.cget("state")) == "normal" for b in app._action_buttons))

                with patch.object(gui.engine, "ask_model") as ask_mock:
                    app._run_worker_turn("list files")
                    ask_mock.assert_called_once_with(app.history, "list files")

                gui.engine.STATUS.tool_started("read_file", "path='README.md'")
                gui.engine.STATUS.tool_finished("read_file", error=False)
                queued = []
                while not app._event_queue.empty():
                    queued.append(app._event_queue.get_nowait())
                self.assertIn(("tool", "read_file (path='README.md')", False), queued)

                # Consecutive tool calls start expanded while running, then auto-collapse on completion.
                app._append_tool_event("list_files", False)
                app._append_tool_event("read_file (path='README.md')", False)
                self.assertEqual(len(app._tool_batches), 1)
                batch = app._tool_batches[1]
                self.assertEqual(batch["count"], 2)
                self.assertFalse(batch["collapsed"])
                self.assertEqual(str(app.transcript.tag_cget(batch["body_tag"], "elide")), "0")

                app._append_output("Done.\n", "assistant")
                self.assertTrue(batch["collapsed"])
                self.assertEqual(str(app.transcript.tag_cget(batch["body_tag"], "elide")), "1")

                app._toggle_tool_batch(1)
                self.assertFalse(batch["collapsed"])
                self.assertEqual(str(app.transcript.tag_cget(batch["body_tag"], "elide")), "0")

                app._append_tool_event("git_status", False)
                self.assertEqual(len(app._tool_batches), 2)

                # Context meter and task list update above the prompt input box.
                app._refresh_context_meter(force=True)
                self.assertIn("Context:", app.context_var.get())

                orig_plan = list(gui.engine.TASK_PLAN)
                try:
                    gui.engine.TASK_PLAN = [
                        {"title": "Inspect code", "status": "completed"},
                        {"title": "Write tests", "status": "in_progress"},
                    ]
                    app._refresh_task_panel(force=True)
                    self.assertEqual(app.task_header_var.get(), "Tasks (1/2):")
                    self.assertEqual(len(app.task_items_frame.winfo_children()), 2)

                    gui.engine.TASK_PLAN = []
                    app._refresh_task_panel(force=True)
                    self.assertEqual(app.task_panel.winfo_manager(), "")
                finally:
                    gui.engine.TASK_PLAN = orig_plan

                # Busy-turn system stdout output renders inside a ShAI rounded bubble with ANSI colors + markdown
                app._set_busy(True, "Working...")
                before_canvases = len([w for w in app.transcript.winfo_children() if isinstance(w, gui.tk.Canvas)])
                app._append_output("\033[1;36mAgent **reply** from stdout.\033[0m\n", "system")
                canvases = [w for w in app.transcript.winfo_children() if isinstance(w, gui.tk.Canvas)]
                self.assertEqual(len(canvases), before_canvases + 1)
                b_inner = canvases[-1].winfo_children()[0]
                b_body = None
                for child in b_inner.winfo_children():
                    for sub in child.winfo_children():
                        if isinstance(sub, gui.tk.Text):
                            b_body = sub
                            break
                self.assertIsNotNone(b_body)
                self.assertEqual(b_body.get("1.0", "end-1c").strip(), "Agent reply from stdout.")
                # Consecutive assistant bubbles omit the repeated ShAI + timestamp header
                app._append_output("Second part of reply.\n", "assistant")
                canvases2 = [w for w in app.transcript.winfo_children() if isinstance(w, gui.tk.Canvas)]
                second_inner = canvases2[-1].winfo_children()[0]
                self.assertFalse(any(isinstance(c, gui.tk.Label) for c in second_inner.winfo_children()))
                app._set_busy(False, "Idle")

                # Markdown tables render as bordered grid frames inside the transcript
                before_frames = len([w for w in app.transcript.winfo_children() if isinstance(w, gui.tk.Frame)])
                app._append_output(
                    "| Area | Details |\n|---|---|\n| **Models** | Supports multiple providers: Kilo Gateway (free/paid), Ollama (local), with model routing/fallback |\n",
                    "assistant",
                )
                self.tk_root.update()
                after_frames = len([w for w in app.transcript.winfo_children() if isinstance(w, gui.tk.Frame)])
                self.assertGreater(after_frames, before_frames)
                self.assertTrue(app._table_blocks)
                self.assertLessEqual(
                    app._table_blocks[-1]["outer"].winfo_reqwidth(),
                    max(360, app.transcript.winfo_width()),
                )

                # Code blocks > 4 lines start collapsed in an inline boxed container and toggle cleanly.
                code_sample = "Here is code:\n```python\n" + "\n".join(f"line_{i}" for i in range(8)) + "\n```\n"
                app._append_output(code_sample, "assistant")
                self.assertEqual(len(app._code_blocks), 1)
                block = app._code_blocks[1]
                self.assertTrue(block["collapsed"])
                self.assertIsInstance(block["box_frame"], gui.tk.Frame)
                self.assertIsInstance(block["body_text"], gui.tk.Text)
                self.assertIsInstance(block["h_scroll"], gui.ttk.Scrollbar)
                self.assertIsInstance(block["popout_btn"], gui.tk.Button)
                pop_win = app._open_code_popout(block)
                try:
                    self.assertTrue(pop_win.winfo_exists())
                finally:
                    pop_win.destroy()
                self.assertEqual(str(app.transcript.tag_cget(block["body_tag"], "elide")), "1")
                app._toggle_code_block(1)
                self.assertFalse(block["collapsed"])
                self.assertEqual(str(app.transcript.tag_cget(block["body_tag"], "elide")), "0")

                # Long prose via stdout (e.g. agent assistant output) stays normal text; explicit collapse_long collapses.
                app._append_output("\n".join(f"prose line {i}" for i in range(10)) + "\n", "system")
                self.assertEqual(len(app._code_blocks), 1)
                app._append_output("\n".join(f"file_{i}.py" for i in range(10)) + "\n", "system", collapse_long=True)
                self.assertEqual(len(app._code_blocks), 2)
                self.assertTrue(app._code_blocks[2]["collapsed"])

                # Inline diff styling tags are applied to diff lines.
                self.assertEqual(app._diff_line_tag("+added"), "diff_add")
                self.assertEqual(app._diff_line_tag("-removed"), "diff_del")
                self.assertEqual(app._diff_line_tag("@@ -1 +1 @@"), "diff_hunk")
                self.assertEqual(app._diff_line_tag("diff --git a/a.py b/a.py"), "diff_file")
            finally:
                app._on_close()

        saved_state = gui.load_gui_state(self.temp_dir.name)
        self.assertEqual(saved_state.get("mode"), "agent")
        self.assertFalse(saved_state.get("listening"))
        self.assertGreaterEqual(saved_state.get("x", -1), 0)
        self.assertEqual(saved_state.get("last_project"), str(pathlib.Path(self.temp_dir.name).resolve()))
        self.assertGreaterEqual(saved_state.get("width", 0), 420)
        self.assertGreaterEqual(saved_state.get("height", 0), 420)
        self.assertEqual(
            gui.resolve_startup_gui_project([], state_root=self.temp_dir.name),
            str(pathlib.Path(self.temp_dir.name).resolve()),
        )

    def test_app_icon_and_tray_menu_constants(self):
        from modules.ui import tray

        ico_path, png_path = tray.ensure_app_icons()
        self.assertEqual(ico_path.parent.name, "icons")
        self.assertTrue(ico_path.is_file())
        self.assertTrue(png_path.is_file())
        self.assertEqual(ico_path.read_bytes()[:4], b"\x00\x00\x01\x00")
        self.assertEqual(png_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

        mic_ico, mic_png = tray.ensure_listening_icons()
        self.assertTrue(mic_ico.is_file())
        self.assertTrue(mic_png.is_file())
        self.assertEqual(mic_ico.read_bytes()[:4], b"\x00\x00\x01\x00")
        self.assertEqual(mic_png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertNotEqual(mic_png.read_bytes(), png_path.read_bytes())

        trimmed = tray.format_tray_notification("  hello   world\n  " + ("x" * 150), max_len=40)
        self.assertLessEqual(len(trimmed), 40)
        self.assertTrue(trimmed.endswith("..."))

        icon = tray.WindowsTrayIcon(ico_path)
        self.assertEqual(icon.MENU_RESTART, 1002)
        self.assertEqual(icon.MENU_EXIT, 1003)
        self.assertEqual(icon.MENU_TOGGLE_LISTENING, 1004)
        self.assertEqual(icon.MENU_SETTINGS, 1005)
        self.assertFalse(icon._listening)
        icon.set_icon(mic_ico, listening=True)
        self.assertTrue(icon._listening)

    def test_close_and_minimize_hide_to_tray_when_tray_active(self):
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}):
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            tray_mock = MagicMock()
            app._tray = tray_mock
            try:
                app._on_close()
                self.assertEqual(self.tk_root.state(), "withdrawn")
                tray_mock.stop.assert_not_called()

                event = MagicMock()
                event.widget = self.tk_root
                with patch.object(self.tk_root, "state", return_value="iconic"), \
                     patch.object(self.tk_root, "withdraw") as withdraw_mock:
                    app._on_window_unmap(event)
                    withdraw_mock.assert_called_once()

                app._minimize_on_focus_loss = True
                with patch.object(self.tk_root, "state", return_value="normal"), \
                     patch.object(self.tk_root, "focus_displayof", return_value=None), \
                     patch.object(app, "_hide_to_tray") as hide_mock:
                    app._check_focus_loss_hide()
                    hide_mock.assert_called_once()
            finally:
                app._exit_app()
                tray_mock.stop.assert_called_once()

    def test_restart_app_relaunches_and_closes(self):
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}), \
             patch.object(gui.subprocess, "Popen") as popen_mock:
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            app.restart_app()
            popen_mock.assert_called_once()
            cmd = popen_mock.call_args[0][0]
            self.assertEqual(cmd[0], sys.executable)

    def test_restores_old_messages_and_uses_persisted_input_history(self):
        from modules.conversation_history import ConversationHistory

        store = ConversationHistory(self.temp_dir.name, "agent", gui.engine.SYSTEM_PROMPT)
        store.save([
            {"role": "system", "content": gui.engine.SYSTEM_PROMPT},
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ], provider="openai", model="gpt-4o")
        store.add_input("previous question")

        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}):
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            try:
                transcript_text = app.transcript.get("1.0", "end-1c")
                self.assertIn("You: previous question", transcript_text)
                self.assertIn("previous answer", transcript_text)
                self.assertIn("--- Current Session ---", transcript_text)
                canvases = [w for w in app.transcript.winfo_children() if isinstance(w, gui.tk.Canvas)]
                self.assertEqual(len(canvases), 2)
                self.assertEqual(canvases[0].winfo_children()[0].cget("bg"), "#1d2624")
                self.assertEqual(canvases[1].winfo_children()[0].cget("bg"), "#1a1c29")
                self.assertEqual(app._input_history, ["previous question"])

                app.input_box.insert("1.0", "new prompt")
                with patch.object(app, "_submit_text"):
                    app._on_send_clicked()
                self.assertEqual(app._input_history, ["previous question", "new prompt"])
                self.assertEqual(store.input_history(), ["previous question", "new prompt"])
            finally:
                app._on_close()

    def test_settings_dialog_is_centered(self):
        self.tk_root.deiconify()
        self.tk_root.geometry("420x600+1400+400")
        self.tk_root.update()
        dialog = gui.SettingsDialog(self.tk_root)
        try:
            expected_x = max(0, (dialog.winfo_screenwidth() - 600) // 2)
            expected_y = max(0, (dialog.winfo_screenheight() - 380) // 2)
            self.assertTrue(dialog.geometry().endswith(f"+{expected_x}+{expected_y}"))
            self.assertEqual(set(dialog._tab_frames), {"gui", "tokens"})
            self.assertEqual(dialog._active_tab, "gui")
            dialog._select_tab("tokens")
            self.assertEqual(dialog._active_tab, "tokens")
            dialog._select_tab("gui")
            dialog._always_on_top_var.set(True)
            dialog._start_minimized_var.set(True)
            dialog._minimize_to_tray_var.set(False)
            dialog._close_to_tray_var.set(False)
            dialog._minimize_on_focus_loss_var.set(True)
            dialog._sound_cues_var.set(False)
            with patch.object(gui, "save_token_store"), \
                 patch.object(gui, "persist_windows_user_env_vars"):
                dialog._save()
            self.assertEqual(dialog.saved_gui_settings, {
                "always_on_top": True,
                "start_minimized": True,
                "minimize_to_tray": False,
                "close_to_tray": False,
                "minimize_on_focus_loss": True,
                "sound_cues": False,
            })
            # Saving GUI-only changes must not report token updates
            self.assertIsNone(dialog.saved_vars)
            self.assertIsNotNone(dialog.saved_geometry)
            self.assertGreaterEqual(dialog.saved_geometry["width"], 600)
            self.assertGreaterEqual(dialog.saved_geometry["height"], 380)

            custom_geo = {"width": 660, "height": 420, "x": 120, "y": 90}
            dialog2 = gui.SettingsDialog(self.tk_root, geometry_state=custom_geo)
            try:
                dialog2.update_idletasks()
                self.assertEqual(dialog2.geometry(), "660x420+120+90")
            finally:
                dialog2.destroy()
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    def test_tooltip_flips_above_widget_near_bottom_work_area(self):
        btn = tk.Button(self.tk_root, text="Mic")
        tip = gui.ToolTip(btn, "Continuous listening — auto-sends on pause (Ctrl+M)")
        try:
            with patch.object(btn, "winfo_rootx", return_value=1800), \
                 patch.object(btn, "winfo_rooty", return_value=990), \
                 patch.object(btn, "winfo_height", return_value=36), \
                 patch.object(gui.ToolTip, "_work_area", return_value=(0, 0, 1920, 1032)):
                tip.show()
            self.assertIsNotNone(tip._tip_window)
            tip._tip_window.update_idletasks()
            self.assertLess(tip._tip_window.winfo_y(), 990)
            self.assertLessEqual(
                tip._tip_window.winfo_x() + tip._tip_window.winfo_reqwidth(),
                1920,
            )
        finally:
            tip._hide()

    def test_speech_module_script_parsing_and_session(self):
        import io
        import json
        import struct
        from modules.ui import speech

        single_ps = speech.build_powershell_speech_script(continuous=False, timeout_seconds=12)
        self.assertIn("Recognize([TimeSpan]::FromSeconds(12))", single_ps)
        cont_ps = speech.build_powershell_speech_script(continuous=True)
        self.assertIn("RecognizeMode]::Multiple", cont_ps)

        encoded = speech.encode_powershell_command("Write-Output 'hi'")
        self.assertTrue(len(encoded) > 0)

        self.assertEqual(speech.parse_speech_event('{"type":"text","text":"hello"}'), {"type": "text", "text": "hello"})
        self.assertIsNone(speech.parse_speech_event("not-json"))
        self.assertTrue(speech.is_speech_cancel_command("Cancel."))
        self.assertTrue(speech.is_speech_cancel_command("  stop now! "))
        self.assertFalse(speech.is_speech_cancel_command("cancel the timer and write tests"))
        self.assertTrue(speech.is_speech_stop_listening_command("Stop listening."))
        self.assertTrue(speech.is_speech_stop_listening_command("mute mic"))
        self.assertFalse(speech.is_speech_stop_listening_command("stop"))

        wake_ps = speech.build_powershell_wake_word_script(("Shai", "Hey Shai"))
        self.assertIn("'Shai', 'Hey Shai'", wake_ps)
        self.assertIn("RecognizeMode]::Multiple", wake_ps)

        wakes = []
        wake_proc = MagicMock()
        wake_proc.stdout = iter(['{"type":"wake","text":"Shai"}\n'])
        wake_proc.poll.return_value = 0
        with patch.object(speech.sys, "platform", "win32"), \
             patch.object(speech.subprocess, "Popen", return_value=wake_proc):
            ws = speech.WakeWordSession(on_wake=wakes.append)
            ws.start()
            import time
            for _ in range(50):
                if wakes:
                    break
                time.sleep(0.01)
        self.assertEqual(wakes, ["Shai"])

        # PCM RMS & WAV packaging
        pcm = struct.pack("<4h", 1000, -1000, 1000, -1000)
        self.assertAlmostEqual(speech.pcm_rms(pcm), 1000.0, places=2)
        wav = speech.pcm_to_wav_bytes(pcm)
        self.assertTrue(wav.startswith(b"RIFF"))
        chime_wav = speech.build_chime_wav_bytes(((880, 40),))
        self.assertTrue(chime_wav.startswith(b"RIFF"))
        with patch.object(speech.sys, "platform", "win32"), \
             patch("winsound.PlaySound") as play_mock:
            speech.play_speech_cue("start")
            import time
            for _ in range(50):
                if play_mock.called:
                    break
                time.sleep(0.01)
            play_mock.assert_called_once()

        # Backend selection, model_routing.json STT config & Gemini transcription
        stt_cfg = speech.load_stt_models(self.temp_dir.name)
        self.assertIn("whisper-1", stt_cfg["openai"])
        self.assertIn("gemini-3.5-flash-lite", stt_cfg["gemini"])

        custom_routing = pathlib.Path(self.temp_dir.name) / ".shai" / "model_routing.json"
        custom_routing.parent.mkdir(parents=True, exist_ok=True)
        custom_routing.write_text(json.dumps({
            "version": 1,
            "speech_to_text": {"gemini": ["gemini-custom-stt"]},
        }), encoding="utf-8")
        self.assertEqual(
            speech.load_stt_models(self.temp_dir.name)["gemini"],
            ["gemini-custom-stt"],
        )

        with patch.object(speech, "_sounddevice_available", return_value=True), \
             patch.dict(os.environ, {"GEMINI_API_KEY": "gem-key"}, clear=True):
            self.assertEqual(speech.select_speech_backend(), "cloud")
            fake_payload = json.dumps({
                "candidates": [{"content": {"parts": [{"text": "hello gemini"}]}}]
            }).encode("utf-8")
            with patch.object(speech, "_post_gemini_json", return_value=(200, fake_payload)) as post_mock:
                self.assertEqual(speech.transcribe_wav_bytes(wav, project_root=self.temp_dir.name), "hello gemini")
                self.assertIn("gemini-custom-stt", post_mock.call_args[0][0])

        with patch.object(speech, "_sounddevice_available", return_value=False), \
             patch.object(speech.sys, "platform", "win32"):
            self.assertEqual(speech.select_speech_backend(), "windows_sapi")

        texts = []
        errors = []
        done = []
        fake_proc = MagicMock()
        fake_proc.stdout = iter([
            '{"type":"text","text":"hello world"}\n',
            '{"type":"done"}\n',
        ])
        fake_proc.stderr = MagicMock()
        fake_proc.stderr.read.return_value = ""
        fake_proc.poll.return_value = 0
        fake_proc.wait.return_value = 0

        with patch.object(speech, "select_speech_backend", return_value="windows_sapi"), \
             patch.object(speech.subprocess, "Popen", return_value=fake_proc):
            session = speech.SpeechToTextSession(
                continuous=False,
                on_text=texts.append,
                on_error=errors.append,
                on_done=lambda: done.append(True),
            )
            session.start()
            # Wait briefly for reader thread to consume the 2 mocked lines
            import time
            for _ in range(50):
                if done:
                    break
                time.sleep(0.01)

        self.assertEqual(texts, ["hello world"])
        self.assertEqual(errors, [])
        self.assertEqual(done, [True])

    def test_mic_button_single_use_and_continuous_listening(self):
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}):
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            try:
                fake_sessions = []

                def _make_session(continuous=False, on_text=None, on_error=None, on_done=None):
                    sess = MagicMock()
                    sess.continuous = continuous
                    sess.is_running = True
                    fake_sessions.append(sess)
                    return sess

                with patch.object(gui, "SpeechToTextSession", side_effect=_make_session), \
                     patch.object(gui, "play_speech_cue") as cue_mock:
                    tray_mock = MagicMock()
                    app._tray = tray_mock
                    self.assertFalse(hasattr(app, "mic_single_btn"))
                    self.assertIn("Ctrl+M", app._mic_cont_tooltip.text)

                    # Shortcut starts continuous listening & updates tray/taskbar icon
                    self.assertEqual(app._on_mic_cont_shortcut(), "break")
                    cue_mock.assert_called_with("start")
                    self.assertEqual(len(fake_sessions), 1)
                    self.assertTrue(fake_sessions[0].continuous)
                    self.assertEqual(app._speech_mode, "continuous")
                    self.assertEqual(app.mic_cont_btn.cget("bg"), gui.THEME["error"])
                    self.assertIsNotNone(app._speech_anim_after_id)
                    app._tick_speech_animation()
                    self.assertEqual(app.mic_cont_btn.cget("bg"), gui.SPEECH_PULSE_COLORS[1])
                    self.assertIn("Ctrl+M", app._mic_cont_tooltip.text)
                    tray_mock.set_icon.assert_called()
                    self.assertIn("shai_mic.ico", str(tray_mock.set_icon.call_args[0][0]))

                    # While in taskbar/withdrawn, speech_text triggers a trimmed tray notification
                    tray_mock.show_notification.reset_mock()
                    app._event_queue.put(("speech_text", "  heard   in taskbar  ", None))
                    with patch.object(app, "_is_in_taskbar", return_value=True), \
                         patch.object(app, "_refresh_task_panel"), \
                         patch.object(app, "_refresh_context_meter"):
                        app._poll_events()
                    tray_mock.show_notification.assert_called_once_with(
                        "ShAI — Heard", "heard in taskbar"
                    )
                    app.input_box.delete("1.0", tk.END)
                    app._cancel_continuous_auto_send()

                    # Clicking again stops the active session & restores normal icon
                    app._on_mic_cont_clicked()
                    fake_sessions[0].stop.assert_called_once()
                    self.assertIsNone(app._speech_mode)
                    self.assertIsNone(app._speech_anim_after_id)
                    self.assertEqual(app.mic_cont_btn.cget("bg"), gui.THEME["surface_alt"])
                    self.assertIn("shai.ico", str(tray_mock.set_icon.call_args[0][0]))
                    app._tray = None

                    # Start continuous listening again for auto-send checks
                    app._on_mic_cont_clicked()
                    self.assertEqual(len(fake_sessions), 2)
                    self.assertTrue(fake_sessions[1].continuous)

                    # Continuous mode auto-sends on pause
                    app._event_queue.put(("speech_text", "hello", None))
                    app._event_queue.put(("speech_text", "world", None))
                    with patch.object(app, "_submit_text") as submit_mock:
                        # Process queued speech_text events without waiting for full timer
                        for _ in range(2):
                            kind, payload, _tag = app._event_queue.get_nowait()
                            self.assertEqual(kind, "speech_text")
                            app._insert_transcribed_text(payload)
                        self.assertEqual(app.input_box.get("1.0", "end-1c"), "hello world")
                        app._auto_send_continuous_speech()
                        submit_mock.assert_called_once_with("hello world")
                        self.assertEqual(app.input_box.get("1.0", "end-1c"), "")

                    # While busy, voice "stop"/"cancel" triggers _on_stop_clicked without queuing input
                    app._set_busy(True, "Working...")
                    app._event_queue.put(("speech_text", "Cancel.", None))
                    with patch.object(app, "_refresh_task_panel"), \
                         patch.object(app, "_refresh_context_meter"):
                        app._poll_events()
                    self.assertTrue(app._cancel_event.is_set())
                    self.assertEqual(app.input_box.get("1.0", "end-1c"), "")
                    self.assertEqual(app._speech_mode, "continuous")
                    app._set_busy(False, "Idle")

                    # "stop listening" stops continuous listening without queuing input
                    app._event_queue.put(("speech_text", "Stop listening.", None))
                    with patch.object(app, "_refresh_task_panel"), \
                         patch.object(app, "_refresh_context_meter"):
                        app._poll_events()
                    self.assertIsNone(app._speech_mode)
                    self.assertEqual(app.input_box.get("1.0", "end-1c"), "")

                    # Wake word starts continuous listening
                    app._event_queue.put(("wake_word", "Shai", None))
                    with patch.object(app, "_refresh_task_panel"), \
                         patch.object(app, "_refresh_context_meter"):
                        app._poll_events()
                    self.assertEqual(app._speech_mode, "continuous")

                    # While busy, continuous mode keeps listening and defers auto-send until idle
                    app._set_busy(True, "Working...")
                    app._insert_transcribed_text("follow up")
                    with patch.object(app, "_submit_text") as submit_mock:
                        app._auto_send_continuous_speech()
                        submit_mock.assert_not_called()
                        self.assertTrue(app._speech_pending_auto_send)
                        self.assertEqual(app._speech_mode, "continuous")
                        self.assertEqual(app.input_box.get("1.0", "end-1c"), "follow up")

                        app._event_queue.put(("done", None, None))
                        with patch.object(app, "_refresh_task_panel"), \
                             patch.object(app, "_refresh_context_meter"):
                            app._poll_events()
                        submit_mock.assert_called_once_with("follow up")
                        self.assertFalse(app._speech_pending_auto_send)
                        self.assertEqual(app._speech_mode, "continuous")
            finally:
                app._on_close()
                if fake_sessions:
                    fake_sessions[1].stop.assert_called_once()

    def test_restores_listening_state_from_last_launch(self):
        gui.save_gui_state(self.temp_dir.name, {"listening": True})
        fake_sess = MagicMock()
        fake_sess.is_running = True
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}), \
             patch.object(gui, "SpeechToTextSession", return_value=fake_sess) as sess_cls:
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            try:
                self.tk_root.update()
                sess_cls.assert_called_once()
                self.assertEqual(app._speech_mode, "continuous")
            finally:
                app._on_close()
        saved = gui.load_gui_state(self.temp_dir.name)
        self.assertTrue(saved.get("listening"))

    def test_remembers_window_open_vs_tray_hidden_state(self):
        gui.save_gui_state(self.temp_dir.name, {"window_open": False})
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}):
            self.tk_root.deiconify()
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            try:
                self.assertEqual(self.tk_root.state(), "withdrawn")
                app._show_window()
                self.assertNotEqual(self.tk_root.state(), "withdrawn")
                self.assertTrue(gui.load_gui_state(self.temp_dir.name).get("window_open"))
                app._hide_to_tray()
                self.assertEqual(self.tk_root.state(), "withdrawn")
                self.assertFalse(gui.load_gui_state(self.temp_dir.name).get("window_open"))
                app._toggle_window()
                self.assertNotEqual(self.tk_root.state(), "withdrawn")
                app._toggle_window()
                self.assertEqual(self.tk_root.state(), "withdrawn")
            finally:
                app._exit_app()

    def test_manual_message_while_busy_is_queued_and_sent_on_done(self):
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}):
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            try:
                app._set_busy(True, "Working...")
                app.input_box.insert("1.0", "queued follow up")
                app._on_send_clicked()
                self.assertEqual(app._queued_messages, ["queued follow up"])
                self.assertIn("Queued (1): queued follow up", app.transcript.get("1.0", "end-1c"))

                app._event_queue.put(("done", None, None))
                with patch.object(app, "_submit_text") as submit_mock, \
                     patch.object(app, "_refresh_task_panel"), \
                     patch.object(app, "_refresh_context_meter"):
                    app._poll_events()
                submit_mock.assert_called_once_with("queued follow up")
                self.assertEqual(app._queued_messages, [])
            finally:
                app._exit_app()

    def test_send_button_not_trimmed_on_narrow_window(self):
        with patch.object(gui, "initialize_provider", return_value=("openai", "gpt-4o")), \
             patch.object(gui, "load_provider_config", return_value={"provider": "openai", "model": "gpt-4o"}):
            app = gui.ShaiGuiApp(self.tk_root, enable_tray=False)
            try:
                self.tk_root.deiconify()
                self.tk_root.geometry("420x600+0+0")
                self.tk_root.update()
                frame_w = app.input_frame.winfo_width()
                btn_x = app.send_btn.winfo_x()
                btn_w = app.send_btn.winfo_width()
                self.assertGreaterEqual(btn_w, app.send_btn.winfo_reqwidth())
                self.assertLessEqual(btn_x + btn_w, frame_w)
                self.assertEqual(app.send_btn.cget("cursor"), "hand2")
                self.assertEqual(app.mic_cont_btn.cget("cursor"), "hand2")
                self.assertGreater(app.context_progress.winfo_width(), 120)
                self.assertIs(self.tk_root.focus_get(), app.input_box)

                # Long diff box stays within narrow transcript width without clipping Copy button
                long_diff = (
                    "diff --git a/very/long/path/to/some/module_file.py b/very/long/path/to/some/module_file.py\n"
                    "--- a/very/long/path/to/some/module_file.py\n"
                    "+++ b/very/long/path/to/some/module_file.py\n"
                    "@@ -1,4 +1,4 @@\n"
                    "+some_very_long_line_of_code_that_exceeds_narrow_window_width_easily = 1234567890\n"
                )
                app._append_output(long_diff, "system")
                self.tk_root.update()
                diff_block = app._code_blocks[max(app._code_blocks)]
                self.assertLessEqual(
                    diff_block["box_frame"].winfo_width(),
                    app.transcript.winfo_width(),
                )
            finally:
                app._on_close()


if __name__ == "__main__":
    unittest.main()
