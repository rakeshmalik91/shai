# LLM Clarification Questions — Technical Design & Protocol

**Status: Forward-Looking Roadmap.**
Currently, when a model encounters an underspecified prompt, ambiguous requirement, or conflicting
user instructions, it either makes unverified assumptions or halts with narration. This document
defines the protocol for an interactive `ask_user` mechanism that allows the agent to pause
execution, query the user for targeted clarification, and resume the task without losing state.

---

## 1. Interaction Principles

1. **Ask Only When Truly Blocked:** Never ask questions whose answers are already discoverable in the codebase, conversation history, or project configuration.
2. **Present Concrete, Ergonomic Choices:** Where possible, supply 2 to 4 clear options plus free-form text input rather than presenting a blank prompt.
3. **Bounded Question Budget:** To prevent clarification loops ("interrogation fatigue"), cap clarification requests at **3 per session**.
4. **Preserve Execution State:** Store the user's answers as explicit constraints in `ConversationHistory` and the session state.

---

## 2. Tool Definition: `ask_user`

The tool is registered in `assets/tools_registry.json` for Agent mode:

```json
{
  "type": "function",
  "function": {
    "name": "ask_user",
    "description": "Pause execution to ask the user a focused clarification question when a critical ambiguity blocks progress.",
    "parameters": {
      "type": "object",
      "properties": {
        "question": {
          "type": "string",
          "description": "The concise, clear question to present to the user."
        },
        "choices": {
          "type": "array",
          "items": {"type": "string"},
          "description": "Optional list of distinct, concrete options for the user to pick from."
        },
        "default_choice": {
          "type": "string",
          "description": "Low-risk default option to assume if the user is in non-interactive mode or presses Enter without selecting."
        },
        "urgency": {
          "type": "string",
          "enum": ["blocking", "advisory"],
          "default": "blocking",
          "description": "Whether execution must halt for an answer (blocking) or can proceed with the default assumption if unanswered."
        },
        "category": {
          "type": "string",
          "enum": ["scope", "target_file", "architectural_choice", "ambiguous_requirement"],
          "description": "The category of ambiguity being resolved."
        }
      },
      "required": ["question"]
    }
  }
}
```

---

## 3. Session Lifecycle & State Machine

When the model emits an `ask_user` tool call:

1. **State Transition:**
   - The session state transitions from `PROCESSING` to `SUSPENDED_WAITING_USER_INPUT`.
   - Tool batch execution halts immediately (no subsequent tool calls in the same batch are executed).
2. **Terminal Presentation:**
   - The live status dashboard pauses in-place updates and commits a newline.
   - The question is displayed with high-visibility styling (ANSI Bold / Yellow).
   - If `choices` are provided, they are rendered as a numbered menu:
     ```text
     [?] Clarification needed: Which database migration strategy should be used?
       [1] Create a new revision file using Alembic (recommended)
       [2] Update the existing migration in-place
       [3] Skip database migration for this change
       [or type a custom answer]
     Choice [1]: 
     ```
3. **Input Handling:**
   - Supports numeric index selection (`1`, `2`, `3`) or arbitrary typed responses.
   - If the user presses `Ctrl+D` or `Ctrl+C`, the request transitions to `CANCELLED`.
4. **State Resumption:**
   - The user's answer is formatted as a standard tool result:
     ```json
     {
       "selected_choice": "Create a new revision file using Alembic",
       "user_response": "1",
       "is_default": false
     }
     ```
   - Execution resumes with a fresh model turn containing this verified constraint.

---

## 4. Headless & Non-Interactive Policy

When ShAI runs in a CI/CD pipeline, script, or non-TTY environment (`sys.stdin.isatty() == False`):

- **If `default_choice` is specified:** ShAI logs the assumption, selects `default_choice`, and continues execution.
- **If `default_choice` is not specified and `urgency == "blocking"`:** Execution halts safely with exit code 2 (`EXECUTION_SUSPENDED_NON_INTERACTIVE`) and outputs the unanswered question to `stderr`.

---

## 5. Loop Guards & Anti-Interrogation Policy

To prevent degenerate models from entering continuous questioning loops:

1. **Per-Turn Count Limit:** At most one `ask_user` call per turn. Multiple parallel `ask_user` calls in a single batch are rejected.
2. **Max Questions Cap:** A hard ceiling of 3 `ask_user` invocations per request. If a 4th question is attempted, the tool returns:
   `"Clarification budget exceeded. Do not ask further questions; proceed using the safest, lowest-risk default assumption or summarize remaining open items in your final response."`
3. **Duplicate Question Rejection:** A question matching previously answered questions within the same session is rejected as a repeated tool call.

---

## 6. Verification & Acceptance Criteria

1. **Interactive Choice Selection:** Unit tests verify selection by number, exact text match, and custom response string.
2. **Default Fallback on Empty Input:** Pressing Enter when `default_choice` is provided resolves to that default choice.
3. **Non-Interactive Behavior:** Verified in headless mode: automatically adopts `default_choice` when available, or exits with code 2 if blocking.
4. **Conversation History Preservation:** Resumed conversations accurately reflect user answers and do not re-ask resolved questions.
5. **Budget Enforcement:** Verified that exceeding 3 questions terminates the loop with an explanatory constraint.
