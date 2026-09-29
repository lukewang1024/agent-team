import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('installer', ROOT / 'install.py')
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallTests(unittest.TestCase):
    def test_yolo_shortcuts_only_add_permission_flags(self):
        flags = {
            'codex': '--yolo',
            'traex': '--yolo',
            'claude': '--dangerously-skip-permissions',
            'opencode': '--auto',
        }
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory)
            for tool in flags:
                executable = fake_bin / tool
                executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
                executable.chmod(0o755)
            env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ.get('PATH', ''))
            for tool, flag in flags.items():
                with self.subTest(tool=tool):
                    result = subprocess.run(
                        [str(ROOT / 'bin' / f'{tool}-yolo'), '--model', 'chosen-model'],
                        check=True, capture_output=True, text=True, env=env,
                    )
                    self.assertEqual(result.stdout.splitlines(), [flag, '--model', 'chosen-model'])

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'bin'
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.install(ROOT, target)
                INSTALLER.install(ROOT, target)
            self.assertEqual((target / 'agent-team').resolve(), ROOT / 'bin/agent-team')
            self.assertEqual((target / 'codex-budget').resolve(), ROOT / 'bin/codex-budget')
            self.assertEqual((target / 'codex-expert').resolve(), ROOT / 'bin/codex-expert')
            self.assertEqual((target / 'codex-team-expert').resolve(), ROOT / 'bin/codex-team-expert')
            self.assertEqual((target / 'traex-budget').resolve(), ROOT / 'bin/traex-budget')
            expected = len([path for path in (ROOT / 'bin').iterdir() if path.is_file()])
            self.assertEqual(len(list(target.iterdir())), expected)

    def test_unrelated_entrypoint_is_preserved_before_any_install(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            conflict = target / 'claude-team'
            conflict.write_text('user file')
            with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                INSTALLER.install(ROOT, target)
            self.assertEqual(conflict.read_text(), 'user file')
            self.assertFalse((target / 'agent-team').exists())
