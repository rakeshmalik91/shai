# Supported Providers

Shai supports the following LLM providers. Each requires an API key set as an environment variable.

---

## Quick Comparison

| Provider | Key Env Var | Format | Free Tier | Notes |
|----------|-------------|--------|-----------|-------|
| **OpenRouter** | `OPENROUTER_API_KEY` | OpenAI-compatible | ✅ Many free models | Access 100+ models via single API |
| **Gemini** | `GEMINI_API_KEY` | OpenAI-compatible | ✅ Generous free tier | Google's models (Gemini 1.5, 2.0, etc.) |
| **Anthropic** | `ANTHROPIC_API_KEY` | Anthropic native | ❌ Paid only | Claude 3.5 Sonnet, Opus, Haiku |
| **OpenAI** | `OPENAI_API_KEY` | OpenAI native | ❌ Paid only | GPT-4o, GPT-4o-mini, o1, etc. |
| **Kilo Gateway** | `KILO_API_KEY` | OpenAI-compatible | ✅ Free models available | Enterprise gateway with free tier |
| **Kilo Gateway (Free Only)** | `KILO_API_KEY` | OpenAI-compatible | ✅ Free models only | Filters to `:free` models only |

---

## Setup

1. Copy the sample keys file:
   ```sh
   cp setup_keys.sample.sh setup_keys.sh
   ```

2. Edit `setup_keys.sh` and uncomment/fill in **one** provider's API key:
   ```sh
   # Example for OpenRouter
   export OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxxx"
   ```

3. Source it in your shell:
   ```sh
   . ./setup_keys.sh
   ```

> **Security:** `setup_keys.sh` is gitignored. Never commit it or share it publicly.

---

## Provider Details

### OpenRouter
- **API Base:** `https://openrouter.ai/api/v1`
- **Models Endpoint:** `/models`
- **Chat Endpoint:** `/chat/completions`
- **Format:** OpenAI-compatible
- **Key:** `OPENROUTER_API_KEY`
- **Free Models:** Many (filter: `:free` suffix)
- **Website:** https://openrouter.ai

> OpenRouter aggregates models from multiple providers. Free models have `:free` suffix (e.g., `meta-llama/llama-3.1-8b-instruct:free`). Shai automatically filters to free models when you select the "OpenRouter" provider.

### Gemini (Google)
- **API Base:** `https://generativelanguage.googleapis.com/v1beta/openai`
- **Models Endpoint:** `/models`
- **Chat Endpoint:** `/chat/completions`
- **Format:** OpenAI-compatible (via Google's OpenAI compatibility layer)
- **Key:** `GEMINI_API_KEY`
- **Free Tier:** Yes, generous limits
- **Website:** https://ai.google.dev

> Uses Google's OpenAI-compatible endpoint. Supports Gemini 1.5 Pro/Flash, 2.0 Flash, etc. API key passed as query parameter `?key=...`.

### Anthropic
- **API Base:** `https://api.anthropic.com/v1`
- **Models Endpoint:** `/models?limit=1000`
- **Chat Endpoint:** `/messages`
- **Format:** Anthropic native (messages API)
- **Key:** `ANTHROPIC_API_KEY`
- **Free Tier:** No
- **Website:** https://console.anthropic.com

> Uses Anthropic's native Messages API format. Requires `anthropic-version: 2023-06-01` header. Models include Claude 3.5 Sonnet, Opus, Haiku.

### OpenAI
- **API Base:** `https://api.openai.com/v1`
- **Models Endpoint:** `/models`
- **Chat Endpoint:** `/chat/completions`
- **Format:** OpenAI native
- **Key:** `OPENAI_API_KEY`
- **Free Tier:** No (trial credits only)
- **Website:** https://platform.openai.com

> Standard OpenAI Chat Completions API. Supports GPT-4o, GPT-4o-mini, o1-preview, o1-mini, etc.

### Kilo Gateway
- **API Base:** `https://ai-gateway.kilo.ai/api/v1`
- **Models Endpoint:** `/models`
- **Chat Endpoint:** `/chat/completions`
- **Format:** OpenAI-compatible
- **Key:** `KILO_API_KEY`
- **Free Tier:** Yes (select models)
- **Website:** https://kilo.ai

> Enterprise AI gateway. Two provider entries:
> - **Kilo Gateway** — All available models
> - **Kilo Gateway (Free Only)** — Filters to `:free` models only

---

## Model Selection

When you run `agent.py` or `chat.py`, Shai will:

1. **Prompt for provider** (shows last used as option 0)
2. **Fetch available models** from the provider's `/models` endpoint
3. **Prompt for model** (shows last used as default)
4. **Save selection** to `shai_config.json`
5. **Display context window** if provider reports it

### Auto-Selection
On subsequent runs, Shai can auto-select the last used provider/model if valid:
```python
# In code: initialize_provider(auto_select=True)
```

---

## Context Window Handling

Shai tracks model context windows from three sources (in priority order):

1. **Provider-reported** — Fetched from `/models` endpoint (e.g., `context_length`, `input_token_limit`)
2. **User-configured** — Set in `shai_config.json` under `model_context_windows["provider::model"]`
3. **Fallback** — `context_window_tokens` in config (legacy single-model setting)

If unknown, Shai displays "unknown" and you can manually configure it:
```json
{
  "model_context_windows": {
    "openrouter::meta-llama/llama-3.1-8b-instruct:free": 131072,
    "anthropic::claude-3-5-sonnet-20241022": 200000
  }
}
```

---

## Configuration File (`shai_config.json`)

Relevant provider/model settings:

```json
{
  "provider": "openrouter",
  "model": "meta-llama/llama-3.1-8b-instruct:free",
  "context_window_tokens": 131072,
  "model_context_windows": {
    "openrouter::meta-llama/llama-3.1-8b-instruct:free": 131072,
    "anthropic::claude-3-5-sonnet-20241022": 200000
  }
}
```

---

## Switching Providers/Models

Inside Shai, use `/model` (or `/switch`, `/switch-model`):
- Prompts for new provider
- Fetches models for that provider
- Prompts for model
- Clears tool-call history for provider compatibility
- Retains text conversation context

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `"KEY_ENV is not set"` | Source `setup_keys.sh` in your shell: `. ./setup_keys.sh` |
| `"No models are currently available"` | Check API key validity and network connectivity |
| `"Saved model not available"` | Model may be deprecated; select a different one |
| `401 Unauthorized` | Invalid or expired API key |
| `429 Rate Limited` | Wait and retry; check provider quota |

---

## Adding Custom Providers

To add a custom OpenAI-compatible provider, edit `modules/provider_selection.py`:

```python
PROVIDERS = {
    # ... existing providers ...
    "my-provider": {
        "url": "https://api.my-provider.com/v1/chat/completions",
        "models_url": "https://api.my-provider.com/v1/models",
        "key_env": "MY_PROVIDER_API_KEY",
        "format": "openai",
    },
}
```

Then add the key to `setup_keys.sample.sh` and your `setup_keys.sh`.

---

## Related Documentation

- [Commands Reference](COMMANDS.md) — `/model` command usage
- [Tools Reference](TOOLS.md) — Model-callable tools
- [Architecture](ARCHITECTURE.md) — Provider integration details