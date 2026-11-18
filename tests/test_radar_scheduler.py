import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('radar_scheduler_test', ROOT / 'radar_scheduler.py')
SCHEDULER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHEDULER)


class FakeSystemd:
    def __init__(self):
        self.enabled = False
        self.active = False
        self.calls = []
        self.fail_enable = False

    def __call__(self, *args, check=True):
        self.calls.append(args)
        if args[0] == 'enable':
            if self.fail_enable:
                raise RuntimeError('manager rejected timer')
            self.enabled = self.active = True
        if args[0] == 'disable':
            self.enabled = self.active = False
        code = 0
        if args[0] == 'is-enabled':
            code = 0 if self.enabled else 1
        if args[0] == 'is-active':
            code = 0 if self.active else 1
        return subprocess.CompletedProcess(args, code, '', '')


class RadarSchedulerTests(unittest.TestCase):
    def test_enable_status_and_disable_preserve_private_proxy_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {'HOME': directory, 'XDG_CONFIG_HOME': directory + '/config',
                   'XDG_CACHE_HOME': directory + '/cache', 'HTTPS_PROXY': 'http://127.0.0.1:1234',
                   'NO_PROXY': 'localhost', 'PATH': '/test/bin:/usr/bin'}
            manager = FakeSystemd()
            with patch.object(SCHEDULER, 'systemctl', side_effect=manager):
                result = SCHEDULER.enable(ROOT / 'bin/agent-team', '/usr/bin/python3', env)
                self.assertEqual(result['status'], 'enabled')
                self.assertNotIn('127.0.0.1', str(result))
                service, timer, env_file = SCHEDULER.paths(env)
                self.assertIn('recommendations sync', service.read_text())
                self.assertIn('OnCalendar=*:0/5', timer.read_text())
                self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
                saved = env_file.read_text()
                self.assertIn('HTTPS_PROXY="http://127.0.0.1:1234"', saved)
                self.assertIn('https_proxy="http://127.0.0.1:1234"', saved)
                self.assertIn('NO_PROXY="localhost"', saved)
                self.assertIn('no_proxy="localhost"', saved)
                self.assertIn('PATH="/test/bin:/usr/bin"', saved)
                self.assertTrue(SCHEDULER.status(env)['active'])
                SCHEDULER.enable(ROOT / 'bin/agent-team', '/usr/bin/python3', env)
                self.assertEqual(SCHEDULER.disable(env)['status'], 'disabled')
                self.assertFalse(any(path.exists() for path in (service, timer, env_file)))
                self.assertEqual(SCHEDULER.disable(env)['status'], 'not_installed')

    def test_scheduler_refuses_unrelated_files_before_changing_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {'HOME': directory, 'XDG_CONFIG_HOME': directory + '/config'}
            service, _, _ = SCHEDULER.paths(env)
            service.parent.mkdir(parents=True)
            service.write_text('user-owned')
            with patch.object(SCHEDULER, 'systemctl') as manager:
                with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                    SCHEDULER.enable(ROOT / 'bin/agent-team', '/usr/bin/python3', env)
                manager.assert_not_called()
            self.assertEqual(service.read_text(), 'user-owned')

    def test_enable_failure_restores_preexisting_managed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {'HOME': directory, 'XDG_CONFIG_HOME': directory + '/config'}
            service, timer, env_file = SCHEDULER.paths(env)
            service.parent.mkdir(parents=True)
            original = SCHEDULER.MARKER + 'prior configuration\n'
            service.write_text(original)
            manager = FakeSystemd()
            manager.fail_enable = True
            with patch.object(SCHEDULER, 'systemctl', side_effect=manager):
                with self.assertRaisesRegex(RuntimeError, 'rejected'):
                    SCHEDULER.enable(ROOT / 'bin/agent-team', '/usr/bin/python3', env)
            self.assertEqual(service.read_text(), original)
            self.assertFalse(timer.exists())
            self.assertFalse(env_file.exists())
            self.assertIn(('disable', '--now', SCHEDULER.TIMER), manager.calls)


if __name__ == '__main__':
    unittest.main()
