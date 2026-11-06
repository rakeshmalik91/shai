# Model Routing — Remaining Design and Implementation Plan

## Goal

Make model selection predictable, explainable, project-configurable, and
appropriate to the work being performed. A role identifies the task; project
configuration identifies suitable candidate models; runtime checks
availability and selects among those candidates.

The initial router and the `chat`, `orchestrator`, and `commit_message`
integrations are implemented. See [the completed routing documentation](../MODEL_ROUTING.md).
This document contains the remaining design decisions, schema extensions,
and implementation blueprints.

---

## 1. Model-Selection Policy & Schema v2

### 1.1 Assign the task role explicitly

The workflow/call site must choose the role because it knows what operation it
is about to perform. Do not infer a role by asking a model to classify the
user's prompt, and do not relabel a general agent request as a specialist task
just because the user mentions code, tests, or documentation.

Initial role contract:

| Role | Use when the runtime is specifically doing... | Current Status |
|---|---|---|
| `chat` | A direct user-facing conversational response | Integrated in `chat.py` |
| `orchestrator` | Agent reasoning, tool selection, and coordination | Integrated in `agent.py` |
| `commit_message` | Generating a Git commit message | Integrated in `/commit` flows |
| `planner` | Producing or revising a multi-step execution plan | Planned call site |
| `code` | Generating or editing implementation code in a dedicated step | Planned call site |
| `debug` | Diagnosing a concrete error, failure, or unexpected behavior | Planned call site |
| `test` | Designing tests or analyzing test results in a dedicated step | Planned call site |
| `review` | Reviewing a diff or implementation for defects and risks | Planned call site |
| `documentation` | Writing or updating project documentation | Planned call site |
| `research` | Synthesizing gathered research and evidence | Planned call site |

A task that moves between stages may make multiple model calls, each with its
own role. Keep `orchestrator` as the owner of the agent/tool loop; specialist
roles must correspond to real, separately dispatched requests.

### 1.2 Task profiles and requirements

Before candidate selection, the call site constructs an immutable `TaskProfile`:

```python
class TaskProfile:
    role: str
    required_context_window: int = 4096
    requires_tools: bool = False
    requires_structured_output: bool = False
    requires_vision: bool = False
    allowed_data_locations: list[str] = ("local", "hosted")  # "local" only for sensitive projects
    max_cost_tier: int = 3  # 1=free, 2=low-cost, 3=standard, 4=premium
```

### 1.3 Version 2 configuration schema

Version 1 configurations (`version: 1`) using only `provider`, `model`, and `priority`
remain fully backward-compatible. Version 2 (`version: 2`) introduces optional capability
metadata:

```json
{
  "version": 2,
  "settings": {
    "discovery_cache_ttl_seconds": 60,
    "failure_cooldown_seconds": 300,
    "max_failover_attempts": 3,
    "auto_reload_on_change": true
  },
  "models": {
    "orchestrator": [
      {
        "provider": "ollama",
        "model": "qwen2.5:14b",
        "priority": 1,
        "context_window": 32768,
        "capabilities": ["tools", "structured_output"],
        "data_location": "local"
      },
      {
        "provider": "openrouter",
        "model": "anthropic/claude-3.5-sonnet",
        "priority": 2,
        "context_window": 200000,
        "capabilities": ["tools", "vision", "structured_output"],
        "data_location": "hosted"
      }
    ],
    "planner": [
      {
        "provider": "ollama",
        "model": "llama3.3:70b",
        "priority": 1,
        "context_window": 65536,
        "capabilities": ["structured_output"]
      }
    ],
    "code": [],
    "debug": [],
    "test": [],
    "chat": [],
    "commit_message": [
      {
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "priority": 1,
        "context_window": 16384
      }
    ],
    "review": [],
    "documentation": [],
    "research": []
  }
}
```

### 1.4 Candidate selection algorithm

Selection remains deterministic and auditable:

1. **Resolve Role:** Identify the explicit task role from the call site.
2. **Load Config:** Load active project `.shai/model_routing.json` merged over `assets/model_routing.json`.
3. **Filter by Hard Requirements:**
   - If `TaskProfile.requires_tools` is `True`, eliminate candidates that lack tool declaration support.
   - If `candidate.context_window` is defined, reject candidates where `context_window < required_context_window`.
   - If `TaskProfile.allowed_data_locations` specifies `["local"]`, reject hosted/cloud providers.
4. **Check Provider Registration:** Candidate provider must be registered in `assets/provider_config.json` or built-in defaults.
5. **Check Model Availability:**
   - Query provider model catalogue (or read cached catalogue if within TTL).
   - Verify model identifier exists in discovered list.
   - Exclude providers currently in failure cooldown.
6. **Order by Priority:**
   - Sort eligible candidates by ascending numeric `priority`.
   - Preserve stable config declaration order for equal priorities.
7. **Dispatch or Fallback:**
   - Return highest-priority eligible candidate.
   - If no candidates match, return the project's configured default provider and model.

---

## 2. Implement Remaining Specialist Call Sites

Wire roles only where genuine, distinct model requests exist:

1. **`planner` — Task Decomposition:**
   - Invoked when the agent decomposes a multi-step user goal or revises `TASK_PLAN`.
   - Request expects structured JSON output matching the task plan schema (`[{"title": ..., "status": ...}]`).
   - Dispatched via `api_request_for_role(..., role="planner")`.

