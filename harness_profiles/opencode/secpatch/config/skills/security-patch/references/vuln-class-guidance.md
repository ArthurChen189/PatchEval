# Vulnerability-class guidance for patches

Read the section for the weakness you are fixing. Each section gives the proof tuple (what must be broken for the
bug to exist), what a **complete** control looks like, and common **incomplete** fixes.

Source labels: **[CS]** codex-security `skills/validation/references/validation-guidance.md` (Apache-2.0,
Copyright 2025 OpenAI); **[DS]** deepsec `packages/processor/src/prompt/slug-notes.ts`, `highlights.ts`, and scanner
matcher comments (Apache-2.0, Copyright 2026 Vercel, Inc. and contributors); **[profile]** notes added for this
profile. Quoted text is lightly edited for a fixing context (see NOTICE.md).

General rules for every class:

- [CS] Do not treat formatting, encoding, generic escaping, exception catching, redirecting, authentication alone, or a control's name as proof that the consequence is prevented. Determine what the operation enforces and whether later processing restores the dangerous meaning.
- [CS] When one route or helper exposes multiple same-family operations, fix or rule out each independently triggerable operation, not only one representative.
- [CS] Partial hardening or a safe sibling does not protect a different call, factory, converter, or handler.
- [profile] Put the check where all callers pass through it (the shared helper or the sink wrapper), validate after decoding/normalization and immediately before use, and reject unsafe input explicitly.

---

## Path traversal, file access, archive extraction (CWE-22, CWE-23, CWE-24, CWE-36, CWE-59, CWE-73)

- [CS] Proof tuple: attacker-controlled bytes + sanitizer/canonicalization/allowlist result + dangerous sink/context. Resource handlers: attacker-controlled URL/path/resource name + allowlist/path-matcher/decoder/canonicalizer + mismatch, pre-decode/post-decode gap, legacy handler behavior, or unsafe resolver fallback.
- [CS] Validate the exact allowlist, path matcher, URL decoding, canonicalization, and resource-selection control used by that handler. A newer safe handler does not protect a legacy or exported handler with a different control line.
- [CS] Archive extraction / restore / import: each untrusted member path must be normalized and contained **before** the extraction or write occurs. Later top-level filtering, manifest gates, or post-extraction scanning are not sufficient. Reason about symlink, hardlink, and metadata members.
- [DS] Flag if `path.join(root, userInput)` lacks a `path.resolve(...).startsWith(root)` containment check.
- [DS] Without an explicit canonical-path / realpath / startsWith(rootDir) guard, attackers can place a symlink at any path component to escape the intended root (CWE-22 + CWE-59).
- [DS] Without an explicit entry-path validation pass (`path.relative(dest, x).startsWith(..)`, realpath check, allowlist regex), a malicious archive can write outside the destination via `..` segments, absolute paths, or symlinks ("Zip Slip" / "Tar Slip").
- [DS] `express.static` on a user-influenced root, or `res.sendFile(req.params.x)`, is path traversal; `send_from_directory(dir, request.args['file'])` without a basename check, `static_file(filename, root)` without `path.basename(filename)`, and `send_file(params[:f])` without containment are path traversal.
- [profile] Complete fix: decode first, then resolve to an absolute canonical path (`path.resolve`/`realpath`, `os.path.realpath`, `filepath.Clean` + `filepath.Rel`/`filepath.IsLocal`), then require the result to equal the root or start with `root + separator` (a bare prefix check lets `/data-evil` pass for root `/data`). Reject absolute inputs, `..` segments, NUL bytes, and Windows drive/backslash forms when the code can run on Windows. Re-check after following symlinks when the attacker can create files.

## Command and argument injection (CWE-77, CWE-78, CWE-88)

- [DS] Distinguish dynamic command (string concat → exec) from static command with sanitized args (which is fine).
- [DS] `exec.Command("sh", "-c", interpolated)` is the bug; `exec.Command("cmd", arg1, arg2)` with discrete args is generally safe.
- [CS] For command/action runners, check each argument type and execution mode independently; treat API/webhook-supplied values as attacker-controlled even when a UI would normally constrain them.
- [profile] Complete fix: avoid the shell (`execFile`/`spawn` with an argument array, `subprocess.run([...], shell=False)`, `exec.Command(name, args...)`). If an argument starts with `-`, insert `--` or reject it (argument injection, CWE-88). Quoting helpers are a last resort; escape for the exact shell and context. Check every code path that builds the command, including alternate platforms and option forms.

## SQL, NoSQL, and query injection (CWE-89, CWE-943, CWE-90, CWE-643)

