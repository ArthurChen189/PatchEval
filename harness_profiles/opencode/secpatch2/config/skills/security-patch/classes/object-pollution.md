# Prototype pollution and mass assignment (CWE-915, 1321)

**Tuple.** Attacker-controlled keys or fields are written into objects or models without an allow-list, which
changes prototypes, defaults, or protected attributes.

## Prototype pollution (JS/TS)
- In every function that writes nested keys (deep merge, extend, set-by-path, clone, query or body parsers,
  config loaders): reject `__proto__`, `constructor`, and `prototype` at **every** path segment. Check after
  splitting `a.b.c`, `a[b]`, and array paths, and after decoding.
- Alternatively, write into `Object.create(null)` or a `Map`, and check own properties with
  `Object.prototype.hasOwnProperty.call`.
- Fix all exported entry points that reach the writer, including string paths, array paths, and the alternate
  API names of the same helper.
- Merges where later keys win: spreading attacker input **after** safe defaults (`{role: 'user', ...input}`)
  lets the input override them. Put safe fields last, or use an allow-list.

## Mass assignment
- Bind only allow-listed fields: strong params `permit(...)`, explicit DTOs, form `fields = [...]` (not
  `__all__`), serializer read-only fields, `@JsonIgnore` or explicit setters.
- Security fields (role, admin, owner, tenant, verified, balance) are never bindable from the request.

## Incomplete fixes
- Blocking only `__proto__` but not `constructor.prototype`.
- Checking the first path segment only.
- Fixing `set` but not `merge`, or the reverse.
