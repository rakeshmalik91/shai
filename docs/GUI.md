# ShAI Desktop GUI

ShAI includes a standard-library Tkinter desktop GUI (`modules/ui/gui.py` and `modules/ui/tray.py`) with no third-party runtime dependencies.

## Launching

Run with a console attached:

```sh
python3 gui.py
```

Or launch on Windows without a console window (by double-clicking `gui.pyw` or running `pythonw`):

```sh
pythonw gui.pyw
```

You can also pass an initial project folder on the command line:

```sh
pythonw gui.pyw /path/to/project
```

If no project path is passed, Shai restores the last opened project from `~/.shai/app_state.json`.

## Modes

Switch modes anytime from the **Mode** dropdown in the top toolbar or via slash commands:

| Mode | Command | Behavior |
|------|---------|----------|
| **Ask (Chat Only)** | `/ask` | Regular conversation without project tools. |
| **Read-Only (Inspect)** | `/read-only`, `/ro` | Chat with read-only project inspection tools. |
| **Agent (Code & Tools)** | `/code` | Full coding agent with file editing, Git, and command tools. |

## Interface Overview

- **Top Toolbar**:
  - **Mode dropdown**: Switch between Ask, Read-Only, and Agent modes.
  - **Project button (`📁 <name>`)**: Shows the active project folder name (full path in tooltip and window title `ShAI - <project path>`) and opens a folder picker.
  - **Settings button (`⚙`)**: Opens the centered API token settings dialog (`/settings`).
- **Quick Action Bar**:
  - `Status` (`/status`), `Files` (`/files`), `Diff` (`/diff`), `Commit` (`/commit`), `Push` (`/push`), `?` (`/help`), and `Reset` (`/reset`, with confirmation prompt).
  - All command buttons and dropdowns are automatically disabled while Shai is processing a request.
- **Chatbox**:
  - Renders user and ShAI messages in separate rounded WhatsApp-style bubbles with timestamps and spacing (user messages right-aligned in green; ShAI responses left-aligned in slate; restored history messages use muted colors separated from the current session by a `Current Session` divider), and renders Markdown tables as bordered grid tables.
  - Renders POSIX/ANSI SGR colors (`30–37`, `90–97`, bold, dim, underline) and routes `\r` spinner/progress updates to the bottom status bar.
  - **Collapsible tool-call batches**: Consecutive tool calls stay expanded while running and auto-collapse (`▶ N tool calls`) once complete. Tool parameters are formatted cleanly as `key=value` pairs.
  - **Inline boxed code, file, and diff containers**: Fenced code blocks (` ```lang `), unified diffs, and `/files` / file outputs render in horizontally scrollable bordered inline containers with **Copy**, **Popout (`↗`)**, and 4-line collapsible preview (click the header to expand or collapse). Diffs use inline syntax highlighting (`diff_file`, `diff_hunk`, `diff_add`, `diff_del`).
- **Above the Prompt Input**:
  - **Current Task List**: Appears automatically when `engine.TASK_PLAN` has active steps.
  - **Context Window Meter**: Shows estimated prompt tokens, model context limit, and a progress bar.
  - **Provider & Model Dropdowns**: Preloads available models for the active provider at startup; switching providers fetches models in the background.
- **Prompt Input, Mic & Send/Stop**:
  - `Enter` sends the message (if ShAI is busy, it queues the message and sends it automatically once the current turn finishes); `Shift+Enter` inserts a newline.
  - `Up` / `Down` arrows navigate persisted prompt history (`ConversationHistory.input_history()`).
  - **Mic button (`🎤`)**: Click `🎤`, press `Ctrl+M`, or say `"Shai"` / `"Hey Shai"` (local wake word) for **continuous listening** (automatically sends after a brief pause, or once ShAI finishes if busy, with subtle audio chimes on start, stop, and auto-send; saying `"stop"` or `"cancel"` while ShAI is busy cancels the active request, and saying `"stop listening"` or `"mute"` stops listening). While listening, the taskbar and system tray icons display a mic badge, and if ShAI is in the taskbar/background, heard speech is shown as a trimmed tray notification. Uses `sounddevice` + OpenAI (`OPENAI_API_KEY`) or Gemini (`GEMINI_API_KEY`) models configured under `speech_to_text` in `assets/model_routing.json` (with Windows `System.Speech` as fallback). Click or press `Ctrl+M` again to stop.
  - While a turn is running, **Send** becomes a red **Stop** button that cancels the active request (`IterationInterrupted`).

## Settings & API Tokens

Clicking `⚙` (or running `/settings`, or choosing **Settings** from the tray menu) opens the settings dialog with two tabs on the left:

- **GUI**: Toggle **Always on top**, **Start minimized**, **Minimize to system tray**, **Close to system tray**, **Minimize to system tray when out of focus**, and **Play sound on listen start/stop/send** (persisted in `~/.shai/app_state.json`).
- **Tokens**:
  - Configures `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `KILO_API_KEY`.
  - Tokens are applied immediately to `os.environ`, saved to `~/.shai/tokens.json`, and (on Windows when checked) persisted to `HKEY_CURRENT_USER\Environment`.
  - On startup, the GUI loads unset tokens from `~/.shai/tokens.json` and Windows `HKEY_CURRENT_USER\Environment`.

## System Tray & Persistence

- **Windows System Tray** (`modules/ui/tray.py`):
  - Uses `assets/icons/shai.ico` / `assets/icons/shai.png` (and `shai_mic.*` while listening) for the taskbar, window, and system tray icon.
  - Closing (`X`) or minimizing the window hides it to the system tray.
  - Left-clicking the tray icon toggles (shows/hides) the window; right-clicking opens **Open ShAI**, **Start listening** / **Stop listening**, **Settings**, **Restart app**, and **Exit**.
- **State Persistence**:
  - Window size, position, maximized state, active mode, listening state, window open/tray state, GUI settings, Settings dialog geometry, and `last_project` are saved in `~/.shai/app_state.json`.
  - Default geometry is 30% of screen width × 80% of screen height, anchored at the bottom-right corner.
  - Conversation messages and prompt input history are restored per project from `<project>/.shai/history.json`.
