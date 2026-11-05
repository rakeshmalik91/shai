# Modular Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md#modular-package-layout) for the detailed
module map, entry-point compatibility, command organization, tool boundaries,
and configuration-preservation guarantees.

The refactor keeps `agent.py` and `chat.py` as stable launch/import entry points
while moving implementation into `modules/engine/` and `modules/ui/`.
