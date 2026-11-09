# Multimodal Model Support — Design and Implementation Plan

## Status

**Proposed — not implemented.** The current ShAI request pipeline is text-oriented. Provider adapters send the conversation history as JSON and attach the existing tool definitions, but there is no common attachment model, media ingestion pipeline, modality-aware routing, or UI workflow for supplying images/audio/video/documents.

This document defines the target design. It is intentionally a plan, not a claim that the capabilities below already work.

## 1. Goals

- Allow users to provide non-text input to Chat and Agent workflows.
- Support image understanding first, followed by audio, video, and document/file understanding.
- Keep provider-specific payload formats inside provider adapters.
- Select a model only when it satisfies the request's modality and workflow requirements.
- Preserve existing text-only behavior and existing provider/model routing as the fallback.
- Keep project files, credentials, and temporary media under the same access-control and privacy rules as other ShAI data.
- Make unsupported modalities and provider limitations explicit instead of silently dropping content.

## 2. Non-goals for the first release

- Image, audio, or video generation.
- Real-time voice conversations or streaming media.
- Training or fine-tuning models.
- Automatic OCR/transcription as a separate mandatory service.
- Sending arbitrary local project files to a remote provider without user approval.
- Guaranteeing that every provider or model supports every modality.

## 3. Current architecture and gap

The current request path is approximately:

1. Chat/Agent builds a list of role/content messages.
2. `api_request_for_role` resolves the task role and selected provider/model.
3. `modules/engine/provider_api.py` builds either an Anthropic Messages payload or an OpenAI-compatible Chat Completions payload.
4. The provider response is parsed by the existing orchestration path; tool calls are handled by the agent loop.

The current provider adapter accepts `messages` and serializes them directly. The message history is effectively text-centric. The routing resolver checks provider registration and discovered model availability, but does not know whether a model accepts images, audio, video, tools, or structured output. Model IDs discovered from a provider catalogue are therefore not proof of modality support.

Relevant existing boundaries to preserve:

- Provider configuration remains in `assets/provider_config.json`.
- Project-specific model preferences remain in `.shai/model_routing.json`.
- The routing implementation remains in `modules/model_routing.py` and the shared request boundary.
- Provider payload conversion belongs in `modules/engine/provider_api.py` or dedicated provider adapter modules, not in CLI commands or task-role call sites.
- Project access policy remains authoritative for reading local files.

## 4. Target architecture

Introduce a provider-neutral content representation between the conversation layer and provider adapters.

```text
CLI / Chat / Agent
       |
       v
Attachment intake + validation
       |
       v
Canonical message/content model
       |
       +----> Modality and task requirements
       |                |
       |                v
       |        Capability-aware model router
       |                |
       v                v
Provider adapter <--- selected provider/model
       |
       v
Provider API
       |
       v
Canonical response normalization
       |
       +---- text / tool calls / usage / media outputs (future)
```

### 4.1 Canonical content model

Messages should retain the existing outer `role` and message ordering, but `content` must be able to represent an ordered list of typed parts. Text-only messages should continue to serialize exactly as they do today wherever practical.

Proposed internal content-part types:

| Type | Purpose | Canonical fields |
|---|---|---|
| `text` | User or assistant text | `text` |
| `image` | Image input | `source`, `mime_type`, `detail` (optional) |
| `audio` | Audio input | `source`, `mime_type`, `task` (transcription/perception) |
| `video_frame` | A sampled frame from a video | `source`, `mime_type`, `timestamp_ms` |
| `file` | Document or other supported file | `source`, `mime_type`, `filename` |

`source` should be an internal attachment reference, not a provider-specific URL or an arbitrary path embedded in message history. The attachment store resolves it to validated bytes or a provider-supported upload reference during request construction.

A message may contain text and multiple media parts. Preserve part ordering. Do not flatten media into a string placeholder before provider serialization.

### 4.2 Attachment intake and lifecycle

Add a dedicated attachment service responsible for:

