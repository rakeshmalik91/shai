# Supported Providers

Shai supports cloud APIs and local Ollama. Provider endpoints, authentication,
headers, model-list filtering and request timeouts are configurable in
`assets/provider_config.json`; the built-in values below are defaults.

## Provider comparison

| Provider | Authentication | Model list | Notes |
|---|---|---|---|
| OpenRouter | `OPENROUTER_API_KEY` | All `:free` models | Free-only filter by default |
| Gemini | `GEMINI_API_KEY` | Models supporting generation | Google OpenAI-compatible endpoint |
| Anthropic | `ANTHROPIC_API_KEY` | Anthropic Models API | Native Messages API |
| OpenAI | `OPENAI_API_KEY` | OpenAI Models API | Chat Completions API |
| Kilo | `KILO_API_KEY` | Non-free models only | Free models deliberately hidden |
| Kilo-free | `KILO_API_KEY` | `:free` models only | Separate free-only selection |
| Ollama | None by default | Locally installed models | Local OpenAI-compatible chat endpoint |

## Setup

Install requirements and configure any required API keys:

```sh
python3 -m pip install -r requirements.txt
cp setup_keys.sample.sh setup_keys.sh
# Edit setup_keys.sh, then:
. ./setup_keys.sh
```

`setup_keys.sh` is gitignored. Do not commit API keys. Ollama does not need an
API key by default.

## Provider configuration

Add provider overrides to `assets/provider_config.json` under the `providers`
section. The default provider/model is configured separately in
`assets/model_routing.json` and can be overridden per project in
`.shai/model_routing.json`.

```json
{
  "api_timeout_seconds": 300,
  "providers": {
    "ollama": {
      "base_url": "http://127.0.0.1:11434/v1",
      "url": "http://127.0.0.1:11434/v1/chat/completions",
      "models_url": "http://127.0.0.1:11434/api/tags",
      "format": "openai",
      "auth_type": "none",
      "key_env": null,
      "models_filter": "ollama",
      "timeout_seconds": 300
    },
    "openrouter": {
      "url": "https://openrouter.ai/api/v1/chat/completions",
      "models_url": "https://openrouter.ai/api/v1/models",
      "key_env": "OPENROUTER_API_KEY",
      "format": "openai",
      "auth_type": "bearer",
      "headers": {
        "HTTP-Referer": "https://openrouter.ai/",
        "X-Title": "Shai"
      }
    }
  }
}
```

### Supported settings per provider

| Setting | Meaning |
|---|---|
| `base_url` | Convenience base URL; derives `/chat/completions` and `/models` unless explicitly overridden |
| `url` / `chat_url` | Exact chat-completions or Messages endpoint |
| `models_url` | Exact model discovery endpoint |
| `key_env` | Environment variable containing the API key; set to `null` for keyless local providers |
| `format` | `openai` or `anthropic`; controls request/response serialization |
| `auth_type` | `bearer`, `header`, `query`, or `none` |
| `auth_header` | Header name when `auth_type` is `header` |
| `auth_prefix` | Bearer prefix; defaults to `Bearer ` |
| `auth_query_param` | Query parameter when `auth_type` is `query` |
| `headers` | Additional static HTTP headers |
| `models_filter` | `all`, `free_only`, `exclude_free`, or `ollama` |
| `timeout_seconds` | Provider-specific HTTP timeout |

Provider overrides are merged with built-in defaults. You only need to specify
values you want to change. Do not put API key values directly in this file;
use environment variables and `key_env`.

## Ollama

1. Install and start Ollama on the machine running the Ollama server.
2. Pull at least one model, for example:

   ```sh
   ollama pull llama3.2
   ollama list
   ```

3. Ensure Shai can reach the server. On the same machine use
   `http://127.0.0.1:11434`; on another device, set the server's reachable LAN
   address, for example `http://192.168.1.20:11434`.
4. Select **Ollama (local)** in Shai. Shai reads installed models from
   `/api/tags` and sends chat/tool requests to `/v1/chat/completions`.

The Ollama provider uses the OpenAI-compatible endpoint and enables tool
calling when supported by the installed Ollama version/model. On iSH, localhost
means the iSH environment itself; if Ollama runs on another computer, configure
that computer's LAN address and ensure its server accepts LAN connections.

## Kilo model visibility

- **Kilo** excludes IDs ending in `:free`, so the regular Kilo list contains
  non-free models only.
- **Kilo-free** shows only IDs ending in `:free`.
- OpenRouter retains its existing free-only model filter.

## Switching providers and models

Use `/model` (or `/switch`, `/switch-model`) in Chat or Agent mode. Shai first
shows the provider list and asks you to choose a provider. It then fetches the
provider's model IDs only, displays the filtered model list, and asks you to
choose a model. It does not fetch or save metadata for every model in that
list. After you select a model, Shai makes one best-effort, short-timeout
request for that model's details only; metadata lookup failure does not block
selection or the next model request. The selected default provider/model is persisted
in the active project's `.shai/model_routing.json` under `default`; task-specific
routes share that file. Provider endpoint and authentication overrides live in
`assets/provider_config.json`.

## Model capabilities and context windows

Shai stores project-local provider-discovered model limits and capabilities in
the active project's `.shai/model_config.json`. The `models` section contains discovered metadata,
while the `overrides` section holds explicit per-model values. Overrides take
precedence over discovered metadata, and `defaults` provide fallbacks. Entries
are keyed by `provider::model`.

For example, a model can declare `context_window`, `max_output_tokens`,
`supported_input_types`, `supported_output_types`, `supports_tools`,
`supports_structured_output`, and `supports_reasoning`. A `null` capability
means unknown rather than unsupported. Content type values are extensible.

## Troubleshooting

| Symptom | Check |
|---|---|
| `KEY_ENV is not set` | Source `setup_keys.sh`; verify `key_env` |
| No models returned | Check endpoint, authentication and network access |
| Ollama connection refused | Confirm Ollama is running and reachable from Shai's device |
| Ollama model missing | Run `ollama pull <model>` on the Ollama host |
| 401/403 | Verify key, auth type and custom headers |
| Kilo free model appears in regular Kilo | Confirm `models_filter` is `exclude_free` |
| Saved model unavailable | Model may have been removed or filtered; select another |

## Related documentation

- [Commands Reference](COMMANDS.md)
- [Tools Reference](TOOLS.md)
- [Architecture](ARCHITECTURE.md)


## Task-based model routing

Shai supports project-local model routing by task category. Configure candidate
models in `.shai/model_routing.json`; use `assets/model_routing.json` as the
tracked template. Candidates are ordered by ascending `priority` (1 is highest).
Model IDs are used as configured; Shai does not download a provider catalogue
to validate them. An unavailable route is skipped in favour of the next
candidate, then the normal default provider/model. See
[CONFIGURATION.md](CONFIGURATION.md#model-routing) for the complete schema and
supported roles. Shai's `.shai/.gitignore` ignores only logs, backups, task state,
and conversation history; project preferences and routing can be committed.
