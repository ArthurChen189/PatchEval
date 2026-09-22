import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from hydra import compose, initialize_config_dir

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / 'patcheval/exp_agent/agents'
HARNESSES = {'codex': 'CODEX', 'opencode': 'OPENCODE'}


def harness_config(name):
    with initialize_config_dir(version_base='1.3', config_dir=str(ROOT / 'scripts/conf')):
        return compose(config_name='config', overrides=[f'harness={name}']).harness


def adapter_pin(name):
    text = (AGENTS / f'{name}.sh').read_text()
    prefix = HARNESSES[name]
    version = re.search(rf'^{prefix}_PINNED_VERSION=(\S+)$', text, re.M).group(1)
    binary = re.search(rf'^{prefix}_VENDORED_BIN=(\S+)$', text, re.M).group(1)
    return version, binary


class PinnedHarnessTests(unittest.TestCase):
    """The legacy adapters enforce the same pins as the Hydra harness YAMLs."""

    def source_adapter(self, name, tmp, **env):
        tmp = Path(tmp)
        (tmp / 'local.config.toml').touch()
        (tmp / 'config/opencode').mkdir(parents=True, exist_ok=True)
        (tmp / 'data').mkdir(exist_ok=True)
        (tmp / 'config/opencode/opencode.json').touch()
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(('CODEX_', 'OPENCODE_'))}
        base.update(CODEX_CONFIG=str(tmp / 'local.config.toml'),
                    OPENCODE_CONFIG=str(tmp / 'config/opencode/opencode.json'), **env)
        script = (f'set -euo pipefail; source {AGENTS / name}.sh; '
                  f'printf "%s" "${HARNESSES[name]}_BIN"')
        return subprocess.run(['bash', '-c', script], env=base, text=True, capture_output=True)

    def test_adapter_pins_match_harness_yaml(self):
        for name in HARNESSES:
            with self.subTest(harness=name):
                version, binary = adapter_pin(name)
                cfg = harness_config(name)
                self.assertEqual(str(cfg.version), version)
                self.assertIn(f'/{version}/', binary)
                if f'{HARNESSES[name]}_BIN' not in os.environ:
                    self.assertEqual(cfg.binary, str(ROOT / binary))
                archive = ROOT / f'{binary}.xz'
                self.assertTrue(archive.is_file(), archive)
                self.assertIn(archive.name, (archive.parent / 'SHA256SUMS').read_text())

    def test_adapter_rejects_other_versions_before_running(self):
        for name in HARNESSES:
            prefix = HARNESSES[name]
            version, _ = adapter_pin(name)
            with self.subTest(harness=name), tempfile.TemporaryDirectory() as tmp:
                binary = Path(tmp) / 'cli'
                binary.write_text('#!/bin/sh\necho cli 99.0.0\n')
                binary.chmod(0o755)
                result = self.source_adapter(name, tmp, **{f'{prefix}_BIN': str(binary)})
                self.assertEqual(result.returncode, 1)
                self.assertIn(f'pinned to {version}', result.stderr)
                # An explicit matching version, or an empty one, accepts the override.
                result = self.source_adapter(name, tmp, **{f'{prefix}_BIN': str(binary),
                                                           f'{prefix}_VERSION': '99.0.0'})
                self.assertEqual((result.returncode, result.stdout), (0, str(binary)), result.stderr)
                result = self.source_adapter(name, tmp, **{f'{prefix}_BIN': str(binary),
                                                           f'{prefix}_VERSION': ''})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('WARNING', result.stderr)
                binary.write_text(f'#!/bin/sh\necho cli {version}\n')
                result = self.source_adapter(name, tmp, **{f'{prefix}_BIN': str(binary)})
                self.assertEqual((result.returncode, result.stdout), (0, str(binary)), result.stderr)

    def test_adapter_defaults_to_the_verified_vendored_release(self):
        # Extracts the vendored binary on first use (git-ignored), as generation does.
        for name in HARNESSES:
            _, binary = adapter_pin(name)
            with self.subTest(harness=name), tempfile.TemporaryDirectory() as tmp:
                result = self.source_adapter(name, tmp)
                self.assertEqual((result.returncode, result.stdout), (0, str(ROOT / binary)), result.stderr)


if __name__ == '__main__':
    unittest.main()
