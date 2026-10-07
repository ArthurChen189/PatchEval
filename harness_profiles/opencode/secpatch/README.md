# OpenCode security-patch profile (`secpatch`)

This profile is a set of OpenCode 1.18.31 rules and skills for tasks that **describe** a vulnerability and ask the
agent to fix it in a repository. PatchEval and CWEBench are examples. It adapts the patch, validation and review
workflow of codex-security, plus vulnerability-class notes from deepsec (both Apache-2.0; see `NOTICE.md`). Their
discovery and scanning skills are left out, because the task already names the vulnerability.

## Layout

```
config/                          # copied into OpenCode's global config directory
  AGENTS.md                      # always in the system prompt: workflow summary and environment rules
  skills/security-patch/
    SKILL.md                     # loaded on demand with the `skill` tool: full workflow
    references/vuln-class-guidance.md   # per-CWE proof tuples, complete vs incomplete fixes
    references/patch-review.md          # final bypass/regression self-review
README.md  NOTICE.md  licenses/  # provenance; not copied
```

### How the files reach the model

`AGENTS.md` is loaded from the global config directory (`$XDG_CONFIG_HOME/opencode/AGENTS.md`) and is always present.
It tells the model to load the `security-patch` skill.

Skills are discovered under `<config dir>/skills/**/SKILL.md`. The model sees each skill's name and description in
`<available_skills>`, and the `skill` tool returns the body together with the reference file paths.

## Use in PatchEval

```bash
bash scripts/run.sh action=generate harness=opencode_secpatch experiment=full label=my_secpatch_run
```

- `scripts/conf/harness/opencode_secpatch.yaml` sets `harness.profile` to this directory. Generation then copies
  `config/` into the rendered `harnesses/opencode/config/opencode/`. `agents/opencode.sh` copies that directory into
  the container as `$XDG_CONFIG_HOME/opencode`.
- `config-manifest.json` records the SHA-256 of every copied file.
- Runs are grouped under `<model>_OpenCode-secpatch_Max-output-token=<cap>/`.
- The task prompt is unchanged.

## Use elsewhere (CWEBench or a plain OpenCode)

Copy the contents of `config/` into the agent's global OpenCode config directory before starting OpenCode:

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
cp -a harness_profiles/opencode/secpatch/config/. "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/"
```

Global rules (`AGENTS.md`) are read only from that directory. Skills alone can also go in a project's
`.opencode/skills/` or in a directory named by `OPENCODE_CONFIG_DIR`. Keep the permission for the `skill` tool
allowed; it is allowed by default.

## Design choices

- **The vulnerability is assumed real.** codex-security's `no_change`/`blocked` outcomes were removed, so the agent
  always delivers a patch.
- **No subagents.** The investigator and the reviewer from `fix-finding` are inline passes, which keeps cost
  predictable under the 40-minute agent timeout.
- **Offline validation only.** There are no installs, setup is timeboxed, and scratch PoCs go in `/tmp`.
- **The final diff contains only the fix.** Existing tests are not edited and new test files are not left behind,
  because benchmarks apply the agent's diff before running their own tests.
