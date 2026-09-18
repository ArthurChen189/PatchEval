#!/usr/bin/env python3
"""Create isolated local-model homes compatible with PatchEval's adapters."""

import json
from pathlib import Path
from urllib.parse import urlparse


def configure(output, base_url, model, context, force=False, output_tokens=8192):
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base URL must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query, or fragment")
    if not parsed.path.rstrip("/").endswith("/v1"):
        raise ValueError("base URL must end in /v1")
    if not model.strip() or context < 16384 or not 0 < output_tokens < context:
        raise ValueError("model must be nonempty, context >= 16384, and 0 < output_tokens < context")
    output = Path(output).expanduser().resolve()
    base_url = base_url.rstrip("/")
    quote = json.dumps  # Basic TOML strings share JSON escaping for these values.
    config = f'''model = {quote(model)}
model_provider = "sglang"
model_context_window = {context}
model_auto_compact_token_limit = {context - output_tokens}
web_search = "disabled"

[model_providers.sglang]
name = "Local SGLang"
base_url = {quote(base_url)}
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false

'''
    opencode = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"sglang/{model}",
        "small_model": f"sglang/{model}",
        "permission": "allow",
        "provider": {"sglang": {
            "npm": "@ai-sdk/openai-compatible", "name": "Local SGLang",
            "options": {"baseURL": base_url, "apiKey": "local"},
            "models": {model: {"name": model,
                               "limit": {"context": context, "output": output_tokens}}},
        }},
    }
    files = {
        output / "codex/config.toml": config,
        output / "codex/local.config.toml": config,
        output / "opencode/config/opencode/opencode.json": json.dumps(opencode, indent=2) + "\n",
        output / "config-manifest.json": json.dumps({
            "model": model, "base_url": base_url, "context_length": context,
            "codex_protocol": "responses", "opencode_protocol": "chat/completions",
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
