# Command, argument, query, and header injection (CWE-77, 78, 88, 89, 90, 93, 113, 643, 917, 943)

**Tuple.** Attacker bytes reach an interpreter as syntax (shell, SQL, query operator, header, expression)
rather than as data.

## Commands and arguments
- Avoid the shell. Call the program with an argument vector: `execFile`/`spawn([...])`,
  `subprocess.run([...], shell=False)`, `exec.Command(name, args...)`, `ProcessBuilder(list)`,
  `std::process::Command::arg`.
- **Argument injection:** a value that starts with `-` can become an option. Put `--` before positional values,
  or reject leading dashes.
- Quoting helpers are a last resort, and must match the exact shell and context.
- Check every place that builds the command, including other platforms and option forms.

## SQL, NoSQL, and other query languages
- Bind values as parameters (`?`, `$1`, `:name`, prepared statements, ORM filters). Concatenating before you bind
  is still injection.
- Identifiers (tables, columns, `ORDER BY`) cannot be bound. Map them through an allow-list.
- **NoSQL:** reject objects or operators where a scalar is expected, such as `{"$ne": …}` or `$where` strings.
- **LDAP and XPath:** escape for the filter or expression grammar, or use parameterized APIs.

## Headers, CRLF, and logs
- Reject or strip CR/LF in values that end up in headers, status lines, cookies, emails, or line-based
  protocols.
- Prefer the framework's header APIs, which validate values.

## Expression and template languages
- Never evaluate user-controlled expressions (SpEL, OGNL, EL, Jinja/Velocity templates, `eval`).
- If expressions are a feature, use a restricted evaluator or an allow-list of operations.

## Incomplete fixes
- A deny-list of metacharacters.
- Escaping for the wrong context.
- Fixing one query builder while its sibling operations (`execute`, `executemany`, raw helpers, alternative
  drivers) stay injectable.
