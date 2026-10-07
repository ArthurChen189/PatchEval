---
name: security-patch
description: Use for any task that asks you to fix, patch, or remediate a described security vulnerability (a CVE, CWE, advisory, or vulnerability description) in a code repository. Gives the end-to-end workflow - assess, locate, minimal boundary fix, sibling check, bounded validation, self-review.
---

<!--
Adapted from codex-security (https://github.com/openai/codex-security, Apache-2.0, Copyright 2025 OpenAI):
plugins/codex-security/skills/fix-finding/SKILL.md, references/static-finding-assessment.md, and
skills/validation/SKILL.md. Modified for described-vulnerability benchmarks run offline in OpenCode:
removed the Codex Security MCP artifact storage, scan workbench stages, `fork_turns` subagents, and the
`no_change`/`blocked` outcomes (the task guarantees the vulnerability exists); the investigator and reviewer
became inline passes; validation is limited to offline, timeboxed checks. See the profile's NOTICE.md.
-->

# Security Patch

Turn a described security vulnerability into a minimal, validated code change.

Judge the result in this order:

1. any fix completely closes the broken security boundary
2. legitimate behavior and compatibility are preserved
3. relevant repository checks pass
4. the implementation follows repository conventions
5. the patch contains only the scope necessary for the earlier properties

Never trade an earlier property for a later one. Minimal means the smallest repository-native change that satisfies all earlier properties, not the fewest lines.

The task states that the vulnerability exists in the checked-out code. If your first reading suggests the code is already safe, you have not found the vulnerable path yet: re-read the description, search for the named function, parameter, option, or file, and look for alternate entry points. Always finish with a patch.

## 1. Assess the described vulnerability

For the described claim, identify the smallest useful tuple:

- source: the attacker-controlled input, external trigger, or trusted operator input named by the claim
- control: the relevant guard, validator, sanitizer, authorization check, configuration gate, feature flag, or missing security control
- sink: the dangerous operation, vulnerable dependency, broken control, or impact point
- reachable path: the code/config path that connects source, control, and sink under stated preconditions
- boundary: the product surface and trust boundary that make the path security relevant

Inspect the smallest relevant evidence set before broadening:

1. Locations named by the description: package, module, function, parameter, option, endpoint, file format, or error message. Search for them with `grep -rn` / `glob`.
2. Affected functions, call sites, routes, RPC handlers, parser entrypoints, CLI commands, plugin hooks, message consumers, and package APIs.
3. Nearby guards, validators, sanitizers, authorization checks, feature flags, configuration checks, and compensating controls.
4. Existing tests, docs, and comments that clarify intended behavior.

Do not treat formatting, encoding, generic escaping, exception catching, redirecting, authentication alone, or a control's name as proof that the claimed consequence is either enabled or prevented. Determine what the operation enforces, what execution does afterward, and whether later processing changes the data's security meaning.

Then open `references/vuln-class-guidance.md` and read the section for this weakness class (CWE). It lists what a complete control looks like for that class and common incomplete fixes.

## 2. Patch contract

Before editing, inspect the affected implementation, its direct callers, nearby helpers, and relevant existing tests. Establish from repository evidence:

- the attacker-controlled input and concrete source-to-sink path or broken control
- the security invariant and narrowest shared enforcement boundary
- legitimate behavior, APIs, error semantics, and compatibility constraints that must remain
- the closest existing implementation, validation, and error-handling precedents

Treat the finding as a data-flow and boundary problem, not merely the named input example. Check equivalent encodings, parser forms, aliases, callers, sinks, and every representation or copy of security-sensitive state that could bypass the proposed change. Handle unsafe state explicitly; do not silently accept, truncate, or reinterpret it into another reachable form.

## 3. Investigation pass (separate perspective, before editing)

Take the perspective of a **security-boundary and compatibility investigator**: independently trace the source-to-sink path and identify the shared enforcement boundary, affected entry points, alternate representations or lifecycle states, parser and validation-to-use transitions, concrete sibling paths, and source-backed bypass risks. Establish the legitimate workflows and public behavior that must remain, then inspect callers, implementations, optional modes, errors, side effects, repository conventions, existing helpers, and focused validation commands for integration constraints.

Keep facts, inferences, and unresolved questions separate. Then choose the patch boundary.

When a dangerous sink has multiple call sites, enumerate each call site with its own source and closest control. When one route or helper exposes multiple same-family operations (for example `execute`/`executemany`, `pickle.load`/`yaml.load`, separate path/file helpers, or several unauthenticated actions), check each independently triggerable operation. A safe sibling does not make a vulnerable sibling safe.

## 4. Implementation workflow

1. Trace the reported path and inspect only the context needed to identify the real shared boundary.
2. When feasible, run the smallest high-signal reproduction through that boundary and one legitimate control through the same path (see Validation below).
3. Implement the smallest repository-native fix at the shared boundary. Prefer nearby helpers and established APIs. Do not broaden into unrelated redesign or cleanup.
4. Before verification, challenge the patch rather than defending it: inspect every direct caller of each changed helper and both outcomes of each changed condition. Look for one sibling path, representation, or copy that still reaches the vulnerable sink and one ordinary or default input that the patch newly rejects or reinterprets; revise the implementation if either exists.
5. Verify in order:
   - inspect the final diff and run the narrowest syntax, import, build, or type check relevant to it
   - rerun the security trigger or strongest focused substitute and review one alternate malicious input class
   - rerun the legitimate control and the nearest existing tests

## 5. Validation (bounded, offline)

Choose the strongest realistic method available, and stop escalating when setup becomes disproportionate:

- unit or integration test: if the vulnerable path is covered by an existing test harness, run the nearest existing tests; write any new focused test or PoC script under `/tmp`, not in the repository.
- realistic interface reproduction: if the code exposes a user-reachable interface such as HTTP, CLI, file parser, or package API, attempt a minimal reproduction through that interface using crafted input that reaches the sink (for example a short `node -e`, `python3 -c`, or `go test -run` invocation).
- crash: for crash, memory-corruption, parser-confusion, or denial-of-service issues in compiled code, build and run a small crashing input when the project builds with bounded effort.
- code understanding: if dynamic reproduction is not feasible or proportionate after bounded attempts, trace source, control, sink, and reachability statically.

Usage guidance:

- Prefer short, bounded commands (git, `grep -rnI` within the relevant dirs, build/test runners, minimal PoCs). Avoid interactive editors and long-running repository-wide scans.
- There is no network: do not `pip`/`npm`/`go` install or download anything. If a dependency is missing, use what is installed or fall back to static tracing.
- Do not treat setup errors, compilation errors, or missing dependencies as evidence that the fix is wrong or unnecessary. Record what blocked runtime proof and continue with static trace.
- Do not abandon a build or test command just because it takes time when it shows progress, but do not spend more than a few minutes on environment setup that is not working.
- If debuggers are needed, invoke them non-interactively (gdb: `-q -batch -ex run -ex bt -ex quit`).

## 6. Patch review pass (separate perspective, after implementing)

Re-read only the vulnerability description and your current diff (`git diff`), setting aside your own rationale, and work through `references/patch-review.md` as a **bypass and regression reviewer**: reconstruct the invariant and look for a concrete surviving route through affected entry points, equivalent representations, parser boundaries, aliases, backend or platform variants, and validation-to-use gaps. Trace changed conditions and direct callers for concrete breakage of legitimate inputs, public contracts, errors, side effects, state transitions, optional modes, compatibility, resource behavior, and repository conventions.

Treat review findings as hypotheses: confirm them against the source or focused execution before revising. Address only confirmed issues within the vulnerability and compatibility boundary. Then rerun relevant verification and ensure no temporary or unrelated changes remain (`git status`, `git diff`). Perform only one review cycle.

## 7. Finish

- Deliver the patch exactly as the task instructs (for example, write `git diff` to the requested patch file).
- The final diff must contain only the fix: no scratch files, debug prints, PoCs, or new test files, and no edits to existing tests.
- In the final message, state briefly: the vulnerable path and invariant, the fix and why it is complete, the checks run and their results, and any remaining uncertainty.

## Hard rules

- Do not rely only on code inspection when a focused test or reproducer is feasible offline.
- Do not broaden the patch into unrelated cleanup or architectural redesign without evidence that the broader change is required for complete closure.
- Do not remove user changes or unrelated local modifications.
- Do not weaken authentication, authorization, tenant isolation, input validation, sandboxing, or logging to make tests pass.
- Do not hide proof gaps. If the environment blocks validation, say exactly which command failed and what evidence is still missing.
