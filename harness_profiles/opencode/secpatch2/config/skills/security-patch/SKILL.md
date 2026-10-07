---
name: security-patch
description: Workflow for fixing a described security vulnerability or weakness (CVE, CWE, advisory, audit finding, or threat description) in a code repository. Locate every affected path, fix at the right boundary, prove the fix with a variant-driven check, and review the diff.
---

<!-- Adapted from codex-security (Apache-2.0, Copyright 2025 OpenAI) and deepsec (Apache-2.0, Copyright 2026
Vercel, Inc. and contributors); see the profile's NOTICE.md for sources and changes. -->

# Security patch workflow

Keep every step short and driven by tool calls. Track the lists below with `todowrite`.

## Priorities, in order
1. The described attack is blocked on every path and for every variant, including the default and
   unconfigured setup. Fail closed. Behavior the task calls vulnerable is not legitimate, even if a default
   or an existing test relies on it.
2. Legitimate behavior keeps working, including safe alternatives the code already offers.
3. Repository conventions. The smallest change that achieves 1 and 2.

## 1. Map the description to code
- Take each concrete phrase (component, input, option, protocol, file type, consequence) and grep for it.
- Write the tuple: attacker-controlled source, the missing or wrong control, the dangerous sink and its impact.
- List candidate sinks. If the obvious sink already handles the classic attack, the weakness is elsewhere, or
  it bypasses that guard. Local changelogs and version notes can reveal an earlier, incomplete fix.
- Write down every invariant or guarantee the task states. Each must hold after your fix.

## 2. Inventory every path (todowrite)
List every way input reaches the sink or the protected action:
- public entry points and wrappers, high- and low-level APIs;
- fallback, owner, admin, and self branches;
- modes, backends, and storage variants;
- sibling operations of the same family (read/write/delete, the load and parse variants);
- input that arrives through paths, headers, configuration, or stored state.

A shared check protects only the callers that actually call it.

## 3. Variant matrix (todowrite)
From the task's wording and the class file for this weakness, list inputs that must be blocked and inputs that
must keep working:
- type and shape: missing, null, empty, wrong type, list vs string;
- case, whitespace, and encodings: percent, double, unicode, backslash, trailing dot;
- aliases and equivalent forms;
- what the consuming component does with the value (browser, shell, filesystem, parser, resolver);
- boundary sizes.

## 4. Fix
- Fix at the shared primitive or where untrusted input enters. Also guard the dangerous operation if other paths
  reach it. Validate after decoding and normalizing, just before use.
- Reject unsafe input explicitly, in the error style the surrounding code already uses. Do not silently truncate
  or reinterpret it.
- Prefer helpers and APIs already used nearby. Add no dependencies.
- If a feature is inherently dangerous, make it opt-in instead of relying on callers to configure a filter.

## 5. Prove it
- Write a small table-driven check in `/tmp` that calls the real entry points, not a reimplementation. Every path
  and variant from steps 2–3 must be blocked; legitimate controls taken from existing tests or docs must still
  pass. Assert the described impact itself, under the default configuration.
- Run the narrowest build or type check, then the nearest existing tests with the project's own runner and
  environment (virtualenv, node_modules, module cache). Missing tooling is not evidence that the fix is wrong:
  trace the code instead, and timebox setup to a few minutes.

## 6. Existing tests that now fail
A newly failing test is a regression: narrow the fix (add a boundary instead of removing a feature). The
exception is a test whose input is the attack itself. Never edit, delete, or skip tests to make them pass.

## 7. Review the diff (once it exists)
Read `git diff` as a reviewer who knows only the task:
- Re-walk the path inventory and variant matrix against the patched code. Try one more variant and one more path.
- Check both outcomes of every changed condition, every caller of a changed helper, and every error path.
- Look for later bypasses: values re-read, re-decoded, or re-resolved after validation; cached or derived
  authority; redirects and callbacks.
- Fix confirmed problems only, then repeat step 5.

## 8. Finish
Remove scratch files and debug output from the repository, and do not `git add`. If the task asks for a patch or
diff file, regenerate it as your last action. Summarize the paths and variants covered, the checks run, and
anything left unverified.

## Class guidance
Read only the file for the weakness you are fixing (paths are relative to this skill):
- `classes/path-file.md`: path traversal, links, archives, uploads (CWE-22, 23, 36, 59, 73, 434)
- `classes/injection.md`: command, argument, SQL, NoSQL, LDAP, XPath, header/CRLF, expression (CWE-77, 78, 88, 89, 90, 93, 113, 643, 917, 943)
- `classes/web-output.md`: XSS, output encoding, template and code injection, CSRF (CWE-79, 80, 94, 95, 116, 352, 1336)
- `classes/ssrf-redirect.md`: SSRF, open redirect, external references, cross-origin data (CWE-200, 441, 601, 610, 918)
- `classes/deserialization.md`: unsafe deserialization, reflection, dynamic code loading (CWE-470, 502, 913)
- `classes/xml.md`: XXE, entity expansion (CWE-611, 776, 827)
- `classes/access-control.md`: authorization, IDOR, missing authentication, privilege (CWE-269, 284, 285, 306, 639, 862, 863)
- `classes/authn-session.md`: authentication, sessions, tokens, brute force (CWE-287, 290, 294, 307, 384, 613, 620, 640)
- `classes/crypto-integrity.md`: TLS and certificates, weak crypto or randomness, signatures, timing (CWE-208, 295, 297, 327, 330, 345, 347)
- `classes/resource-dos.md`: limits, ReDoS, recursion, decompression (CWE-400, 409, 674, 770, 789, 834, 1333)
- `classes/memory-integer.md`: bounds, integer overflow, NULL, use-after-free (CWE-119, 125, 129, 131, 190, 191, 415, 416, 476, 787)
- `classes/defaults-errors.md`: insecure defaults, failed protection, error handling, validation (CWE-20, 703, 754, 755, 1188, 693)
- `classes/logging-exposure.md`: log injection, secrets in logs or errors, cleartext (CWE-117, 200, 209, 312, 319, 532)
- `classes/object-pollution.md`: prototype pollution, mass assignment (CWE-915, 1321)
- `classes/concurrency.md`: races, TOCTOU (CWE-362, 366, 367)
