# Roadmap: LLM Clarification Questions

**Status: Not implemented.** No `ask_user` interaction or task-suspension/resumption flow is currently registered.

## Goal

Allow Shai to pause before acting when the request, requirements, target files,
or expected behaviour are ambiguous. Shai should ask concise questions, wait
for the user's answers, and then continue the same task without losing context.

## Proposed interaction

1. Detect a material ambiguity before planning or executing a consequential action.
2. Ask one focused question at a time by default; combine independent questions
   only when answering them together is clearly easier.
3. Present concrete choices when they reduce effort, while allowing free-form
   answers and "not sure".
4. Store answers as explicit task constraints in the active conversation/task
   state, not as hidden assumptions.
5. Re-evaluate the plan after each answer and ask again only if a blocking
   ambiguity remains.
6. If the user declines or cannot answer, proceed only with a clearly stated,
   low-risk assumption—or explain why execution must wait.

## Proposed capability

- Add an `ask_user` interaction/tool with a short question and optional choices.
- The tool must suspend the agent loop and return the user's actual response.
- Support single-select, multi-select and free-text responses; never require
  a form for a question that can be asked conversationally.
- Keep question/answer pairs in the task history so resumed work can use them.
- Distinguish blocking questions from optional preference questions.
- Permit a user setting to disable proactive clarification for low-risk tasks.

## Safety and UX requirements

- Do not ask questions whose answers are already available in the conversation
  or project configuration.
- Ask before irreversible or broad-scope actions when intent is unclear.
- Do not turn every missing preference into a blocker.
- Never fabricate a user's answer or silently choose a destructive option.
- Display a clear "Waiting for your answer" state without flooding the terminal.

## Acceptance criteria

- An ambiguous edit request triggers a targeted clarification before edits.
- A sufficiently specified request proceeds without unnecessary questions.
- The user's answer survives a tool-call cycle and task continuation.
- The agent can explain which assumption it used when the user explicitly
  authorizes proceeding with a default.
