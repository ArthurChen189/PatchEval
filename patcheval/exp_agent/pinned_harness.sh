# Pinned harness binaries shared by the Codex and OpenCode adapters. Source this
# file from an agent adapter; it only defines functions.
#
# Each adapter pins a release vendored under third_party/ (an .xz archive plus
# SHA256SUMS). With <PREFIX>_BIN unset the vendored binary is used, extracted on
# first use; a vendored binary is checksum-verified on every run. Before any case
# starts, `--version` must report <PREFIX>_VERSION, which defaults to the pin.
# Set <PREFIX>_VERSION to another release when overriding <PREFIX>_BIN, or to an
# empty string to skip the version check.

PINNED_HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Succeeds when FILE matches NAME's digest in SUMS_FILE.
_pinned_sha256_matches() {
  local sums_file="$1" name="$2" file="$3" want have
  want="$(awk -v name="$name" '$2 == name {print $1}' "$sums_file")"
  have="$(sha256sum "$file" | cut -d' ' -f1)"
  [[ -n "$want" && "$want" == "$have" ]]
}

# pinned_harness_resolve VAR VENDORED_PATH
# Defaults VAR to the repository-relative VENDORED_PATH and, when VAR names the
# vendored binary, extracts and verifies it. Leaves VAR as an absolute path.
pinned_harness_resolve() {
  local var="$1" vendored="${PINNED_HARNESS_ROOT}/$2"
  local binary dir name partial
  binary="$(realpath -m "${!var:-$vendored}")"
  if [[ "$binary" == "$vendored" && -f "${vendored}.xz" ]]; then
    dir="$(dirname "$vendored")"
    name="$(basename "$vendored")"
    if [[ ! -e "$vendored" ]]; then
      if ! _pinned_sha256_matches "${dir}/SHA256SUMS" "${name}.xz" "${vendored}.xz"; then
        echo "Checksum mismatch for ${vendored}.xz" >&2
        exit 1
      fi
      # A unique name keeps concurrent first runs from sharing a partial file.
      partial="$(mktemp "${dir}/.${name}.XXXXXX.partial")"
      xz -dc "${vendored}.xz" > "$partial"
      if ! _pinned_sha256_matches "${dir}/SHA256SUMS" "$name" "$partial"; then
        rm -f "$partial"
        echo "Checksum mismatch after extracting ${vendored}.xz" >&2
        exit 1
      fi
      chmod 755 "$partial"
      mv -f "$partial" "$vendored"
      echo "Extracted vendored harness binary: $vendored" >&2
    elif ! _pinned_sha256_matches "${dir}/SHA256SUMS" "$name" "$vendored"; then
      echo "Checksum mismatch for $vendored; delete it to re-extract ${name}.xz" >&2
      exit 1
    fi
  fi
  printf -v "$var" '%s' "$binary"
}

# pinned_harness_check VAR PINNED_VERSION
# Requires the executable in VAR to report ${VAR%_BIN}_VERSION (default:
# PINNED_VERSION); an explicitly empty version variable skips the check.
pinned_harness_check() {
  local var="$1" pinned="$2"
  local version_var="${var%_BIN}_VERSION"
  local binary="${!var}" expected="${!version_var-$pinned}" output reported
  if [[ -z "$expected" ]]; then
    echo "WARNING: ${version_var} is empty; not checking the version of $binary" >&2
    return
  fi
  output="$("$binary" --version 2>&1 || true)"
  reported="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' <<<"$output" | head -n 1 || true)"
  if [[ "$reported" != "$expected" ]]; then
    echo "$binary reports ${reported:-an unknown version}, but ${var%_BIN} is pinned to $expected." >&2
    echo "Unset ${var} to use the vendored release, or set ${version_var} to match (empty skips the check)." >&2
    exit 1
  fi
  echo "${var%_BIN} ${reported}: $binary" >&2
}
