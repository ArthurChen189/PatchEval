# OpenCode 1.18.31 (vendored)

PatchEval pins the OpenCode harness to this release so every run uses the same
agent; the rendered configs (built-in agent names, temperature capability flag)
were validated against it.

- Upstream: [anomalyco/opencode `v1.18.31`](https://github.com/anomalyco/opencode/releases/tag/v1.18.31),
  licensed under MIT (`LICENSE`, copied from that tag).
- Target: Linux x86-64 (glibc, dynamically linked), reports `1.18.31`.
- `opencode-linux-x64.xz` is the `opencode` executable from the official asset
  `opencode-linux-x64.tar.gz` (asset sha256
  `e9312be75ed803b7415fc2aeabda1f4fe938912a39673762dc0c38c0e11ebde4`),
  recompressed with `xz -9e`; it is byte-identical after decompression.
- `SHA256SUMS` lists the compressed file and the decompressed binary.

The archive is stored with Git LFS. Generation extracts the binary next to the
archive on first use and verifies both checksums; the extracted file is
git-ignored. To extract it manually:

```bash
cd third_party/opencode/1.18.31
xz -dkc opencode-linux-x64.xz > opencode-linux-x64
chmod +x opencode-linux-x64
sha256sum -c SHA256SUMS
```

The `opencode` harness defaults to this binary (`scripts/conf/harness/opencode.yaml`)
and refuses to generate if the configured binary reports a different version.
