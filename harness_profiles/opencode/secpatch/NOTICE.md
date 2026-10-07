# Notices

The files under `config/` include material adapted from two Apache-2.0 projects. Copies of their licenses are in
`licenses/`. Every adapted file was modified; each one names its sources in a header or comment.

## codex-security

- Upstream: https://github.com/openai/codex-security (local checkout `/home/ubuntu/Workplace/codex-security`,
  commit `90c54917`). Copyright 2025 OpenAI. Licensed under the Apache License, Version 2.0
  (`licenses/codex-security-LICENSE`). No NOTICE file upstream.
- Used in `config/skills/security-patch/SKILL.md`:
  - `plugins/codex-security/skills/fix-finding/SKILL.md`
  - `plugins/codex-security/references/static-finding-assessment.md`
  - `plugins/codex-security/skills/validation/SKILL.md`
  - `plugins/codex-security/skills/triage-finding/SKILL.md` (one paragraph on control semantics)
- Used in `config/skills/security-patch/references/vuln-class-guidance.md`:
  - `plugins/codex-security/skills/validation/references/validation-guidance.md`
- Used in `config/skills/security-patch/references/patch-review.md`:
  - `plugins/codex-security/skills/verify-fix/SKILL.md`
  - `plugins/codex-security/skills/assess-patch-risk/SKILL.md`
  - `plugins/codex-security/skills/assess-patch-risk/references/risk-rubric.md`
- Changes:
  - Removed the Codex Security MCP artifact storage, scan ledgers, and workbench remediation stages.
  - Removed `fork_turns` subagent delegation; the investigator and reviewer became inline passes.
  - Removed the `no_change`/`blocked` outcomes and the JSON result contracts.
  - Restricted validation to offline, timeboxed checks with scratch files under `/tmp`.
  - Reworded scan-oriented validation rules as patch-completeness rules and grouped them by CWE.
- This profile is not Codex Security and is not affiliated with OpenAI.

## deepsec

- Upstream: deepsec (local checkout `/home/ubuntu/Workplace/deepsec`, commit `4fa6722`). Licensed under the Apache
  License, Version 2.0 (`licenses/deepsec-LICENSE`).
- Its NOTICE file (`licenses/deepsec-NOTICE`) reads:

  > deepsec
  > Copyright 2026 Vercel, Inc. and contributors
  >
  > This product includes software developed at Vercel, Inc. (https://vercel.com/).

- Used in `config/skills/security-patch/references/vuln-class-guidance.md`:
  - `packages/processor/src/prompt/slug-notes.ts`
  - `packages/processor/src/prompt/highlights.ts`
  - Header comments of these scanner matchers: `untrusted-redirect-following.ts`, `fs-write-symlink-boundary.ts`,
    `url-regex-validation.ts`, and `.deepsec/matchers/archive-extraction-untrusted.ts`
  - `packages/processor/src/prompt/core.ts` (mitigation and auth-bypass notes)
- Used in `config/skills/security-patch/references/patch-review.md`:
  - `packages/processor/src/agents/shared.ts` (revalidation investigation steps)
- Changes: reworded the scanner "flag if…" notes as fix guidance and grouped them by CWE. The "static analysis only"
  restriction was not copied.
- This profile is not affiliated with Vercel.

## Profile additions

Lines marked `[profile]` in `vuln-class-guidance.md`, and `config/AGENTS.md`, were written for this profile. They
are general remediation guidance. They were not derived from PatchEval or CWEBench cases or their reference patches.
