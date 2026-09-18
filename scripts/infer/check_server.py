#!/usr/bin/env python3
"""Streaming protocol checks (stdlib only), with a Hydra CLI alias."""

import json
import os
import sys
import urllib.error
import urllib.request


FUNCTION = {"name": "lookup_code", "description": "Return the secret verification code.",
            "parameters": {"type": "object", "properties": {"key": {"type": "string", "enum": ["verification"]}},
                           "required": ["key"], "additionalProperties": False}}
PROMPT = "Call lookup_code with key set to verification, then repeat its result exactly. Do not guess the code."
RESULT = "PATCHEVAL_CHECK_7291"


def sse_events(response):
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                payload = "\n".join(data)
                data = []
                if payload == "[DONE]":
                    return
                yield json.loads(payload)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        payload = "\n".join(data)
        if payload != "[DONE]":
            yield json.loads(payload)


class Client:
    def __init__(self, base_url, timeout):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, endpoint, payload=None):
        headers = {"Content-Type": "application/json"}
        # SGLANG_API_KEY remains a deprecated compatibility fallback.
        api_key = os.environ.get("VLLM_API_KEY") or os.environ.get("SGLANG_API_KEY")
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        req = urllib.request.Request(self.base_url + endpoint,
                                     data=None if payload is None else json.dumps(payload).encode(),
                                     headers=headers)
        return urllib.request.urlopen(req, timeout=self.timeout)

    def stream(self, endpoint, payload):
        with self.request(endpoint, {**payload, "stream": True}) as response:
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise ValueError("Expected a streaming SSE response")
            for event in sse_events(response):
                if "error" in event or event.get("type") in ("error", "response.failed"):
                    raise ValueError(f"Server stream error: {event}")
                yield event


def chat(client, model):
    messages = [{"role": "user", "content": PROMPT}]
    payload = {"model": model, "messages": messages,
               "tools": [{"type": "function", "function": FUNCTION}],
               "tool_choice": {"type": "function", "function": {"name": FUNCTION["name"]}},
               "max_tokens": 8192}
    calls = {}
    for event in client.stream("/chat/completions", payload):
        for choice in event.get("choices", []):
            for part in choice.get("delta", {}).get("tool_calls", []):
                call = calls.setdefault(part["index"], {"id": "", "type": "function",
                                                        "function": {"name": "", "arguments": ""}})
                call["id"] += part.get("id") or ""
                for key in ("name", "arguments"):
                    call["function"][key] += (part.get("function") or {}).get(key) or ""
    if len(calls) != 1:
        raise ValueError("Expected one structured Chat Completions tool call")
    call = next(iter(calls.values()))
    if not call["id"] or call["function"]["name"] != FUNCTION["name"] or json.loads(call["function"]["arguments"]) != {"key": "verification"}:
        raise ValueError("Invalid Chat Completions tool call")
    messages.extend([{"role": "assistant", "content": None, "tool_calls": [call]},
                     {"role": "tool", "tool_call_id": call["id"], "content": RESULT}])
    payload["tool_choice"] = "none"
    text = ""
    for event in client.stream("/chat/completions", payload):
        for choice in event.get("choices", []):
            text += choice.get("delta", {}).get("content") or ""
    if RESULT not in text:
        raise ValueError("Chat Completions did not return the tool result")


def responses(client, model):
    inputs = [{"role": "user", "content": PROMPT}]
    payload = {"model": model, "input": inputs, "store": False,
               "tools": [{"type": "function", **FUNCTION}],
               "tool_choice": {"type": "function", "name": FUNCTION["name"]},
               "max_output_tokens": 8192}
    completed = None
    for event in client.stream("/responses", payload):
        if event.get("type") == "response.completed":
            completed = event["response"]
    if completed is None:
        raise ValueError("Responses stream never completed")
    output = completed.get("output", [])
    calls = [item for item in output if item.get("type") == "function_call"]
    if len(calls) != 1 or calls[0].get("name") != FUNCTION["name"] or not calls[0].get("call_id"):
        raise ValueError("Expected one structured Responses function call")
    if json.loads(calls[0]["arguments"]) != {"key": "verification"}:
        raise ValueError("Invalid Responses function arguments")
    inputs.extend(output)
    inputs.append({"type": "function_call_output", "call_id": calls[0]["call_id"], "output": RESULT})
    payload["tool_choice"] = "none"
    text, done = "", False
    for event in client.stream("/responses", payload):
        if event.get("type") == "response.output_text.delta":
            text += event.get("delta", "")
        if event.get("type") == "response.completed":
            done = True
    if not done or RESULT not in text:
        raise ValueError("Responses did not complete the tool-result round trip")


def run_checks(base_url, model, protocol="both", timeout=300):
    client = Client(base_url, timeout)
    try:
        with client.request("/models") as response:
            models = json.load(response)
        if model not in [entry["id"] for entry in models["data"]]:
            raise ValueError(f"Model {model!r} is not advertised by /models")
    except (OSError, ValueError, KeyError) as exc:
        print(f"Endpoint/model check failed: {exc}", file=sys.stderr)
        return 1
    failed = False
    for name, check in (("chat", chat), ("responses", responses)):
        if protocol not in ("both", name):
            continue
        try:
            check(client, model)
            print(f"PASS {name}: streaming tool-call/result round trip")
        except (OSError, ValueError, KeyError) as exc:
            harness = "Codex" if name == "responses" else "OpenCode"
            print(f"FAIL {harness} compatibility ({name}): {exc}", file=sys.stderr)
            failed = True
    return int(failed)


def main():
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.run import entrypoint
    entrypoint("check")


if __name__ == "__main__":
    main()
