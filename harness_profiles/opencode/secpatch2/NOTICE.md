# Notices

The files under `config/` contain material adapted from two Apache-2.0 projects. Copies of their licenses are in
`licenses/`. Every adapted file was modified; `SKILL.md` names the sources in a comment.

## codex-security

- **Upstream:** https://github.com/openai/codex-security, local checkout at commit `90c54917`. Copyright 2025
  OpenAI. Licensed under the Apache License, Version 2.0 (`licenses/codex-security-LICENSE`). There is no upstream
  NOTICE file.
- **Used for the workflow, priorities, and review** in `config/skills/security-patch/SKILL.md`, through the v1
  profile (`../secpatch/`):
  - `plugins/codex-security/skills/fix-finding/SKILL.md`
  - `plugins/codex-security/references/static-finding-assessment.md`
  - `plugins/codex-security/skills/validation/SKILL.md`
  - `plugins/codex-security/skills/verify-fix/SKILL.md`
  - `plugins/codex-security/skills/assess-patch-risk/SKILL.md`
  - `plugins/codex-security/skills/assess-patch-risk/references/risk-rubric.md`
- **Used for the "complete control" rules** in `config/skills/security-patch/classes/*.md`:
  `plugins/codex-security/skills/validation/references/validation-guidance.md`.
- **Changes:**
  - Condensed into numbered steps.
  - Removed the MCP artifact storage, scan workbench, subagent delegation, `no_change`/`blocked` outcomes, and
    JSON contracts.
  - The investigation and review are inline passes.
  - Validation is offline and timeboxed.
  - The scan-oriented validation rules became patch-completeness rules, grouped by weakness family.
- This profile is not Codex Security and is not affiliated with OpenAI.

## deepsec

- **Upstream:** deepsec, local checkout at commit `4fa6722`. Licensed under the Apache License, Version 2.0
  (`licenses/deepsec-LICENSE`).
- **Its NOTICE** (`licenses/deepsec-NOTICE`):

  > deepsec
  > Copyright 2026 Vercel, Inc. and contributors
  >
  > This product includes software developed at Vercel, Inc. (https://vercel.com/).

- **Used in `config/skills/security-patch/classes/*.md`, through the v1 profile:**
  - `packages/processor/src/prompt/slug-notes.ts`
  - `packages/processor/src/prompt/highlights.ts`
  - `packages/processor/src/prompt/core.ts`
  - the header comments of the `untrusted-redirect-following`, `fs-write-symlink-boundary`,
    `url-regex-validation`, and `archive-extraction-untrusted` matchers
- **Used in the review step of `SKILL.md`:** `packages/processor/src/agents/shared.ts` (the revalidation steps).
- **Changes:** the scanner's "flag if…" notes were reworded as fix guidance and grouped by weakness family. The
  "static analysis only" restriction was not copied.
- This profile is not affiliated with Vercel.

## Profile additions and anti-overfitting rule

The rest of the text was written for this profile, using standard remediation practice for the listed CWE
families:
- `config/AGENTS.md`;
- the priorities and steps of `SKILL.md`;
- most class-file content, and the C/C++, Rust, Java, crypto/TLS, logging, defaults, and concurrency material.

The weakness families follow the CWE mix of the nexus cybersecurity-training tasks and of PatchEval.

An analysis of PatchEval validation failures (see `README.md`) informed only the generic method:
- path inventory;
- variant matrix;
- safe-by-default priority;
- regression triage;
- review after the diff exists;
- short, tool-driven reasoning.

No text was derived from any benchmark case, hidden test, or reference patch. The profile contains no CVE
identifiers.
