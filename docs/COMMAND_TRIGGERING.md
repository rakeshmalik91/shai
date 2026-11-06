# Natural-Language Command Triggering

## 1. Purpose

Allow users to invoke Shai's existing slash commands using ordinary language,
without requiring them to memorize command syntax.

Examples:

- "Show my git status" → `/status`
- "Show the diff" → `/diff`
- "Commit the staged changes" → `/commit`
- "Switch to read-only mode" → `/read-only`

This document defines **intent recognition and command routing**. It does not
grant permission to run arbitrary programs. Any slash command that launches a
process must use the execution controls defined in
[`CONTROLLED_COMMAND_EXECUTION.md`](CONTROLLED_COMMAND_EXECUTION.md).

Core rule:

> Natural language may select a known command. It must not invent commands,
> silently add side effects, or bypass the command's normal authorization.

## 2. Goals and non-goals

### Goals

- Preserve every existing slash command and its documented behavior.
- Recognize clear natural-language equivalents.
- Route recognized requests through one canonical command dispatcher.
- Ask a concise clarification when intent or required parameters are ambiguous.
- Preserve the user's ability to type slash commands directly.
- Make command selection, execution, and approval observable and testable.

### Non-goals

- Guessing that the user wants a command merely because a keyword appears.
- Automatically executing a sequence of commands from a vague request.
- Learning private vocabulary or behavioral patterns without an explicit
  product requirement and user-visible controls.
- Replacing the command registry with prompt-only instructions.
- Bypassing mode restrictions, command permissions, or execution approval.

## 3. Command registry as the source of truth

Maintain one structured registry in code. Use it to drive slash-command
parsing, natural-language descriptions, validation, help text, and tests.
Do not maintain separate, drifting lists in the system prompt and dispatcher.

A registry entry should include:

| Field | Purpose |
|---|---|
| `id` | Stable internal identifier |
| `slash_command` | Canonical command syntax |
| `description` | User-facing behavior |
| `examples` | Natural-language examples |
| `parameters` | Typed arguments and validation |
| `aliases` | Explicit alternate phrases or names |
| `mode_requirements` | Modes in which the command is valid |
| `risk` | Read-only, mutating, destructive, or external |
| `confirmation` | Whether confirmation is required |
| `enabled` | Runtime availability |
| `handler` | Internal handler reference; never model-supplied |

Example (illustrative; adapt to the actual command implementations):

```python
COMMAND_REGISTRY = {
    "status": {
        "id": "status",
        "slash_command": "/status",
        "description": "Show the current repository status.",
        "examples": [
            "show git status",
            "what has changed",
            "check the working tree"
        ],
        "parameters": {},
        "aliases": [],
        "mode_requirements": ["ask", "code", "read-only"],
        "risk": "read_only",
        "confirmation": "none",
        "enabled": True,
        "handler": status_handler
    },
    "commit": {
        "id": "commit",
        "slash_command": "/commit",
        "description": "Commit the changes that are already staged.",
        "examples": [
            "commit the staged changes",
            "create a commit"
        ],
        "parameters": {
            "message": {
                "type": "string",
                "required": False
            },
            "message_only": {
                "type": "boolean",
                "required": False
            }
        },
        "aliases": [],
        "mode_requirements": ["code"],
        "risk": "repository_mutation",
        "confirmation": "required",
        "enabled": True,
        "handler": commit_handler
    }
}
```

The handler names above are placeholders, not a requirement to expose Python
callables to the model. Keep handlers internal.

### Existing command inventory

The current documented command set is:

`/commit`, `/push`, `/status`, `/files`, `/diff`, `/reset`, `/ask`,
`/read-only`, `/code`, `/todo`, `/commit-push`, `/project`, `/exit`.

Before implementation, verify each command's actual parser, parameters,
availability, mode behavior, and side effects. The registry must reflect
implemented behavior, not assumptions based on command names.

## 4. Intent recognition

Use the LLM to interpret user intent, but constrain its output to a structured
candidate. Do not let it execute handlers directly.

Suggested recognition result:

```json
{
  "intent": "invoke_command",
  "command_id": "status",
  "arguments": {},
  "confidence": 0.96,
  "reason": "The user explicitly asked to see repository status."
}
```

Allowed outcomes:

- `invoke_command`: one known command is clearly requested.
- `clarify`: more than one command or materially different interpretation is
  plausible, or required parameters are missing.
- `chat`: the message is a question, discussion, explanation request, or other
  normal conversation.
- `unsupported`: the user requests an action for which no enabled command
  exists.

The model may return only a registry `command_id` and schema-valid arguments.
The application must validate both independently. Ignore unknown fields and
reject invalid values; never dynamically import or evaluate model output.

### Confidence is not authorization

A confidence score can help choose between command routing and clarification,
but it must not override policy. Even a high-confidence result must pass
registry validation, mode checks, risk classification, and any required
confirmation.

Avoid relying on keyword matching as the primary detector. Words such as
"commit", "push", "reset", and "exit" can appear in questions, explanations,
code, quoted text, and hypothetical examples.

