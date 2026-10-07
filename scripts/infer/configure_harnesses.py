#!/usr/bin/env python3
"""Create isolated local-model homes compatible with PatchEval's adapters."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


# Agent and model settings are validated against this OpenCode release.
OPENCODE_VERSION = "1.18.31"
# Built-in agents of OpenCode 1.18.31 (`opencode agent list`, src/agent/agent.ts).
# Newer docs also list `scout`, which this release lacks; unknown names would
# define new custom agents, so only these receive a temperature.
OPENCODE_AGENTS = ("build", "plan", "general", "explore", "compaction", "summary", "title")
# Every tool runs without approval except the web tools, which could fetch the
# upstream fix. The agents' Docker network also has no internet route.
OPENCODE_PERMISSION = {"*": "allow", "webfetch": "deny", "websearch": "deny", "codesearch": "deny"}


def profile_files(profile):
    """Files of an OpenCode profile's config/ tree, keyed by path relative to the OpenCode config directory."""
    source = Path(profile).expanduser().resolve() / "config"
    if not source.is_dir():
        raise ValueError(f"OpenCode profile has no config/ directory: {source}")
    files = {}
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"OpenCode profile must not contain symlinks: {item}")
        if item.is_file():
            relative = item.relative_to(source)
            if relative.as_posix() in ("opencode.json", "opencode.jsonc", "config.json"):
                raise ValueError(f"OpenCode profile must not replace the rendered config: {item}")
            files[relative] = item.read_bytes()
    if not files:
        raise ValueError(f"OpenCode profile is empty: {source}")
    return source.parent, files


def configure(output, base_url, model, context, force=False, output_tokens=16000, temperature=None,
              opencode_profile=None):
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
        "permission": dict(OPENCODE_PERMISSION),
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
    # An OpenCode profile (rules in AGENTS.md, skills/) is copied verbatim into the
    # global config directory, which agents/opencode.sh copies into the container.
    profile, extra = (None, {}) if opencode_profile is None else profile_files(opencode_profile)
    extra = {output / "opencode/config/opencode" / relative: data for relative, data in extra.items()}
    profile_record = None if profile is None else {
        "name": profile.name, "source": str(profile),
        "files": {str(path.relative_to(output / "opencode/config/opencode")): hashlib.sha256(data).hexdigest()
                  for path, data in extra.items()},
    }
    files = {
        output / "codex/config.toml": config,
        output / "codex/local.config.toml": config,
        output / "opencode/config/opencode/opencode.json": json.dumps(opencode, indent=2) + "\n",
        output / "config-manifest.json": json.dumps({
            "model": model, "base_url": base_url, "context_length": context,
            "codex_protocol": "responses", "opencode_protocol": "chat/completions",
            "output_tokens": output_tokens, "temperature": temperature,
            "opencode_version_validated": OPENCODE_VERSION,
            "web_tools": {"codex": "web_search disabled", "opencode": OPENCODE_PERMISSION},
            "opencode_profile": profile_record,
            "enforcement": {
                "codex": "vLLM --override-generation-config (max_new_tokens, default temperature)",
                "opencode": "limit.output and agent.*.temperature; vLLM max_new_tokens also caps",
            },
        }, indent=2) + "\n",
    }
    existing = [str(path) for path in [*files, *extra] if path.exists()]
    if existing and not force:
        raise FileExistsError("Refusing to overwrite configs; use --force: " + ", ".join(existing))
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for path, data in extra.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "opencode/data").mkdir(parents=True, exist_ok=True)
    return output


def main():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.run import entrypoint
    entrypoint("configure")


if __name__ == "__main__":
    main()
