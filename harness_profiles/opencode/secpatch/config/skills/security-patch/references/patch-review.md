# Patch review checklist

Run this once, after the patch is implemented and before delivering it. Review the current `git diff` against the
vulnerability description as if someone else wrote it. Treat each finding as a hypothesis and confirm it in the
source or with a focused command before changing the patch.

Sources: codex-security `skills/verify-fix/SKILL.md`, `skills/assess-patch-risk/SKILL.md` and
`references/risk-rubric.md` (Apache-2.0, Copyright 2025 OpenAI); deepsec revalidation prompt in
`packages/processor/src/agents/shared.ts` (Apache-2.0, Copyright 2026 Vercel, Inc. and contributors). Edited for a
self-review pass inside the fixing session (see NOTICE.md).

## 1. Is the original vulnerability closed?

From codex-security verify-fix:

1. Establish the original vulnerability, its preconditions, affected security boundary, and legitimate behavior that must continue to work.
2. Follow moved or refactored code rather than treating a missing file, removed line, or changed function name as proof of remediation.
3. Trace the original exploit path through the current implementation and check the nearest relevant control, equivalent paths, and plausible bypasses.
4. Run the original reproducer, focused regression checks, or legitimate-behavior checks when they can run offline. Preserve exact static evidence when runtime checks are unavailable.
5. Treat unrelated passing tests as insufficient proof.

From deepsec revalidation, applied to the patched code:

- Read the patched file fully, not just the changed lines, and the imports that matter (middleware, auth utilities, validation helpers, the framework's request pipeline).
- Trace the data flow end-to-end: where does the input enter, what transformations happen, where is it validated?
- Think like an attacker: try to construct a concrete attack against the patched code. If you can, the fix is incomplete.

## 2. Try to falsify the fix (boundary challenge)

From codex-security assess-patch-risk and its risk rubric. For each changed security boundary, record:

- the invariant that must hold;
- the strongest concrete counterexample (an attacker input that should be rejected);
- a legitimate control (an ordinary input from existing callers, tests, or docs that must still work);
- the patched source path for both cases; and
- whether the result is supported, contradicted, or unresolved.

Then check:

- Equivalent representations: other encodings (URL/percent, double encoding, Unicode, case, backslashes), alternate syntaxes (`a.b` vs `a[b]`, arrays vs strings), and type confusion (object or array where a string is expected).
- Sibling paths: every direct caller of each changed helper, other entry points that reach the same sink without passing through the changed code, and other same-family operations in the same module (for example the setter you fixed and the merge or parse function next to it).
- Validation-to-use gaps: values that are mutated, decoded, re-resolved, or re-parsed after your check and before the sensitive use. Reclassify redirects, callbacks, embedded URLs, and cached authority at the point of use.
- Both outcomes of every changed condition, including error branches and early returns.

## 3. Did the fix break legitimate behavior?

From codex-security assess-patch-risk:

- Describe the semantic change: changed behavior, defaults, errors, side effects, state, and contracts.
- Map program impact from source: trace changed symbols through direct callers to public entry points and package exports. Do not call code dead from text search alone.
- Distinguish what tests actually cover. Tests lower likelihood or raise confidence; they never prove a boundary that they do not exercise.
- Do not infer compatibility from a clean diff, individual green tests, or a small change.

## 4. Clean up and deliver

- `git status` and `git diff`: only the intended source changes remain; no scratch files, debug output, PoCs, new test files, or edits to existing tests.
- Rerun the narrowest build/syntax check after any revision.
- Deliver the patch as the task instructs.