- [CS] Proof tuple: attacker-controlled bytes + query/selector/parser API that receives syntax or operators rather than bound values + semantic change.
- [DS] Raw SQL: string concat / template literal / f-string / `%` / `.format()` / `fmt.Sprintf` into a query is injection. Safe shapes: `$1` / `?` / `:name` placeholders with separate args (pg, mysql2, psycopg `execute("... %s ...", (val,))`, SQLAlchemy `text(...).bindparams`, `db.Query("... $1 ...", val)`, GORM `Where("col = ?", val)`), ORM `where({col: x})`. MyBatis `${param}` is unsafe; `#{param}` parameterizes. Concatenation before `PreparedStatement` binding is still injection.
- [DS] MongoDB: `find(JSON.parse(req.body...))` accepts attacker operator keys (`$ne`, `$gt`) — coerce to typed query first. `$where` with concatenated strings runs JS in the database.
- [profile] Complete fix: bind values as parameters. Identifiers (table/column/order-by) cannot be bound — map them through an allowlist. For NoSQL, reject objects/operators where a scalar is expected (`typeof x === 'string'`).

## Cross-site scripting and template injection (CWE-79, CWE-80, CWE-94, CWE-95, CWE-116, CWE-1336)

- [CS] Proof tuple: attacker-controlled value + escaping/template context + browser/server-side template execution sink. Recursive placeholder/template expansion of resolved values is a separate injection path.
- [DS] Check escape state at every step; raw concat into HTML, JSON-in-script without `</`-escape, and `innerHTML` are the usual sinks. DB-stored HTML is still untrusted — a sanitizer (DOMPurify, sanitize-html) must sit between the data and the render.
- [DS] `mark_safe()` / `format_html()` on user input, `{% autoescape off %}`, Rails `raw(x)` / `html_safe` / `<%== %>` are XSS; bare ERB `<%= %>` auto-escapes. `render_template_string(user_input)` is server-side template injection (RCE).
- [profile] Complete fix: escape for the exact output context (HTML body, attribute, URL, JS string, CSS) at the sink, or sanitize with an allowlist sanitizer. For URLs in `href`/`src`, also reject `javascript:`, `data:`, and `vbscript:` schemes after trimming and decoding. Never evaluate user input as a template or code (`eval`, `new Function`, `vm`, `exec`); for expression evaluators, use a restricted parser or allowlist.

## SSRF and open redirect (CWE-918, CWE-601)

- [CS] SSRF proof tuple: attacker-controlled destination + destination control bypass + network/read/side-effect impact. Filters that are optional, empty by default, regex-only, applied only before the request, or bypassed by redirects are partial controls.
- [DS] Check whether the URL host is constrained to an allowlist, blocked from RFC1918, or proxied via a vetted URL parser.
- [DS] A server-side fetch of a caller-influenced URL must EITHER set `redirect: 'manual'` and re-validate the resolved Location, OR use a fetch wrapper that re-validates per hop, OR block fetches to private IP ranges entirely (DNS resolution check).
- [DS] Regex-based URL/hostname validation with `.+` or `.*` patterns is bypassable.
- [DS] Open redirect: require an allowlist, origin check, or hash-only redirect; relative paths starting with `//` are still external. Verify the validator can't be bypassed via encoding.
- [profile] Complete fix: parse with the platform URL parser, then compare the parsed scheme and host (not a string prefix). Treat `\`, `/\`, `//`, `%2f`, userinfo (`a@b`), and leading whitespace/control characters as external. Resolve DNS and block loopback, link-local (169.254/16), private, and IPv6-mapped forms when the threat is internal access.

## Prototype pollution and mass assignment (CWE-1321, CWE-915)

- [DS] User-controlled keys into `obj[x] = v` without an allowlist enable prototype pollution / overwriting safe defaults.
- [DS] Object spread precedence: later keys win. `{role: 'user', ...userInput}` is the bug; `{...userInput, role: 'user'}` is the safe order.
- [DS] `ModelForm` with `__all__`, or `params[:x]` directly into `Model.update(...)`, exposes mass assignment; Rails strong params `permit(...)` is the guard. `koa-bodyparser` prototype-pollution options must be set explicitly.
- [profile] Complete fix for deep merge / set-by-path / query parsers: reject the keys `__proto__`, `constructor`, and `prototype` at **every** path segment (including keys produced after splitting `a.b.c` or `a[b]` syntax and after decoding), or use `Object.create(null)` / `Map` and own-property checks (`Object.prototype.hasOwnProperty.call`). Check every entry point (merge, set, assign, parse) that writes nested keys, and both string paths and array paths.

## Resource exhaustion and ReDoS (CWE-400, CWE-770, CWE-674, CWE-1333, CWE-834, CWE-409)

- [CS] Parser/file-format DoS proof tuple: untrusted structure + helper that performs unchecked cast, size-based allocation, recursive traversal, numeric conversion, or loop over attacker-controlled structure + crash or denial-of-service impact.
- [DS] `new RegExp(req.*)` and `$regex` with user input are ReDoS-shaped. Body size limits (`content_length_limit`), query depth/complexity limits, and bounds on repeated/bytes fields stop abuse; their absence is the bug.
- [profile] Complete ReDoS fix: remove ambiguity in the regex (no nested quantifiers like `(a+)+`, no overlapping alternations like `(a|a)*`, no `\s*` adjacent to another `\s*`/`.*` that can match the same characters), or bound the input length before matching, or replace the regex with linear parsing. Check every regex in the affected function and its siblings, not only the one named. Escape user input placed into a regex (`escapeRegExp`).
- [profile] Complete resource fix: enforce limits on size, count, depth, and recursion **before** allocation or iteration; fail closed with an error; keep limits configurable when the code already has options.

