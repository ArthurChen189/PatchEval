# Resource limits, ReDoS, recursion, decompression (CWE-400, 409, 674, 770, 789, 834, 1333)

**Tuple.** Attacker-controlled size, count, depth, pattern, or input shape drives unbounded CPU, memory,
recursion, or allocation.

## Complete fix
- Enforce limits **before** allocating or iterating, and fail with an error: body size, field and array counts,
  nesting depth, string lengths, file counts, and decompressed size (check the ratio and total during
  decompression, not after). Keep existing option names if the code already has limits.
- **ReDoS:** remove ambiguity from the regex. Avoid nested quantifiers (`(a+)+`), overlapping alternations
  (`(a|ab)*`), and adjacent quantified groups that can match the same text (`\s*…\s*`, `.*.*`).
  - Alternatively, bound the input length before matching, or replace the regex with linear parsing.
  - Check every regex in the affected function and its siblings.
  - Escape user input that is placed into a regex.
- **Recursion:** add a depth limit, or convert to iteration, for parsers, merges, and graph walks over attacker
  data.
- **Allocation from attacker numbers:** validate the counts and lengths read from headers or file formats
  against the remaining input size and a maximum before allocating.
- **Loops:** make sure every loop over attacker structure makes progress and terminates on malformed input.
- **Expensive endpoints:** pagination caps, timeouts, and query complexity or depth limits (GraphQL).

## Incomplete fixes
- Limits applied after parsing everything.
- Limiting one parser while a sibling format or endpoint stays unbounded.
- A timeout around code that keeps consuming memory.
