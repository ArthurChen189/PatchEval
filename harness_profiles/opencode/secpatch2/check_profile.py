#!/usr/bin/env python3
"""Static checks that the secpatch2 profile is well-formed, benchmark-agnostic, and fits small budgets.

- layout: the files exist, SKILL.md frontmatter matches its directory, and the class index matches the files.
- lint: no benchmark-specific terms or paths, and no CVE or GHSA identifiers, in anything OpenCode loads.
- budget: token counts with the Qwen3.8 tokenizer when `tokenizers` and the model cache are available
  (otherwise a character estimate, reported as such).
- install: the pinned OpenCode, offline and with no model, lists the `security-patch` skill and resolves its
  config from a temporary global config directory holding a nexus-style opencode.json (context 30000,
  output 12288, chat-completions provider).

usage: check_profile.py [--profile DIR] [--opencode BIN] [--tokenizer tokenizer.json] [--skip-install]
Exits 1 if any check fails; prints a JSON report.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SKILL = "security-patch"
# Budgets in tokens: AGENTS.md is in every request; SKILL.md and one class file load per task.
BUDGETS = {"AGENTS.md": 450, "SKILL.md": 1900, "class": 550}
# Benchmark mechanics or names that must not appear in a benchmark-agnostic profile.
DENYLIST = re.compile(r"fix\.patch|/workspace\b|PoC_env|patch\s*eval|fix-run|vul-run|evaluation_output"
                      r"|nexus|cwe-?bench|harbor|polar\b", re.IGNORECASE)
IDENTIFIERS = re.compile(r"\bCVE-\d{4}-\d{4,}\b|\bGHSA(-[0-9a-z]{4}){3}\b", re.IGNORECASE)
NEXUS_STYLE_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "model": "local/model", "small_model": "local/model",
    "permission": {"*": "allow", "webfetch": "deny", "websearch": "deny", "codesearch": "deny"},
    "provider": {"local": {"npm": "@ai-sdk/openai-compatible", "name": "Local",
                           "options": {"baseURL": "http://127.0.0.1:9/v1", "apiKey": "local"},
                           "models": {"model": {"name": "model", "limit": {"context": 30000, "output": 12288}}}}},
}
OFFLINE_ENV = {"OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1",
               "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
               "OPENCODE_DISABLE_SHARE": "1"}


def config_files(profile):
    return sorted(p for p in (profile / "config").rglob("*") if p.is_file())


def check_layout(profile):
    errors = []
    config = profile / "config"
    skill = config / "skills" / SKILL / "SKILL.md"
    for path in (config / "AGENTS.md", skill):
        if not path.is_file():
            errors.append(f"missing {path.relative_to(profile)}")
    if errors:
        return errors
    text = skill.read_text()
    front = re.match(r"---\n(.*?)\n---\n", text, re.S)
    fields = dict(line.split(":", 1) for line in front.group(1).splitlines() if ":" in line) if front else {}
    if fields.get("name", "").strip() != SKILL:
        errors.append("SKILL.md frontmatter name must equal its directory name")
    if not fields.get("description", "").strip():
        errors.append("SKILL.md frontmatter needs a description")
    indexed = set(re.findall(r"`(classes/[a-z0-9-]+\.md)`", text))
    present = {f"classes/{p.name}" for p in (skill.parent / "classes").glob("*.md")}
    if indexed - present:
        errors.append(f"indexed but missing: {sorted(indexed - present)}")
    if present - indexed:
        errors.append(f"present but not indexed: {sorted(present - indexed)}")
    return errors


def check_lint(profile):
    errors = []
    for path in config_files(profile):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for pattern in (DENYLIST, IDENTIFIERS):
                match = pattern.search(line)
                if match:
                    errors.append(f"{path.relative_to(profile)}:{number}: {match.group(0)!r}")
    return errors


def find_tokenizer():
    roots = [os.environ.get("HF_HOME", ""), "/mnt/local/huggingface", str(Path.home() / ".cache/huggingface")]
    for root in filter(None, roots):
        hits = glob.glob(f"{root}/hub/models--Qwen--Qwen3.8-27B/snapshots/*/tokenizer.json")
        if hits:
            return hits[0]
    return None


def check_budget(profile, tokenizer_path=None):
    try:
        from tokenizers import Tokenizer
        path = tokenizer_path or find_tokenizer()
        count = Tokenizer.from_file(path).encode if path else None
        method = f"tokenizer:{path}" if path else None
    except ImportError:
        count, method = None, None
    if count is None:
        method = "estimate:chars/3.2"
    counts, errors = {}, []
    for path in config_files(profile):
        text = path.read_text(errors="replace")
        n = len(count(text).ids) if count else round(len(text) / 3.2)
        name = str(path.relative_to(profile / "config"))
        counts[name] = n
        limit = BUDGETS.get(path.name, BUDGETS["class"] if path.parent.name == "classes" else None)
        if limit and n > limit:
            errors.append(f"{name}: {n} tokens > {limit}")
    skill = f"skills/{SKILL}/SKILL.md"
    classes = [v for k, v in counts.items() if "/classes/" in k]
    warnings = []
    if count is None:
        # A character estimate is too rough to enforce limits; report it instead.
        warnings, errors = [f"(estimate) {e}" for e in errors], []
    summary = {"method": method, "files": counts, "warnings": warnings,
               "always_on": counts.get("AGENTS.md", 0),
               "per_task_typical": counts.get("AGENTS.md", 0) + counts.get(skill, 0) + (max(classes) if classes else 0)}
    return errors, summary


def check_install(profile, opencode):
    errors, details = [], {}
    if not opencode or not Path(opencode).is_file():
        return [f"OpenCode binary not found: {opencode}"], details
    with tempfile.TemporaryDirectory(prefix="secpatch2-install-") as tmp:
        tmp = Path(tmp)
        home = tmp / "config" / "opencode"
        shutil.copytree(profile / "config", home)
        (home / "opencode.json").write_text(json.dumps(NEXUS_STYLE_CONFIG, indent=2))
        (tmp / "data").mkdir()
        work = tmp / "work"
        work.mkdir()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp),
               "XDG_CONFIG_HOME": str(tmp / "config"), "XDG_DATA_HOME": str(tmp / "data"),
               "XDG_CACHE_HOME": str(tmp / "cache"), "XDG_STATE_HOME": str(tmp / "state"), **OFFLINE_ENV}
        skills = subprocess.run([opencode, "debug", "skill"], cwd=work, env=env, capture_output=True, text=True,
                                timeout=120)
        try:
            listed = json.loads(skills.stdout[skills.stdout.index("["):])
        except ValueError:
            listed = []
            errors.append(f"`opencode debug skill` did not print JSON (exit {skills.returncode}): "
                          f"{(skills.stderr or skills.stdout)[-300:]}")
        found = [s for s in listed if s.get("name") == SKILL]
        if listed and not found:
            errors.append(f"skill {SKILL} not listed; got {[s.get('name') for s in listed]}")
        elif found and not str(found[0].get("location", "")).startswith(str(home)):
            errors.append(f"skill {SKILL} loaded from an unexpected location: {found[0].get('location')}")
        details["skills"] = [s.get("name") for s in listed]
        config = subprocess.run([opencode, "debug", "config"], cwd=work, env=env, capture_output=True, text=True,
                                timeout=120)
        details["config_exit"] = config.returncode
        if config.returncode != 0:
            errors.append(f"`opencode debug config` failed: {(config.stderr or config.stdout)[-300:]}")
        elif '"context": 30000' not in config.stdout and '"context":30000' not in config.stdout:
            errors.append("resolved config does not carry the nexus-style model limits")
    return errors, details


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", type=Path, default=HERE)
    parser.add_argument("--opencode", default=str(REPO / "third_party/opencode/1.18.31/opencode-linux-x64"))
    parser.add_argument("--tokenizer", default=None)
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args()
    report = {"layout": check_layout(args.profile), "lint": check_lint(args.profile)}
    report["budget"], report["budget_summary"] = check_budget(args.profile, args.tokenizer)
    if not args.skip_install:
        report["install"], report["install_details"] = check_install(args.profile, args.opencode)
    failed = [key for key in ("layout", "lint", "budget", "install") if report.get(key)]
    report["passed"] = not failed
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