- Accepting files from supported CLI input mechanisms (initially explicit file paths) and future UI upload mechanisms.
- Resolving paths and enforcing project access controls before reading project files.
- Validating file existence, regular-file status, size, MIME type, and supported formats.
- Detecting MIME type from file content where feasible; do not trust the filename extension alone.
- Computing a stable content hash for deduplication within a request, without using the hash as a public identifier.
- Storing temporary copies only when needed for upload or preprocessing.
- Cleaning temporary files after the request/session retention policy expires.
- Returning a clear error for unsupported, corrupt, oversized, or inaccessible files.

Do not place binary data or large base64 strings in persistent conversation history, logs, backups, or `.shai` task state. Persist a safe attachment descriptor only when session persistence requires it; the descriptor must not make a temporary path appear permanently valid.

### 4.3 Provider adapters

Provider-specific serialization must be isolated behind adapters. The canonical conversation model must not contain OpenAI, Anthropic, or Gemini wire-format structures.

Each adapter should declare or implement:

- Which input modalities it supports for the selected model.
- Whether media can be sent inline, by URL, or through a provider file-upload API.
- Supported MIME types and relevant size/duration limits.
- Whether tool calling is supported together with each modality.
- How to normalize text, tool calls, usage, and provider errors from responses.

OpenAI-compatible endpoints are not guaranteed to have identical multimodal behavior. A provider's `format: openai` setting must not be treated as proof that every model behind that endpoint accepts the same content-part schema.

Anthropic Messages payloads and OpenAI-compatible payloads require separate serialization. Gemini's OpenAI-compatible endpoint and native Gemini API may have different feature coverage; use the configured endpoint's actual contract rather than assuming parity.

### 4.4 Capability metadata

Extend model routing with optional capability metadata. Keep candidate priority as the preference order; capabilities are eligibility filters, not scores.

Proposed candidate shape:

```json
{
  "provider": "openrouter",
  "model": "vendor/vision-model",
  "priority": 1,
  "capabilities": {
    "input_modalities": ["text", "image"],
    "output_modalities": ["text"],
    "tools": true,
    "structured_output": false,
    "max_context_tokens": null
  }
}
```

The exact metadata schema should be versioned. Unknown capability values must be treated as unknown, not as supported. Do not infer capabilities from model names.

Capability sources, in descending confidence:

1. Explicit project configuration maintained by the user.
2. Provider/model catalogue metadata when the provider supplies reliable fields.
3. A small, explicit ShAI-maintained capability registry for known model families, with provenance and update date.
4. Unknown.

Do not send a live user attachment as a capability probe. A future optional synthetic probe must use non-sensitive test content, be opt-in, and be cached.

## 5. Capability-aware routing

Routing must receive explicit request requirements, in addition to the existing task role.

Example internal requirement object:

```json
{
  "role": "debug",
  "required_input_modalities": ["text", "image"],
  "required_output_modalities": ["text"],
  "requires_tools": true,
  "requires_structured_output": false
}
```

Selection sequence:

1. Determine the workflow role from the actual call site (`chat`, `orchestrator`, `debug`, etc.).
2. Determine required modalities from the canonical message parts.
3. Determine additional requirements such as tools or structured output.
4. Load candidates for that role in priority order.
5. Reject candidates whose declared capabilities conflict with a requirement.
6. Treat missing/unknown capability metadata as ineligible when the request requires that capability. For text-only requests, preserve current behavior.
7. Select the first available eligible candidate.
8. If no configured candidate qualifies, check the normal default model only if its capabilities are known to satisfy the request.
9. If no eligible model exists, explain which capability is missing and ask the user to configure a compatible route or remove the unsupported attachment. Never silently discard media or downgrade to text-only.

Routing should distinguish `unavailable` from `unsupported_capability` in logs and user-facing diagnostics. Existing text-only routing and priority semantics must remain unchanged.

### 5.1 Proposed routing config extension

Existing role lists remain backward-compatible. The new `capabilities` object is optional per candidate. Example:

```json
{
  "version": 2,
  "models": {
    "chat": [
      {
        "provider": "openrouter",
        "model": "vendor/vision-chat",
        "priority": 1,
        "capabilities": {
          "input_modalities": ["text", "image"],
          "output_modalities": ["text"],
          "tools": true
        }
      },
      {
        "provider": "ollama",
        "model": "qwen2.5:14b",
        "priority": 2,
        "capabilities": {
          "input_modalities": ["text"],
          "output_modalities": ["text"],
          "tools": true
        }
      }
    ]
  }
}
```

