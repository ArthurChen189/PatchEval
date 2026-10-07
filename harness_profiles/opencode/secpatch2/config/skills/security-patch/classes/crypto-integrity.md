# TLS and certificates, weak crypto or randomness, signatures, timing (CWE-208, 295, 297, 327, 330, 345, 347)

**Tuple.** Data, a peer, or an update is trusted without a sound cryptographic check, or a secret or
comparison leaks through weak primitives or timing.

## Complete fix
- **TLS:** keep certificate and hostname verification on by default (`verify=True`, `InsecureSkipVerify=false`,
  no trust-all `TrustManager`/`HostnameVerifier`, `rejectUnauthorized: true`). Insecure modes must be explicit
  opt-ins, never the fallback when configuration is missing.
- **Signatures and MACs:** verify before using or parsing the data, and over the exact bytes you consume. Pin
  the algorithm and key. Check the result. Compare MACs, tokens, and passwords in constant time
  (`hmac.compare_digest`, `crypto.timingSafeEqual`, `subtle.ConstantTimeCompare`, `MessageDigest.isEqual`).
- **Integrity of downloads and updates:** verify the checksum or signature before extracting, executing, or
  installing. Fail closed if verification is unavailable.
- **Randomness:** use a CSPRNG for tokens, ids, nonces, and keys (`secrets`, `crypto.randomBytes`,
  `crypto/rand`, `SecureRandom`, `OsRng`). Never `Math.random`, `random`, or a time-seeded PRNG.
- **Algorithms:**
  - no MD5 or SHA-1 for security decisions;
  - no ECB, static IVs, or reused nonces;
  - passwords through bcrypt, scrypt, argon2, or PBKDF2 with a salt;
  - keys from configuration or a key store, not hard-coded.
- **Webhooks:** verify the signature on the raw body **before** parsing or acting, and reject stale timestamps
  to prevent replay.

## Incomplete fixes
- Logging a verification failure and continuing.
- Verifying one field while the code uses another.
- Turning verification on only when an option is present.
