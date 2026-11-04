# Agent continuation and tool-module fix

## Log diagnosis (4 November 2026)

The supplied run used `kilo-free` with `nvidia/nemotron-3-ultra-550b-a55b:free`. The configuration set `max_tool_call_iterations` to **500**. The log shows the agent reaching at least 25 iterations, repeatedly reading/searching `agent.py`, compacting prior tool activity, and revisiting already inspected sections. Tool calls often succeeded, so the existing no-progress detector correctly did not classify those calls as failures; however, successful inspection did not guarantee that the model moved on to edits and verification. Compaction then reduced the model's visibility into earlier work and contributed to rediscovery.

The observed failure mode is therefore broader than a missing tool dispatch: the loop could continue for an excessive number of iterations while the model narrated intended work, and the code returned immediately on any text-only response even if the in-memory task plan still contained pending steps. The log's `finish_reason=tool_calls` entries confirm that tool dispatch itself was functioning during many iterations.

## Changes

- A text-only model response now triggers a continuation prompt when the task plan still contains pending/in-progress items. This recovery is bounded to two continuations; completed or blocked plans do not trigger it.
- Added explicit logs for model finish reason, tool-call count, dispatch start/completion, pending plan state and no-tool-call termination.
- Capped `max_tool_call_iterations` at 80; the supplied configuration's unsafe value of 500 is reduced to 80. The cap is logged when applied.
- Moved shared read-only project tools, their schemas and dispatch registry from `chat.py` into `modules/tools.py`. Agent-only mutating and verification tools remain in `agent.py` so they retain the existing approval and execution safeguards.
- Updated architecture/tool documentation and added regression tests.

## Verification

Run `python -m unittest discover -s test -q` and `python -m compileall -q .`.

## Inline tool activity display

Tool activity is intentionally not a pinned dashboard. Conversation output
continues to scroll normally. During each provider tool-call batch, Shai
rewrites one transient terminal line (`\\r` + erase-line) with the current
batch position, tool name, and running/completed/error state. The line is
committed with a newline when the batch finishes. Context estimates and task
plan state continue to update internally without reserving terminal rows or
printing a new line for every tool call. Non-interactive output stays clean.
