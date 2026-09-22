#!/usr/bin/env python3
"""Create isolated local-model homes compatible with PatchEval's adapters."""

import json
from pathlib import Path
from urllib.parse import urlparse


# Agent and model settings are validated against this OpenCode release.
OPENCODE_VERSION = "1.18.31"
# Built-in agents of OpenCode 1.18.31 (`opencode agent list`, src/agent/agent.ts).
# Newer docs also list `scout`, which this release lacks; unknown names would
# define new custom agents, so only these receive a temperature.
OPENCODE_AGENTS = ("build", "plan", "general", "explore", "compaction", "summary", "title")


def configure(output, base_url, model, context, force=False, output_tokens=16000, temperature=None):
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query, or fragment")
    if not parsed.path.rstrip("/").endswith("/v1"):
        raise ValueError("base URL must end in /v1")
    if not model.strip() or context < 16384 or not 0 < output_tokens < context:
        raise ValueError("model must be nonempty, context >= 16384, and 0 < output_tokens < context")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                                    or temperature < 0):
        raise ValueError("temperature must be a non-negative number or None")
    output = Path(output).expanduser().resolve()
    base_url = base_url.rstrip("/")
    quote = json.dumps  # Basic TOML strings share JSON escaping for these values.
    config = f'''model = {quote(model)}
model_provider = "vllm"
model_context_window = {context}
model_auto_compact_token_limit = {context - output_tokens}
web_search = "disabled"

[model_providers.vllm]
name = "Local vLLM"
base_url = {quote(base_url)}
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false

'''
    opencode = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"vllm/{model}",
        "small_model": f"vllm/{model}",
        "permission": "allow",
        "provider": {"vllm": {
            "npm": "@ai-sdk/openai-compatible", "name": "Local vLLM",
            "options": {"baseURL": base_url, "apiKey": "local"},
            "models": {model: {"name": model,
                               "limit": {"context": context, "output": output_tokens}}},
        }},
    }
    # Codex has no output-cap or temperature config key; vLLM enforces both.
    # OpenCode sends agent.<name>.temperature (https://opencode.ai/docs/agents/#temperature)
    # only when the model declares temperature support; custom models default to
    # false in 1.18.31, which would silently drop it. The built-in title agent
    # otherwise uses 0.5.
    if temperature is not None:
        opencode["provider"]["vllm"]["models"][model]["temperature"] = True
        opencode["agent"] = {name: {"temperature": temperature} for name in OPENCODE_AGENTS}
    files = {
        output / "codex/config.toml": config,
        output / "codex/local.config.toml": config,
        output / "opencode/config/opencode/opencode.json": json.dumps(opencode, indent=2) + "\n",
        output / "config-manifest.json": json.dumps({
            "model": model, "base_url": base_url, "context_length": context,
            "codex_protocol": "responses", "opencode_protocol": "chat/completions",
            "output_tokens": output_tokens, "temperature": temperature,
            "opencode_version_validated": OPENCODE_VERSION,
            "enforcement": {
                "codex": "vLLM --override-generation-config (max_new_tokens, default temperature)",
                "opencode": "limit.output and agent.*.temperature; vLLM max_new_tokens also caps",
            },
        }, indent=2) + "\n",
    }
    existing = [str(path) for path in files if path.exists()]
    if existing and not force:
        raise FileExistsError("Refusing to overwrite configs; use --force: " + ", ".join(existing))
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (output / "opencode/data").mkdir(parents=True, exist_ok=True)
    return output


def main():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.run import entrypoint
    entrypoint("configure")


if __name__ == "__main__":
    main()