A text-only request can use either candidate. An image request can use only the first. If a candidate omits capabilities, it remains usable for text-only work under legacy rules but cannot be assumed to support image/audio/video/file input.

## 6. Modality rollout

### Phase 1 — Images

- Support common raster formats: JPEG, PNG, and WebP; optionally GIF only after defining frame-selection behavior.
- Add explicit image attachment input to Chat and Agent.
- Add configurable maximum image bytes and pixel dimensions.
- Allow multiple images in one user message, preserving order.
- Provider adapters serialize images using the provider's supported image content format or upload mechanism.
- Provide clear errors for unsupported models and formats.
- Do not automatically resize or recompress unless required by a provider limit; if transformed, retain the original attachment and make the transformation deterministic.

### Phase 2 — Documents and files

- Support text and PDF extraction locally where feasible, subject to project access checks.
- Preserve page boundaries and source metadata for extracted text.
- For providers with file input APIs, support upload references behind the adapter.
- Treat office formats and archives as separate explicit capabilities; do not assume generic file support means arbitrary file understanding.
- Apply limits to extracted text, page count, archive expansion, and processing time.

### Phase 3 — Audio

- Distinguish audio transcription from audio perception/understanding; they are different tasks and may require different model capabilities.
- Support a small initial set of formats (for example WAV, MP3, M4A) after provider compatibility is verified.
- Represent task intent explicitly (`transcription` or `perception`).
- Normalize provider transcripts and timestamps into a stable response shape.
- Do not imply that text-only models can process raw audio.

### Phase 4 — Video

- Begin with local frame sampling rather than assuming native video upload support.
- Extract frames at a configurable interval and include timestamps with each frame.
- Enforce maximum duration, frame count, total extracted bytes, and processing time.
- Preserve the original video reference for context, but send only sampled frames to providers that support image input.
- Native video/audio synchronization is deferred until a provider adapter can support it reliably.

## 7. Tool calling with media

Agent mode must be able to combine a multimodal user message with existing tools where the selected model supports both capabilities.

- Tool definitions remain generated from the existing tool registry.
- Media parts must survive assistant/tool round trips and history sanitization.
- History sanitization must remove only provider-specific or unsafe blocks; it must never convert image/audio/file parts into empty text or discard them silently.
- A tool-capable text model must not be selected for a media request unless it also supports the required input modality.
- If a provider/model supports media but not tools, it may be eligible for Chat but not an Agent request that requires tools.
- Existing invalid-tool-call recovery and retry behavior must remain independent of attachment handling.

## 8. Configuration

Keep global/provider configuration separate from project-specific routing.

Proposed additions to provider configuration are adapter-level limits/defaults only, for example:

```json
{
  "multimodal": {
    "enabled": true,
    "attachments": {
      "max_file_bytes": 20971520,
      "max_images_per_message": 8,
      "temporary_retention_minutes": 60
    },
    "video": {
      "max_duration_seconds": 300,
      "frame_interval_seconds": 2,
      "max_frames": 60
    }
  }
}
```

These values are illustrative defaults, not final limits. Provider-specific hard limits must still be enforced by adapters. Project model choices and declared model capabilities belong in `.shai/model_routing.json`; API keys and provider endpoints remain in provider configuration/environment variables.

## 9. Security, privacy, and resource controls

- Apply project path allowlists/denylists before reading any local attachment.
- Require the same user approval policy used for other project-file access when applicable.
- Do not follow symlinks outside permitted project paths without explicit policy approval.
- Validate actual file type, size, dimensions, duration, and decompression limits.
- Never execute or render active content from attachments.
- Treat OCR, extracted text, image text, audio transcripts, and document contents as untrusted user data, not system instructions.
- Do not log raw media, base64 payloads, transcripts, or extracted document content by default.
- Redact API keys and provider upload URLs/tokens from request/response logs.
- Avoid storing temporary attachment copies in Git, backups, or the project `.shai` directory unless the user explicitly chooses a persistent workflow.
- Make remote transmission visible: identify the provider/model receiving the attachment before sending where the CLI flow permits.
- Add configurable limits to prevent memory exhaustion, oversized requests, and unexpected provider costs.

