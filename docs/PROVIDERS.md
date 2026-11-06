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
section to your existing file). Existing settings are preserved when Shai
saves the selected provider/model.

```json
{
  "provider": "ollama",
  "model": "llama3.2",
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

Use `/model` (or `/switch`, `/switch-model`) in Chat or Agent mode. Shai fetches
the selected provider's model list, lets you choose a model, and persists the
selection in `assets/shai_config.json`; provider endpoint and authentication overrides live in `assets/provider_config.json`.

## Context windows

Shai captures provider-reported context limits in `model_context_windows`.
You can set a separate, explicit per-model override in
`assets/context_window_config.json`. Overrides take precedence over metadata
returned by the provider and are keyed by `provider::model`:

```json
{
  "model_context_windows": {
    "ollama::llama3.2": 131072,
    "openrouter::provider/model:free": 131072
  },
  "model_context_window_overrides": {
    "ollama::llama3.2": 65536,
    "openrouter::provider/model:free": 98304
  }
}
```

`model_context_windows` is provider-discovered metadata; normally let Shai
manage it. `model_context_window_overrides` is user-managed and is never
replaced when model metadata is refreshed or a provider/model is selected.
Values must be positive integer token counts. Remove an override entry to
return to the provider-reported value. The legacy `context_window_tokens`
setting remains supported for the currently selected model when no
model-specific value is available.


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
