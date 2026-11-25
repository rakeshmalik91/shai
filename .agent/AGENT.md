# Architecture Guidelines

- **Keep Engine, CLI, and GUI completely separated**:
  - **Engine (`modules/engine/`, `modules/tools/`, `modules/commands/`, etc.)**: Core agent runtime, tool execution, provider/model routing, and state management. Never import `tkinter`, GUI modules, or CLI-specific terminal formatting/input code in the engine.
  - **CLI (`agent.py`, `chat.py`, `modules/ui/agent_cli.py`, `modules/ui/chat_runtime.py`)**: Terminal I/O, CLI prompts, and CLI-specific display. Never import GUI/Tkinter modules here.
  - **GUI (`gui.py`, `gui.pyw`, `modules/ui/gui.py`, `modules/ui/tray.py`, `modules/ui/speech.py`)**: Desktop Tkinter UI, system tray, and speech-to-text UI integration. Never embed engine/tool business logic in the GUI.
  - **Communication**: CLI and GUI must interact with the engine strictly through `InteractionHandler` (`modules/engine/interaction.py`) and public engine APIs/callbacks rather than mixing presentation and core logic.
