# Insecure defaults, failed protection, error handling, input validation (CWE-20, 693, 703, 754, 755, 1188)

**Tuple.** A protection is off, bypassable, or skipped under the default configuration, on an exceptional path,
or for input shapes the validator did not expect.

## Complete fix
- **Safe defaults:** the default and unset configuration must be secure. Security features default on, and
  dangerous ones default off and become explicit opt-ins. A missing, empty, or invalid security setting fails
  closed, never "allow all" or "disabled".
- **Validate the type and shape** of options and inputs (scalar vs list vs object, null, empty, out of range)
  before using them in a security decision.
- **Error handling:** when a check throws or returns an error, deny. Look for code that catches the exception and
  continues, or a `default:` or `else` branch that allows. Make sure execution cannot fall through to the
  protected action after a failed check.
- **Protection mechanisms:** if the control can be switched off by a header, parameter, environment variable, or
  debug flag reachable by an attacker, remove that switch or restrict it to trusted configuration.
- **Input validation:** validate at the boundary where untrusted input enters, after decoding, with allow-lists
  (format, range, length, charset). Keep server-side checks even if the client validates.
- **Legitimate behavior:** if existing users rely on the insecure default, keep a safe, explicit way to opt into
  the old behavior. Do not keep it as the default.

## Incomplete fixes
- Adding a secure option but leaving the insecure default.
- Validating only the documented shape of an option.
- Failing open when a dependency, configuration, or verification step is unavailable.
