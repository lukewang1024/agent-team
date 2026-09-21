import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader('agent_team', str(ROOT / 'bin/agent-team'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
TEAM = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(TEAM)


class AdapterTests(unittest.TestCase):
    def test_codex_defaults_work_without_personal_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'CODEX_HOME': directory, 'XDG_CONFIG_HOME': directory}):
                cfg = TEAM.settings('codex', False)
                self.assertEqual(cfg['model'], 'gpt-6-astra')
                self.assertEqual(cfg['worker_effort'], 'low')
                args, _ = TEAM.command('codex', cfg, 'native')
                self.assertNotIn('--profile', args)
                self.assertIn('gpt-6-astra', args)

    def test_picker_selects_tool_preset_and_mode(self):
        with patch.object(TEAM.sys, 'argv', ['agent-team', '--tmux']), \
                patch.object(TEAM.sys.stdin, 'isatty', return_value=True), \
                patch.object(TEAM.shutil, 'which', return_value='/bin/tool'), \
                patch('builtins.input', side_effect=['4', '2']), \
                patch.object(TEAM, 'launch') as launch, \
                contextlib.redirect_stdout(io.StringIO()):
            TEAM.main()
        launch.assert_called_once_with('claude', ['--tmux', '--team-budget'])

    def test_native_and_pane_modes_use_distinct_delegation(self):
        for tool in TEAM.TOOLS:
            for mode in ('native', 'tmux'):
                with self.subTest(tool=tool, mode=mode):
                    args, env = TEAM.command(tool, {'limit': 2}, mode)
                    if tool == 'codex':
                        self.assertIn('agents.enabled=' + ('true' if mode == 'native' else 'false'), args)
                    elif tool == 'traex':
                        self.assertIn('--enable' if mode == 'native' else '--disable', args)
                        self.assertIn('multi_agent', args)
                    elif tool == 'claude':
                        self.assertEqual('--disallowedTools' in args, mode == 'tmux')
                        self.assertEqual(env['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'], '0')
                    else:
                        lead = json.loads(env['OPENCODE_CONFIG_CONTENT'])['agent']['team-lead']
                        self.assertEqual(lead.get('permission', {}).get('task'), 'deny' if mode == 'tmux' else None)
                    self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
                    self.assertNotIn('--dangerously-skip-permissions', args)

    def test_worker_uses_own_model_and_cannot_delegate(self):
        for tool in TEAM.TOOLS:
            args, env = TEAM.command(tool, {'model': 'primary', 'worker_model': 'worker', 'worker_effort': 'high'}, 'tmux', worker=True)
            if tool == 'opencode':
                lead = json.loads(env['OPENCODE_CONFIG_CONTENT'])['agent']['team-lead']
                self.assertEqual(lead['model'], 'worker')
                self.assertEqual(lead['permission']['task'], 'deny')
            else:
                self.assertIn('worker', args)
                self.assertNotIn('primary', args)
            self.assertNotIn('--profile', args)

    def test_opencode_inline_settings_are_preserved(self):
        with patch.dict(os.environ, {'OPENCODE_CONFIG_CONTENT': '{"theme":"custom","agent":{"other":{"mode":"subagent"}}}'}):
            _, env = TEAM.command('opencode', {}, 'native')
        cfg = json.loads(env['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(cfg['theme'], 'custom')
        self.assertIn('other', cfg['agent'])

    def test_custom_model_combinations_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'agent-team'
            folder.mkdir()
            (folder / 'config.json').write_text('{"claude":{"budget":{"worker_model":"custom","limit":1}}}')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}):
                cfg = TEAM.settings('claude', True)
                self.assertEqual(cfg['worker_model'], 'custom')
                self.assertEqual(cfg['limit'], 1)
                self.assertNotIn('worker_model', TEAM.settings('claude', False))

    def test_prompt_after_separator_is_not_a_wrapper_flag(self):
        with patch.object(TEAM, 'settings', return_value={'limit': 2}), patch.object(TEAM.os, 'execvpe') as execute:
            TEAM.launch('claude', ['--', '--tmux'])
        self.assertEqual(execute.call_args.args[1][-2:], ['--', '--tmux'])


