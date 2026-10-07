# Path traversal, links, archives, uploads (CWE-22, 23, 36, 59, 73, 434)

**Tuple.** An attacker-controlled name or path reaches a file operation without being contained to the
intended root.

## Complete fix
- Decode first, then resolve to a canonical absolute path. Examples: `path.resolve`/`fs.realpath`,
  `os.path.realpath`, `filepath.Clean` with `filepath.Rel`/`IsLocal`, `Path.toRealPath`, `std::fs::canonicalize`.
- Require the result to equal the root or start with `root + separator`. A bare prefix check accepts sibling
  directories such as `/data-evil` for the root `/data`.
- Reject absolute paths, `..` segments, NUL bytes, and drive letters or backslashes where Windows semantics apply.
- Re-check after following symlinks when the attacker can create files. Open with no-follow flags where they
  exist.
- **Archives** (zip, tar, and similar): validate each member name, and reject symlink and hardlink members,
  **before** writing anything. Later filtering or post-extraction scans are too late.
- **Uploads:** generate the stored name or sanitize it to a basename, check type and size on the server side,
  and store outside served or executable directories.
- **URL-derived paths:** query strings, fragments, and percent-encoded parts are attacker data. Apply the same
  containment to them.

## Incomplete fixes
- Removing `../` once: fails on `....//` and on decoded or double-encoded input.
- Checking the raw string but using the normalized path, or the other way round.
- Fixing one handler while sibling read, write, delete, list, download, or static-file routes keep the old logic.

## Typical safe and unsafe shapes
- `res.sendFile(req.params.x)` and `send_from_directory(dir, request.args[...])` without a basename or
  containment check are unsafe.
- `static_file(name, root)` and `send_file(params[:f])` are unsafe without containment.
