# OpenCode security-patch profile v2 (`secpatch2`)

secpatch2 is a set of benchmark-agnostic OpenCode 1.18.31 rules and skills for tasks that describe a security
weakness and ask the agent to fix it in a repository. Examples are PatchEval CVEs and the nexus
cybersecurity-training (audit-and-patch) tasks. It replaces v1 (`../secpatch/`, kept unchanged for
reproducibility). The provenance and the anti-overfitting rule are in `NOTICE.md`.

## Layout

```
config/                                  # copied into OpenCode's global config directory
  AGENTS.md                              # always in the system prompt (~320 tokens)
  skills/security-patch/SKILL.md         # loaded with the `skill` tool (~1,860 tokens): steps, review
  skills/security-patch/classes/*.md     # one small file per weakness family (310-510 tokens each)
check_profile.py                         # static checks (layout, agnostic lint, token budget, OpenCode install)
README.md  NOTICE.md  licenses/          # provenance; not copied
```

## What changed from v1, and why

Each change answers a pattern found in v1's PatchEval validation failures:
- **Short, tool-driven reasoning; plans kept in `todowrite`; act once an edit is known; no recalling upstream
  fixes.** Every empty patch came from one response spending the whole output budget on reasoning (finish
  reason `length`). The model was planning the full patch in its head or recalling upstream fixes.
- **Safe-by-default priority.** Vulnerable behavior is not "legitimate", even when a default or a test relies on
  it. v1 put "preserve legitimate behavior / checks pass" high, and agents kept insecure defaults for
  compatibility.
- **Path inventory and variant matrix before fixing; a table-driven check of the described impact under the
  default configuration.** Failed patches covered one entry point or one input variant.
- **Regression triage.** A newly failing test means narrow the fix; never edit tests. Agents rewrote or ignored
  tests that started failing.
- **Review only after the diff exists, inline in `SKILL.md`.** v1's separate checklist was read before any edit
  and caught none of the decisive defects.
- **Per-family class files instead of one large reference.** This fits a 30k-token context and covers more
  languages and CWEs.

## Results so far (PatchEval val46, Qwen3.8-27B, 4 samples)

Full report: `patcheval/exp_agent/agent_runs/analysis_reports/20261006_051507-opencode-secpatch2-val46/`.

**Headline:** secpatch2 with continue-on-length, in the fixed agent environment, reaches pass@1 74.5. The old
plain-OpenCode baseline scored 65.8 (+8.7 pp, p = 0.023) and v1 scored 66.8.

| Component | Effect on pass@1 | p |
|---|---|---|
| Continue-on-length | +4.3 to +4.9 pp | about 0.02 to 0.06 |
| Profile content | +3.3 to +3.8 pp | not significant |

Main remaining failure mode: timeouts from long test runs.

These numbers come from the split whose failures shaped the method.

**Held-out check (nexus iid100, 2026-10-07): no gain.**
- **Setup:** 100 cyber-training tasks × 4 samples, base Qwen3.8-27B, nexus's own prompt and OpenCode limits (context
  30k, output 12k, 40-minute cap), against plain OpenCode run back to back.
- **Result:** the profile plus continuation scored 28.5% against 30.25% (−1.8 pp, 95% CI −4.9 to +1.4, p = 0.35), at
  +29% cost per rollout.
- **Why:** its longer method pushed 15 percentage points more rollouts into the cap (46.75% vs 31.75%). Continuation
  never fired there: OpenCode's own auto-compaction resumes those sessions under nexus's 30k/12k limits.
- **Details:** `cyber-train/docs/arthur/qwen38-27b-secpatch2-iid100/README.md`.

## Use in PatchEval

```bash
bash scripts/run.sh action=generate harness=opencode_secpatch2 experiment=full label=my_run
```

`scripts/conf/harness/opencode_secpatch2.yaml` sets:
- `harness.profile`: generation copies `config/` into the rendered OpenCode config directory;
- `continue_on_length: 2`: `patcheval/exp_agent/container/opencode_continue.sh` continues a session up to
  twice when a response ends at the output-token limit.

Agent containers also get the pinned ripgrep, which OpenCode's `grep`, `glob`, and `skill` tools need.

## Use elsewhere (nexus/cyber-train or a plain OpenCode)

The profile assumes nothing about PatchEval.

1. **Install the rules and skill.** Copy `config/` into the agent's global OpenCode config directory, next to its
   `opencode.json`:
   ```bash
   cp -a harness_profiles/opencode/secpatch2/config/. "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/"
   ```
   In cyber-train's nexus rollouts that directory is `/root/.config/opencode/`. The `PREPARE` step writes
   `opencode.json` there, so the copy goes after it. `OPENCODE_CONFIG_CONTENT` does not carry files; `AGENTS.md`
   is read only from the global config directory.
2. **ripgrep.** The `skill`, `grep`, and `glob` tools need `rg` on `PATH`. The nexus task images install it.
3. **Optional: continue after length stops.** Wrap `opencode run` with
   `patcheval/exp_agent/container/opencode_continue.sh`.
   - The first call passes through all `opencode run` arguments (prompt on stdin or as a positional).
   - `--format json` is required.
   - Continuations use `OPENCODE_CONTINUE_RUN_ARGS`. For nexus that is
     `-m <model> --format json`; nexus sets permissions in `opencode.json`.
   - Example:
     `OPENCODE_CONTINUE_ON_LENGTH=2 OPENCODE_CONTINUE_RUN_ARGS="-m <model> --format json" bash opencode_continue.sh -m <model> --format json -- "<prompt>"`.
4. **Budget.** With nexus's `limit.context=30000`, the always-on overhead is `AGENTS.md` plus the skill listing,
   about 0.4k tokens. A task that loads the skill and one class file adds about 2.4k tokens.

cyber-train is not modified by this profile.

## Static checks

```bash
/mnt/local/patcheval-local-llm/venv-vllm/bin/python harness_profiles/opencode/secpatch2/check_profile.py
```

The checks:
- layout and frontmatter, and that the class index in SKILL.md matches the files;
- agnostic lint: no PatchEval-specific paths or terms, no CVE or GHSA ids;
- token budgets with the Qwen3.8 tokenizer, or a character estimate if `tokenizers` is unavailable;
- an offline OpenCode install check: the pinned binary, with a nexus-style `opencode.json` (context 30000,
  output 12288), lists the `security-patch` skill and resolves its config.
