import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('installer', ROOT / 'install.py')
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallTests(unittest.TestCase):
    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'bin'
            with contextlib.redirect_stdout(io.StringIO()):
                INSTALLER.install(ROOT, target)
                INSTALLER.install(ROOT, target)
            self.assertEqual((target / 'agent-team').resolve(), ROOT / 'bin/agent-team')
            self.assertEqual(len(list(target.iterdir())), 9)

    def test_unrelated_entrypoint_is_preserved_before_any_install(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            conflict = target / 'claude-team'
            conflict.write_text('user file')
            with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                INSTALLER.install(ROOT, target)
            self.assertEqual(conflict.read_text(), 'user file')
            self.assertFalse((target / 'agent-team').exists())
