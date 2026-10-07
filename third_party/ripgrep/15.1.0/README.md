# ripgrep 15.1.0 (vendored)

OpenCode's `grep`, `glob`, and `skill` tools run ripgrep. OpenCode 1.18.31 looks for `rg` on `PATH`, then in its
cache directory, and otherwise downloads exactly this release from GitHub. Agent containers are offline, so that
download fails and every call to these tools errors with "ripgrep execution failed". PatchEval therefore mounts this
binary read-only at `/usr/local/bin/rg` in OpenCode agent containers (`harness.ripgrep` in
`scripts/conf/harness/opencode.yaml`; `RIPGREP_BIN` for `run_infer.sh`).

- **Upstream:** [BurntSushi/ripgrep `15.1.0`](https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0). It is
  dual-licensed under MIT or the Unlicense (`LICENSE-MIT`, `UNLICENSE`, `COPYING`, copied from the release archive).
- **Target:** Linux x86-64, statically linked (musl, static-pie). Reports `ripgrep 15.1.0 (rev af60c2de9d)`.
- **Source asset:** `rg.xz` is the `rg` executable from the official asset
  `ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz`, the same asset OpenCode downloads when online. The asset's
  sha256 is `1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599`, matching the release's `.sha256`
  file.
- **Recompression:** the binary was recompressed with `xz -9e` and is byte-identical after decompression.
- **Checksums:** `SHA256SUMS` lists both the compressed file and the decompressed binary.

Like the other vendored archives here, `rg.xz` is a plain git blob. Generation extracts the binary next to the
archive on first use (`ensure_vendored_binary` in `scripts/run.py`) and verifies both checksums. The extracted file
is git-ignored. To extract it manually:

```bash
cd third_party/ripgrep/15.1.0
xz -dkc rg.xz > rg
chmod +x rg
sha256sum -c SHA256SUMS
```