## Unsafe deserialization and code loading (CWE-502, CWE-470, CWE-913)

- [CS] Proof tuple: attacker-controlled serialized/code/template bytes + unsafe loader/evaluator + execution or object-construction effect. Check the concrete codec, converter, or deserializer that resolves types or constructs objects; one hardened parser does not protect another.
- [DS] `pickle` is unsafe deserialization (RCE); `yaml.load` without a safe loader constructs objects; Erlang `binary_to_term/1` is unsafe — use `binary_to_term/2` with `[safe]`.
- [profile] Complete fix: use safe loaders (`yaml.safe_load`, `json`), type allowlists for polymorphic deserializers, and never pass untrusted data to `eval`, `Function`, `pickle`, `marshal`, `node-serialize`, or dynamic `require`/`import`.

## XML external entities (CWE-611, CWE-776, CWE-827)

- [CS] Proof tuple: attacker-controlled XML/SVG/XSLT input + parser factory, converter, transformer, or resolver setup + fail-open feature configuration, missing entity/DTD controls, or secure-processing-only hardening + XXE, SSRF, file read, or denial-of-service impact.
- [CS] Hardening must fail closed on the exact parser instance. `FEATURE_SECURE_PROCESSING` by itself, swallowed `setFeature` failures, or caller-supplied parser factories leave the bug alive.
- [profile] Complete fix: disable DTDs and external entities on every parser instance (for example `disallow-doctype-decl`, `external-general-entities=false`, `resolve_entities=False`, `no_network=True`, `defusedxml`), and bound entity expansion.

## Authorization and access control (CWE-284, CWE-285, CWE-639, CWE-862, CWE-863, CWE-269)

- [CS] Proof tuple: attacker path + missing/wrong guard + protected object/comparison/state transition. Name the exact permission, authentication, tenant/object, or state-transition check on that endpoint.
- [CS] For self-service updates, compare the request object and persisted object field by field for security-sensitive identity, role/group, tenant, and recovery properties; an alias that is still writable bypasses the check.
- [DS] User-supplied teamId/userId in DB queries — the authenticated identity must be used for the ownership check, not the request param alone. Look for inverted booleans, early returns that skip checks, and environment-variable switches that skip auth.
- [DS] Only middleware that wraps the handler directly counts (Express middleware, NestJS guards, Spring filters, Rails `before_action`, Django decorators, FastAPI `Depends`).
- [profile] Complete fix: enforce the check server-side at the handler or shared service function for every action (read, create, update, delete, export) and every route alias; deny by default.

## Authentication, sessions, tokens, signatures, cryptography (CWE-287, CWE-290, CWE-294, CWE-345, CWE-347, CWE-352, CWE-384, CWE-613, CWE-208, CWE-327, CWE-330)

- [CS] Proof tuple: attacker-controlled token, assertion, protocol metadata, or version value + exact validator semantics + mismatch between validated and trusted value, incomplete canonicalization/equality, unchecked parsing, or missing binding. For stateful protocols, check the transition from pre-auth to credentialed identity (rebind, reauthentication, issuer/callback binding, validated object vs consumed object).
- [DS] JWT: look for `algorithm: 'none'`, missing `algorithms: ['HS256']` pinning, or skipping `verify()` in dev branches. Webhooks: signature verification must happen BEFORE the body is parsed/processed.
- [profile] Complete fix: verify signatures over the exact bytes that are used, pin algorithms, compare secrets with constant-time functions (`crypto.timingSafeEqual`, `hmac.compare_digest`, `subtle.ConstantTimeCompare`), use a CSPRNG for tokens, regenerate session IDs on login, and require CSRF tokens or SameSite cookies on state-changing requests.

## Race conditions (CWE-362, CWE-367)

- [DS] Read-then-write patterns without a lock / transaction / atomic operation are TOCTOU when the resource is shared across requests.
- [profile] Complete fix: make the check and the use one atomic step (lock, transaction, compare-and-swap, `O_EXCL`/`O_NOFOLLOW` opens on file descriptors rather than re-resolving paths).

## Input validation and other weaknesses (CWE-20, CWE-129, CWE-190, CWE-476, CWE-125, CWE-787)

- [CS] Deterministic parser/helper failures from untrusted input (exception, unchecked cast, unchecked numeric parse, recursion, allocation, loop) are security-relevant when a repeated trigger can abort request processing or service availability.
- [profile] Complete fix: validate type, range, length, and format at the boundary where untrusted input enters the shared code path; check index and length before use; check integer arithmetic for overflow before allocation; check every error branch that could leave a pointer or value unset before it is used.
