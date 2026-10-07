# XSS, output encoding, template and code injection, CSRF (CWE-79, 80, 94, 95, 116, 352, 1336)

**Tuple.** An attacker value reaches an output context (HTML, attribute, URL, script, style) or an evaluator
without the escaping that context needs.

## Complete fix
- Escape at the sink, for the exact context: HTML body, attribute, URL, JS string, CSS. Or sanitize with an
  allow-list sanitizer (DOMPurify, sanitize-html, bleach) placed between the data and the render.
- Stored data is still untrusted. Escape it when you render it.
- **URL attributes** (`href`, `src`, `action`): trim, decode, and compare case-insensitively. Then allow only
  expected schemes; reject `javascript:`, `data:`, and `vbscript:`.
- **JSON inside `<script>`:** escape `<`, `>`, `&`, and U+2028/U+2029, or deliver the data separately.
- **Templates:** never render user input as template source (for example `render_template_string(user)`).
  Explicit unescape forms (`mark_safe`, `|safe`, `raw`, `html_safe`, `<%==`, `dangerouslySetInnerHTML`,
  `v-html`, `innerHTML`) are the usual sinks.
- **Code evaluation:** remove `eval`, `new Function`, `vm`, and `exec` of user data, or replace them with a
  parser that has an allow-list.
- **Encoders:** use the platform's encoder (`html.escape`, `escapeHtml`, `template/html`, `OWASP Encoder`)
  rather than hand-written replacements.
- **CSRF:** state-changing requests need a CSRF token, an origin check, or a strict `SameSite` cookie. Do not
  allow state changes through GET.

## Incomplete fixes
- Escaping only `<` and `>` inside attributes.
- Escaping once, followed by a later decode.
- Fixing one renderer while other views, error pages, or alternate formats reuse the raw value.