## 10. Error handling and fallback

Errors should be actionable and categorized:

- `attachment_not_found`
- `attachment_access_denied`
- `unsupported_media_type`
- `attachment_too_large`
- `media_processing_failed`
- `unsupported_modality`
- `model_capability_unknown`
- `provider_media_limit_exceeded`
- `provider_upload_failed`

A media request must not silently fall back to a text-only model. If the provider rejects a media payload, ShAI may retry only when the failure is transient and the same payload remains valid. It may route to the next configured eligible candidate only after a request-level failover policy is designed and the user has not opted out. Do not retry permanent validation or capability errors.

## 11. Testing strategy

Unit tests should cover:

- Canonical message validation and ordering of mixed text/media parts.
- MIME detection, corrupt files, symlinks, access-control boundaries, and size limits.
- Provider serialization for each supported adapter and modality.
- Capability matching, unknown capability behavior, role/tool requirements, and priority ordering.
- Backward compatibility for existing routing configuration and text-only requests.
- History sanitization preserving media parts and tool-call messages.
- Error mapping and no-silent-downgrade behavior.
- Temporary attachment cleanup and ensuring payload bytes never enter logs.

Integration tests should use mocked HTTP endpoints and fixture media; they must not require paid provider calls. Optional manual smoke tests may use a non-sensitive sample image/audio/video file and should be clearly separated from the automated suite.

## 12. Implementation sequence

| Milestone | Work | Exit criteria |
|---|---|---|
| M1 | Canonical content-part model and attachment intake | Mixed text/image messages validated; access controls and limits tested |
| M2 | Capability metadata and requirement-aware routing | Text routing unchanged; image requests reject unknown/incompatible models |
| M3 | First provider image adapters | At least one verified provider path supports image + text; serialization tests pass |
| M4 | Chat image workflow | User can attach and ask questions about images; errors are actionable |
| M5 | Agent image + tools | Image parts survive tool loops; compatible tool-capable model is required |
| M6 | Document/PDF support | Page-aware extraction and/or provider file upload tested |
| M7 | Audio support | Transcription/perception distinction and normalized output tested |
| M8 | Video frame sampling | Timestamped frame extraction and resource limits tested |

Do not begin later modality milestones until the previous milestone has stable tests and documented provider compatibility.

## 13. Decisions to make before implementation

1. **Initial interface:** Should image input be provided by an explicit `/attach` command, a CLI argument, or both? Recommended: both, with `/attach` as the interactive path.
2. **Attachment lifetime:** Should attachments remain available for the whole conversation or only the current request? Recommended: conversation-scoped descriptors with temporary bytes and configurable cleanup.
3. **Default image handling:** Should ShAI resize large images automatically? Recommended: no silent transformation; use deterministic adapter preprocessing only when required and report it.
4. **Capability registry ownership:** Should model capabilities be entirely project-declared or may ShAI ship a curated registry? Recommended: allow both, with explicit project metadata taking precedence and registry entries carrying provenance/update dates.
5. **Video implementation:** Native provider video input or local timestamped frame sampling? Recommended: frame sampling first for portability.
6. **Document extraction:** Which local parsers/dependencies are acceptable given ShAI's current standard-library-only runtime goal? Recommended: keep the core dependency-free and make richer parsers optional.

## 14. Acceptance criteria for multimodal support

Multimodal support is ready for general use only when:

- Text-only workflows and existing configuration remain backward-compatible.
- Attachments are validated and protected by project access controls.
- The router can prove that a selected model meets the request's modality and tool requirements.
- Every enabled provider adapter has fixture-based serialization and response tests.
- Unsupported capabilities fail clearly without silently dropping content.
- Temporary media is cleaned up and sensitive payloads are absent from logs/backups.
- User-facing documentation lists verified provider/model combinations and known limits.
