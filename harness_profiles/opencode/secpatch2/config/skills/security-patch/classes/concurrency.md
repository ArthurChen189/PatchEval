# Races and time-of-check/time-of-use (CWE-362, 366, 367)

**Tuple.** A check and the action it protects are separate steps on shared state (a file, a database row, a
balance, a session, a cache), so a concurrent change between them invalidates the check.

## Complete fix
- Make the check and the use one atomic step:
  - database transactions with the right isolation level, `SELECT … FOR UPDATE`, conditional updates
    (`UPDATE … WHERE balance >= x`), unique constraints;
  - compare-and-swap or atomic operations;
  - a lock that covers both steps.
- **Files:** operate on file descriptors, not paths you re-resolve. Open with `O_CREAT|O_EXCL` and `O_NOFOLLOW`,
  use `openat`-style APIs, and create temporary files with `mkstemp` or `tempfile`. Do not `stat` a path and
  then open it.
- **Idempotency** for single-use tokens, coupons, and invites: mark them used atomically as part of the action.
- **Shared mutable state** in servers (globals, caches, singletons) must be synchronized or kept per request.

## Incomplete fixes
- Adding a sleep or retry.
- Locking the check but not the update.
- Fixing one handler while another path performs the same read-then-write.