2. **`code` — Targeted Code Generation / Refactoring:**
   - Invoked when an isolated code block or function replacement is requested without broad orchestration reasoning.
   - Input: Relevant file excerpt, outline, and target specification.
   - Output: Replacement code hunk with strict diff formatting.

3. **`debug` — Failure Analysis and Root-Cause Diagnosis:**
   - Invoked when `inspect_test_failure` or three consecutive tool errors occur.
   - Input: Traceback, test output, recent diff, and relevant source lines.
   - Output: Structured diagnosis containing identified failure reason, affected file, and recommended fix.

4. **`test` — Test Generation and Coverage Gap Analysis:**
   - Invoked when generating unit tests for newly written modules or reproducing a bug.
   - Input: Source module AST outline, public APIs, and behavioral requirements.
   - Output: Complete, self-contained test file or test method.

5. **`review` — Diff Review & Defect Detection:**
   - Invoked before committing changes or during explicit `/review` commands.
   - Input: Staged Git diff and modified file outlines.
   - Output: Bulleted risk analysis, anti-patterns, missing edge cases, and verification status.

6. **`documentation` — Project Documentation & API Summaries:**
   - Invoked when updating Markdown docs, docstrings, or user guides.
   - Output: Clear, idiomatic Markdown adhering to project style guidelines.

7. **`research` — Evidence and Architecture Synthesis:**
   - Invoked when comparing architectural patterns or surveying project dependencies.

---

## 3. Runtime Failover & Failure Classification

### 3.1 Failure Classification Table

When a dispatched model request returns an error or raises an exception, the failure is
classified to determine whether runtime failover is permitted:

| Error Category | Trigger | Failover Eligible? | Action |
|---|---|---|---|
| `AUTHENTICATION_ERROR` | HTTP 401 / 403, missing API key | **Yes** | Mark provider cooldown (10m); try next candidate |
| `RATE_LIMIT_EXHAUSTED` | HTTP 429, provider quota error | **Yes** | Mark candidate cooldown (60s); try next candidate |
| `SERVICE_UNAVAILABLE` | HTTP 500, 502, 503, 504 | **Yes** | Mark candidate cooldown (30s); try next candidate |
| `TRANSIENT_TIMEOUT` | Socket timeout / URLError | **Yes** | Try next candidate |
| `MODEL_NOT_FOUND` | HTTP 404, model decommissioned | **Yes** | Invalidate catalogue cache; try next candidate |
| `CONTEXT_OVERFLOW` | Context length exceeded (400) | **Conditional** | If larger context candidate exists, failover; else truncate |
| `PROMPT_REFUSAL` | Model refusal / safety block | **No** | Fail closed; return refusal message to user |
| `CLIENT_REQUEST_ERROR`| HTTP 400 (malformed schema, syntax)| **No** | Fail closed; report error to caller (do not retry same bad payload) |
| `USER_CANCELLED` | SIGINT / Ctrl+D | **No** | Abort request immediately |

### 3.2 Bounded Failover Execution Loop

```python
def dispatch_with_failover(role, task_profile, prompt_payload, project_root):
    candidates = get_eligible_candidates(role, task_profile, project_root)
    candidates.append(get_default_candidate(project_root))  # Final fallback
    
    last_error = None
    attempt = 0
    max_attempts = min(len(candidates), 3)

    for candidate in candidates:
        if attempt >= max_attempts:
            break
        attempt += 1
        try:
            log_route_attempt(role, candidate, attempt)
            response = execute_model_request(candidate, prompt_payload)
            record_route_success(role, candidate)
            return response
        except ModelRequestError as err:
            classification = classify_error(err)
            log_route_failure(role, candidate, classification)
            record_candidate_failure(candidate, classification)
            
            if not classification.is_failover_eligible:
                raise err
            last_error = err

    raise AllCandidatesExhaustedError(role, last_error)
```

---

## 4. Availability, Caching, and Invalidation

- **File Modification Watching:** Check `.shai/model_routing.json` `st_mtime` on every session turn.
  If the file has been edited since last read, flush cached routes immediately.
- **Discovery Negative Caching:** If a provider endpoint fails discovery, cache the failure for 30 seconds
  so that multiple requests in the same turn do not hang on network timeouts.
- **Provider Health Preflight:** For local providers (e.g. Ollama `http://127.0.0.1:11434/api/tags`),
  perform a lightweight socket connection check before dispatch.
- **Audit Logging:** Every routing decision logs `role`, `selected_provider`, `selected_model`,
  `priority`, `candidates_evaluated`, and `fallback_reason`. Sensitive tokens and prompt content
  are never logged.

---

## 5. Verification & Acceptance Criteria

Deterministic test suites in `test/test_model_routing.py` and `test/test_commit_message_routing.py` must verify:

1. **Role Boundary Isolation:** Specialist roles are invoked only at genuine functional boundaries.
2. **Schema v2 Parsing:** Correct parsing and validation of `context_window`, `capabilities`, `data_location`.
3. **Hard Constraint Filtering:** Rejection of candidates not meeting `TaskProfile` requirements before priority sorting.
4. **Deterministic Priority:** Stable priority sorting, tie-breaking by file order, skipping invalid/duplicate entries.
5. **Runtime Failover Matrix:**
   - 429/503 errors fail over to candidate #2.
   - 400 Bad Request does not fail over (fails closed).
   - Maximum attempt budget is strictly respected.
6. **Cache Invalidation:** Modification of `.shai/model_routing.json` triggers automatic reload.
7. **Zero Tool Re-execution:** Retrying a model call after a network error never duplicates an already executed tool call.
