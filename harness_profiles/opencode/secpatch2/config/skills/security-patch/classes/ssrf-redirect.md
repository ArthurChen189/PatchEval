# SSRF, open redirect, external references, cross-origin data (CWE-200, 441, 601, 610, 918)

**Tuple.** An attacker-influenced URL, host, or redirect target makes the server fetch an internal resource,
or sends a user (or the user's credentials) to an attacker's origin.

## Parse and compare like the consumer does
- Parse with the platform's URL parser. Compare the parsed scheme, host, and port, never a string prefix or a
  loose regex (`.+`/`.*`).
- **Browsers** strip TAB, CR, and LF, treat `\` like `/`, resolve `.` and `..` segments, and treat `//host` and
  `/\host` as other origins. Treat all of these, plus userinfo (`a@b`) and leading whitespace or control
  characters, as external. When the code cannot decide, reject.
- **Hosts:** canonicalize case and the trailing dot. Loopback has many spellings (`localhost`, `*.localhost`,
  `localhost.localdomain`, `127.0.0.0/8`, `0.0.0.0`, `::1`, IPv4-mapped, decimal or hex). Also block
  link-local, metadata, and private ranges. Check the resolved IP when the threat is internal access, and grep
  the repository for hostnames it documents as local.

## SSRF
- Prefer an allow-list of destinations. If redirects are followed, validate **every** hop. A filter that is
  optional, empty by default, or applied only to the first request is not a fix.
- If the feature is inherently risky (fetching arbitrary URLs, routing to arbitrary names), make it opt-in.

## Open redirect
- Allow only relative paths that start with a single `/` followed by a non-slash, non-backslash character, or
  absolute URLs whose origin is on the allow-list.
- Check the value after normalization. Validate every parameter or header that feeds the redirect.

## Credentials across origins
- When a redirect or request changes scheme, host, or port, drop `Authorization`, `Cookie`,
  `Proxy-Authorization`, and similar headers, and do not downgrade https to http.
- The default configuration must be safe without the caller opting in.