Examples that should remain chat:

- "What does `git commit` do?"
- "Should I push this branch?"
- "Explain why the last commit failed."
- "Can you show me an example of a reset command?"
- "I don't want to commit yet."

Negation, quoted text, hypothetical language, and requests for explanation
must be considered during intent recognition.

## 5. Routing and execution flow

```text
User message
    |
    v
Explicit slash command?
    | yes
    v
Parse using canonical registry
    |
    +-----------------------------+
    |                             |
    no                            |
    v                             |
Natural-language intent           |
recognition (constrained)          |
    |                             |
    +--> chat / clarify /          |
    |    unsupported               |
    |                             |
    +--> command candidate <-------+
                  |
                  v
       Validate registry ID/args
                  |
                  v
       Check mode and availability
                  |
                  v
       Check command-specific policy
                  |
                  v
        Confirmation if required
                  |
                  v
          Invoke canonical handler
                  |
                  v
       Return structured result to LLM
                  |
                  v
          Explain outcome to user
```

The explicit slash-command path and natural-language path must converge before
validation and handler invocation. Neither path should duplicate command
execution logic.

### Dispatcher contract

Prefer an internal API such as:

```python
dispatch_command(
    command_id="status",
    arguments={},
    source="natural_language",
    user_request_id=request_id
)
```

The dispatcher must:

1. Resolve the command ID from the registry.
2. Verify that the command is enabled.
3. Validate and normalize arguments.
4. Check the current mode and command preconditions.
5. Apply the command's risk and confirmation policy.
6. Invoke the registered handler only after authorization.
7. Return a structured result.

If a handler runs an external process, it must call the controlled execution
service. It must not invoke `subprocess` directly.

## 6. Execution and confirmation policy

Natural-language recognition does not make a command safe.

| Command/action type | Example | Expected behavior |
|---|---|---|
| Read-only | `/status`, `/diff`, `/files` | May execute when clearly requested and enabled |
| Mode change | `/ask`, `/code`, `/read-only` | Execute only when the user clearly requests the mode change |
| Local mutation | `/reset` (depending on its implementation) | Explain effect and confirm when state may be lost |
| Repository mutation | `/commit` | Confirm according to the command policy |
| Remote mutation | `/push`, `/commit-push` | Explicit confirmation before network side effects |
| Session control | `/exit` | Execute only when the user clearly intends to end the session |

This table is provisional. Derive each command's actual risk from its
implementation. For example, `/reset` could be harmless or destructive
depending on its semantics.

Confirmation requirements:

- State the specific action and relevant target.
- For destructive or remote operations, summarize the expected side effect.
- Bind approval to the exact command and arguments.
- If arguments or targets change, request approval again.
- Do not treat a previous approval as consent for future operations.
- Do not chain a confirmed command into additional unconfirmed actions.

For read-only commands, automatic execution can be enabled by policy after
the feature is tested. For mutating, destructive, or external actions, require
the configured confirmation flow.

## 7. Parameters and ambiguity

Infer only parameters clearly supplied by the user or safely defaulted by the
command's documented contract.

Examples:

- "Commit with message 'fix parser'" → `/commit` with the message parameter,
  if custom messages are supported.
- "Push to origin main" → `/push` with remote and branch only if those
  parameters are implemented.
- "Commit and push" → `/commit-push` only if that command exists and its
  exact semantics are known.
- "Reset it" → clarify what should be reset and what data may be lost.
- "Stage everything and commit" → do not silently run `git add -A`; confirm
  the staging scope and use an explicitly supported workflow.

Do not invent unsupported flags such as `--message-only`. Parameters must be
defined by the registry and validated by the command handler.

## 8. Chaining and multi-step requests

Treat a multi-step request as a workflow, not as permission to execute
arbitrary commands in sequence.

- Prefer a dedicated, reviewed command such as `/commit-push` when it matches
  the request exactly.
- If no dedicated command exists, present the planned steps and obtain the
  required approval before each side-effecting step.
- Stop if an earlier step fails or produces an unexpected result.
- Do not continue from stale repository state.
- Never turn "commit and push" into a broader operation such as staging all
  files unless the user explicitly requested that behavior.
- Do not retry a partially completed mutation without checking the resulting
  state first.

## 9. Context-aware preconditions

Context can improve routing and prevent invalid actions, but it must not
silently expand user intent.

Examples:

- `/commit` may check whether anything is staged. If nothing is staged, report
  that fact and explain the next supported step; do not stage files
  automatically.
- `/push` may check whether a push is possible. If there are no commits to
  push, report that rather than invoking a no-op or guessing a branch.
- A mode change should use the current mode and the requested target mode.
- A command may inspect repository state only when that inspection is within
  the command's documented behavior and allowed policy.

Do not inject `git status` output into every prompt. Fetch state on demand or
maintain a clearly timestamped snapshot. Never treat stale state as a
precondition for a mutation.

## 10. System prompt and tool design

Keep the system prompt concise. Supply a compact registry summary or the
currently relevant enabled commands, generated from the canonical registry.
Do not hand-maintain a second command list in prompt text.

