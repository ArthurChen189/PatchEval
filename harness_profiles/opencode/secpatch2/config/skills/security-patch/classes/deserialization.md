# Unsafe deserialization, reflection, dynamic code loading (CWE-470, 502, 913)

**Tuple.** Attacker-controlled serialized data, type names, or code reaches a loader that constructs
arbitrary objects, resolves arbitrary classes, or executes code.

## Complete fix
- Use data-only formats and safe loaders:
  - Python: `json`, `yaml.safe_load`; never `pickle`/`marshal`/`shelve` or `yaml.load` without
    `SafeLoader` on untrusted data.
  - Ruby: `YAML.safe_load`.
  - PHP: `json_decode`; never `unserialize`, or only with `allowed_classes => false`.
  - .NET: no `BinaryFormatter`.
  - JS: no `node-serialize`.
- **Polymorphic deserializers** (Jackson default typing, `@JsonTypeInfo` with class names, XStream, Kryo, Hessian,
  SnakeYAML constructors, .NET `TypeNameHandling`): disable polymorphism, or use an explicit type allow-list
  enforced before any object is constructed.
- **Java:** use `ObjectInputFilter` with an allow-list, or avoid `ObjectInputStream` for untrusted input.
- **Reflection** (`Class.forName`, `getattr`/`importlib` with user names, `require(userPath)`, `constantize`):
  map the inputs to an allow-list of known classes, functions, or modules.
- **Templates, plugins, and expressions** that execute code need a sandbox or an allow-list, not a deny-list of
  dangerous names.
- **Shared wrappers:** fix the wrapper every caller uses. Check each registered codec or converter, because a
  safe default does not protect a second registered handler.

## Incomplete fixes
- Deny-listing a few gadget classes.
- Validating after deserialization.
- Fixing one entry point (an HTTP body) while other inputs (cookies, caches, message queues, file imports,
  RPC) still reach the unsafe loader.
