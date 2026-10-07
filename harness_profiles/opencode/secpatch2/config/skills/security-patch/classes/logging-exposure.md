# Log injection, secrets in logs or errors, cleartext (CWE-117, 200, 209, 312, 319, 532)

**Tuple.** Attacker data forges log entries, or sensitive data (credentials, tokens, keys, personal data,
internals) reaches logs, error responses, other origins, or storage and transport in cleartext.

## Complete fix
- **Log injection:** encode or strip CR, LF, and other control characters in values logged from requests.
  Prefer structured logging with fields over string concatenation.
- **Secrets in logs:** never log passwords, tokens, cookies, `Authorization` headers, keys, or full request
  bodies and headers. Redact at the logging helper so every caller is covered, including error and debug
  paths and exception messages.
- **Error responses:** return generic messages to clients. Keep stack traces, SQL, paths, versions, and internal
  hostnames in server logs only. Check every error handler and error status.
- **Exposure to the wrong party:** make sure responses, redirects, caches, and exports include only data the
  requester may see (see `access-control.md`). When a request crosses origins, drop credentials (see
  `ssrf-redirect.md`).
- **Cleartext:** use TLS for remote connections by default, and do not silently fall back to plaintext. Store
  secrets hashed (passwords) or encrypted with managed keys, and keep them out of URLs.

## Incomplete fixes
- Redacting one log statement while sibling statements (or a debug mode) still log the raw value.
- Sanitizing only `\n` but not `\r` or other control characters.