A suitable model-facing tool could be:

```python
{
    "name": "request_command",
    "description": (
        "Request a known Shai command when the user clearly intends to invoke "
        "it. This does not bypass validation or confirmation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command_id": {
                "type": "string",
                "enum": ["status", "diff", "files", "commit", "push"]
            },
            "arguments": {
                "type": "object"
            }
        },
        "required": ["command_id", "arguments"],
        "additionalProperties": False
    }
}
```

The enum should be generated from the enabled registry for the current
context. If the tool schema cannot express each command's argument schema,
validate arguments in application code before dispatch.

Prefer the name `request_command` over `execute_slash_command`: the model is
requesting a command, while the application remains responsible for deciding
whether it may run.

## 11. Configuration

Proposed configuration:

```json
{
  "natural_language_commands": {
    "enabled": true,
    "recognition": true,
    "auto_execute_read_only": true,
    "confirm_mode_changes": false,
    "confirm_mutations": true,
    "confirm_destructive": true,
    "confirm_network": true,
    "allow_chaining": false,
    "clarify_below_confidence": 0.85
  }
}
```

Configuration is illustrative. Validate ranges and supported values. The
confidence threshold is a routing aid, not a security control. The command
execution feature must also be enabled for any handler that starts a process.

## 12. Suggested implementation plan

### Phase 1 — Registry and dispatcher

- Inventory all existing slash commands from their actual implementations.
- Define the canonical registry, parameter schemas, mode requirements, and
  risk metadata.
- Refactor slash-command handling to use the registry and one dispatcher.
- Add tests proving existing slash-command behavior is unchanged.

### Phase 2 — Natural-language recognition

- Add constrained intent recognition that returns a registry ID and arguments.
- Add `chat`, `clarify`, and `unsupported` outcomes.
- Validate all model output in application code.
- Route recognized commands through the canonical dispatcher.
- Keep natural-language execution disabled by default during initial testing.

### Phase 3 — Policy and context

- Integrate mode checks, command preconditions, confirmation, and controlled
  command execution.
- Add on-demand repository-state checks where useful.
- Ensure context cannot silently expand the requested action.

### Phase 4 — Rollout

1. **Internal:** recognition proposes a command; no automatic mutation.
2. **Read-only:** optionally auto-run clear read-only commands after tests pass.
3. **Mutations:** enable only with exact previews and explicit confirmation.
4. **External actions:** enable only when network policy and confirmation
   controls are implemented and tested.

Do not make all commands auto-executable as a general-availability milestone.

## 13. Test plan

### Recognition

- Clear positive: "show git status" routes to `/status`.
- Clear positive: "show the diff" routes to `/diff`.
- Negative: "what does git status show?" remains chat if asking for an
  explanation rather than current status.
- Negative: "don't commit yet" does not invoke `/commit`.
- Negative: quoted examples and code snippets do not trigger commands.
- Ambiguous: "reset it" asks a clarification.
- Unsupported: an unknown action does not invent a command.

### Registry and dispatcher

- Unknown command IDs are rejected.
- Disabled commands are rejected.
- Invalid or extra arguments are rejected.
- Mode restrictions are enforced.
- Slash and natural-language invocation produce the same handler behavior.
- A handler failure is returned as a failure, not a successful command result.

### Authorization and side effects

- Read-only approval cannot authorize mutation.
- Mutations require the configured confirmation.
- Approval is invalidated if arguments or target change.
- `/commit-push` cannot continue to push after a failed commit.
- Natural-language routing cannot bypass the controlled execution service.
- No command executes when command execution is disabled.

### Regression and reliability

- Existing slash syntax remains supported.
- Recognition failure falls back to ordinary chat or clarification.
- Tool errors do not cause repeated execution loops.
- Duplicate tool calls do not execute the same mutation twice.
- Latency and false-positive rates are measured separately.

## 14. Success metrics

Measure with a labeled evaluation set and production-safe telemetry:

- **Intent precision:** fraction of routed requests that genuinely requested
  that command.
- **Intent recall:** fraction of clear command requests correctly recognized.
- **False execution rate:** non-command messages that invoke a command. Target
  should be zero for the evaluation suite.
- **Clarification rate:** how often the system asks instead of guessing.
- **Execution success rate:** successful handler results divided by authorized
  attempts.
- **Latency:** recognition and total dispatch overhead.
- **Regression rate:** failures in existing explicit slash-command behavior.

Do not optimize adoption or recall at the expense of false executions.

## 15. Open implementation questions

Resolve these against the current codebase before implementation:

1. What are the exact semantics and parameters of every existing slash
   command?
2. Does `/reset` discard conversation state, repository changes, or something
   else?
3. Does `/commit` commit only staged changes, and how are commit messages
   selected?
4. What does `/commit-push` do if commit succeeds but push fails?
5. Which commands are valid in each mode?
6. Which commands invoke subprocesses or access the network?
7. How are pending confirmations represented and expired?
8. How are duplicate model tool calls and interrupted agent loops handled?

These are implementation facts to inspect, not assumptions to fill in from
the examples in this document.