@unittest.skipUnless(shutil.which('tmux'), 'tmux not installed')
class PaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.socket = 'agent-team-test-' + uuid.uuid4().hex
        self.real_tmux = shutil.which('tmux')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'tmux').write_text('#!/bin/sh\nexec ' + self.real_tmux + ' -L ' + self.socket + ' "$@"\n')
        (self.bin / 'tmux').chmod(0o755)
        (self.bin / 'claude').write_text('#!/bin/sh\nprintf "interactive-ready\\n"\nwhile IFS= read -r line; do printf "received:%s\\n" "$line"; done\n')
        (self.bin / 'claude').chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'], XDG_STATE_HOME=str(self.root / 'state'))
        self.env.pop('TMUX', None)
        self.env.pop('TMUX_PANE', None)
        self.addCleanup(lambda: subprocess.run([self.real_tmux, '-L', self.socket, 'kill-server'], capture_output=True))
        pane = subprocess.check_output([self.real_tmux, '-L', self.socket, '-f', '/dev/null', 'new-session', '-d', '-P', '-F', '#{pane_id}', '-s', 'test', '-x', '160', '-y', '50'], env=self.env, text=True).strip()
        self.env.update(TMUX='isolated-test', TMUX_PANE=pane)
        with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            TEAM.launch('claude', ['--tmux'])
        self.directory = next((self.root / 'state/agent-team').iterdir())
        self.env.update(AGENT_TEAM_DIR=str(self.directory), AGENT_TEAM_ROLE='lead')
        self.wait_for(lambda: 'interactive-ready' in self.capture('lead'))

    def state(self):
        return json.loads((self.directory / 'state.json').read_text())

    def capture(self, name):
        pane = self.state()['members'][name]['pane']
        return subprocess.run([self.real_tmux, '-L', self.socket, 'capture-pane', '-p', '-t', pane], capture_output=True, text=True).stdout

    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.05)
        self.fail('Timed out waiting for pane state')

    def control(self, *args, role='lead'):
        with patch.dict(os.environ, dict(self.env, AGENT_TEAM_ROLE=role), clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            TEAM.control(list(args))
        return out.getvalue()

    def file(self, name, text):
        path = self.root / name
        path.write_text(text)
        return str(path)

    def spawn(self, name):
        result = self.control('spawn', name, '--file', self.file(name + '.txt', 'Inspect one task; do not spawn agents.'))
        self.wait_for(lambda: 'interactive-ready' in self.capture(name))
        return json.loads(result)

    def test_interactive_layout_cap_mailbox_reuse_and_stop(self):
        self.spawn('one')
        self.spawn('two')
        state = self.state()
        rows = subprocess.check_output([self.real_tmux, '-L', self.socket, 'list-panes', '-t', state['window'], '-F', '#{pane_id} #{pane_left} #{pane_top}'], text=True).splitlines()
        positions = {p: (int(x), int(y)) for p, x, y in (line.split() for line in rows)}
        lead = state['members']['lead']['pane']
        one = state['members']['one']['pane']
        two = state['members']['two']['pane']
        self.assertLess(positions[lead][0], positions[one][0])
        self.assertEqual(positions[one][0], positions[two][0])
        self.assertNotEqual(positions[one][1], positions[two][1])
        with self.assertRaisesRegex(RuntimeError, 'limit'):
            self.spawn('three')
        with self.assertRaisesRegex(RuntimeError, 'Only the lead'):
            self.control('spawn', 'recursive', '--file', self.file('x', 'x'), role='one')
        self.control('report', '--file', self.file('result', 'evidence'), role='one')
        messages = json.loads(self.control('receive'))
        self.assertEqual(messages[0]['text'], 'evidence')
        self.assertEqual(json.loads(self.control('receive')), [])
        hostile = "literal `touch NO` $(touch NO)\nnew line"
        self.control('send', 'one', '--file', self.file('followup', hostile), '--wake')
        self.wait_for(lambda: 'team message is waiting' in self.capture('one'))
        self.assertEqual(json.loads(self.control('receive', role='one'))[0]['text'], hostile)
        self.assertEqual(len(self.state()['members']), 3)
        self.control('stop', 'two')
        self.assertEqual(self.state()['members']['two']['status'], 'stopped')

    def test_main_exit_closes_workers_but_not_original_window(self):
        self.spawn('one')
        state = self.state()
        subprocess.run([self.real_tmux, '-L', self.socket, 'send-keys', '-t', state['members']['lead']['pane'], 'C-d'], check=True)
        self.wait_for(lambda: self.state().get('closed'))
        self.wait_for(lambda: not self.capture('one'))
        windows = subprocess.check_output([self.real_tmux, '-L', self.socket, 'list-windows', '-t', 'test'], text=True)
        self.assertTrue(windows.strip())

    def test_queued_message_does_not_claim_delivery(self):
        self.spawn('one')
        with self.assertRaisesRegex(RuntimeError, 'queued'):
            self.control('send', 'one', '--file', self.file('queued', 'later'), '--wake')
        self.assertEqual(json.loads(self.control('receive', role='one'))[0]['text'], 'later')
