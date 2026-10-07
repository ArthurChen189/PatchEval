# Authentication, sessions, tokens, brute force (CWE-287, 290, 294, 307, 384, 613, 620, 640)

**Tuple.** Attacker-controlled credentials, tokens, assertions, or protocol state are accepted without the
binding or verification that the trust decision requires.

## Complete fix
- **Tokens and assertions** (JWT, SAML, OAuth/OIDC): verify the signature with a pinned algorithm list and the
  expected key, then validate issuer, audience, expiry, not-before, and nonce or state.
  - Never accept `alg: none`, unsigned tokens, or algorithm confusion between HMAC and asymmetric keys.
  - Use the same object you verified. Do not re-parse it or pick a different element afterwards.
- **Security options** such as allowed algorithms, audiences, or domains must be validated for type and shape. A
  missing, null, empty, or wrongly typed allow-list must fail closed, never mean "allow all".
- **Sessions:** on login, privilege change, or principal change, issue a new session id **and** start from a
  fresh session state, so no data from the previous principal carries over. Do this in every session store
  (memory, cookie, database, cache).
  - Invalidate the server-side session on logout, password change, and expiry.
- **Identity binding:** after a protocol transition (TLS upgrade, redirect, callback, bind or rebind), verify that
  the authenticated identity is the one the code consumes.
- **Spoofable sources:** client IP and forwarded headers are spoofable unless set by a trusted proxy. Never use
  them alone for authentication.
- **Password and recovery flows:** require the old password or a valid recovery token. Rate-limit or lock out
  repeated attempts. Tokens must be random, single-use, and expiring.

## Incomplete fixes
- Rotating the session id but keeping the old session object.
- Verifying one assertion in a list and consuming another.
- Checking the signature only when an optional option is set.
