# Codex CLI 0.155.0 (vendored)

PatchEval pins the Codex harness to this release so every run uses the same
agent, independent of the self-updating standalone install on the host.

- Upstream: [openai/codex `rust-v0.155.0`](https://github.com/openai/codex/releases/tag/rust-v0.155.0),
  licensed under Apache-2.0 (`LICENSE`, `NOTICE`, copied from that tag).
- Target: `x86_64-unknown-linux-musl`, static binary (`codex-cli 0.155.0`).
- `codex-x86_64-unknown-linux-musl.xz` is the release binary recompressed with
  `xz -9e` to stay under GitHub's 100 MB file limit. It is byte-identical after
  decompression to the official asset `codex-x86_64-unknown-linux-musl.zst`
  (asset sha256 `2a72d3352f5eb9a58a60269eb3338fc5a8bdb8805e3dec2dfaa94f4569941613`).
- `SHA256SUMS` lists the compressed file and the decompressed binary.

The archive is stored with Git LFS. Generation extracts the binary next to the archive on first use and verifies
both checksums; the extracted file is git-ignored. To extract it manually:

```bash
cd third_party/codex/0.155.0
xz -dkc codex-x86_64-unknown-linux-musl.xz > codex-x86_64-unknown-linux-musl
chmod +x codex-x86_64-unknown-linux-musl
sha256sum -c SHA256SUMS
```

The `codex` harness defaults to this binary (`scripts/conf/harness/codex.yaml`)
and refuses to generate if the configured binary reports a different version.
