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
    def test_trae_scenes_use_highest_advertised_effort_for_every_role(self):
        for scene in TEAM.scene_names('traex'):
            for team in (False, True):
                cfg = TEAM.scene_settings(scene, team=team, tool='traex')
                self.assertEqual(cfg['effort'], 'xhigh')
                self.assertEqual(cfg['worker_effort'], 'xhigh')
                self.assertFalse(cfg['fast'])
                if scene == 'batch':
                    self.assertEqual(cfg['model'], 'gpt-6.1-sol' if team else 'gpt-5.6-luna')
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    TEAM.start_codex(['--scene', scene, '--dry-run'] + (['--team'] if team else []), tool='traex')
                plan = json.loads(output.getvalue())
                self.assertEqual(plan['argv'][0], 'traex')
                self.assertIn('model_reasoning_effort="xhigh"', plan['argv'])
                self.assertFalse(any('service_tier=' in arg for arg in plan['argv']))

    def test_simple_tools_ignore_scene_options_without_injecting_models(self):
        for tool in ('claude', 'opencode'):
            for team in (False, True):
                for tmux in ((False, True) if team else (False,)):
                    flags = ['--scene', 'hard', '--adjust', 'quick', '--fast', '--dry-run']
                    flags += ['--team'] if team else []
                    flags += ['--tmux'] if tmux else []
                    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                        TEAM.start_simple(flags, tool)
                    plan = json.loads(out.getvalue())
                    self.assertEqual(plan['argv'][0], tool)
                    self.assertEqual(err.getvalue().count('not applied'), 1)
                    for flag in ('--scene', '--adjust', '--fast', '--model', '--effort'):
                        self.assertNotIn(flag, plan['argv'])
                    self.assertNotIn('model', plan['config'])
                    self.assertEqual(plan['mode'], ('tmux' if tmux else 'native') if team else 'single')
                    if tool == 'opencode' and team:
                        args, env = TEAM.command(tool, plan['config'], plan['mode'])
                        for agent in json.loads(env['OPENCODE_CONFIG_CONTENT'])['agent'].values():
                            self.assertNotIn('model', agent)
                            self.assertNotIn('reasoningEffort', agent)

    def test_simple_single_preserves_native_arguments_and_separator(self):
        with patch.object(TEAM.os, 'execvpe') as execute, patch.object(TEAM.shutil, 'which', return_value='/bin/tool'):
            TEAM.start_simple(['--model', 'chosen', '--', '--scene', 'hard'], 'claude')
        self.assertEqual(execute.call_args.args[1], ['claude', '--model', 'chosen', '--', '--scene', 'hard'])

    def test_simple_check_never_starts_a_cli_and_tmux_requires_team(self):
        with patch.object(TEAM.os, 'execvpe') as execute, patch.object(TEAM.shutil, 'which', return_value='/bin/tool'), contextlib.redirect_stdout(io.StringIO()):
            TEAM.start_simple(['--check'], 'opencode')
            execute.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, '--tmux requires --team'):
            TEAM.start_simple(['--tmux'], 'claude')

    def test_codex_without_a_selected_recommendation_uses_native_defaults(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), \
                patch.object(TEAM, 'CODEX_RADAR_RUNTIME', False), \
                patch.object(TEAM, 'CODEX_RADAR_MENU_CACHE', None):
            for team in (False, True):
                flags = ['--dry-run'] + (['--team'] if team else [])
                with contextlib.redirect_stdout(io.StringIO()) as output, \
                        contextlib.redirect_stderr(io.StringIO()):
                    TEAM.start_codex(flags)
                plan = json.loads(output.getvalue())
                self.assertIsNone(plan['config']['recommendation_id'])
                self.assertEqual(plan['config']['fast'], False)
                self.assertNotIn('model', plan['config'])
                self.assertNotIn('effort', plan['config'])
                self.assertNotIn('-m', plan['argv'])
                self.assertFalse(any('model_reasoning_effort=' in arg for arg in plan['argv']))
                self.assertFalse(any('service_tier=' in arg for arg in plan['argv']))

    def test_recommendations_route_only_allowed_scenes_and_keep_lead(self):
        cache = {'source_url': TEAM.codex_radar_options()['url'], 'recommendations': {'items': [
            {'id': 'daily', 'source_key': 'daily', 'label': '日常开发', 'model': 'gpt-6.1-terra', 'effort': 'max'},
            {'id': 'batch', 'source_key': 'batch', 'label': '后台自动化', 'model': 'gpt-6-luna', 'effort': 'max'},
            {'id': 'hard', 'source_key': 'hard', 'label': '难题攻坚', 'model': 'gpt-6-astra', 'effort': 'xhigh'},
            {'id': 'unknown', 'source_key': 'unknown', 'label': '未知', 'model': 'other-model', 'effort': 'max'},
        ]}}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}):
            cfg = TEAM.codex_settings('daily', team=True, cache=cache)
            with patch.object(TEAM, 'latest_models_by_tier', return_value={1: 'gpt-6-luna', 2: 'gpt-6.1-terra', 4: 'gpt-6-astra'}):
                policy = TEAM.model_policy(cfg, 'codex', 'native')
                prompt = TEAM.instructions('native', cfg=cfg, tool='codex')
        self.assertEqual(cfg['model'], 'gpt-6.1-terra')
        self.assertEqual(cfg['limit'], 4)
        self.assertEqual([r['scene'] for r in policy['task_recommendations']], ['日常开发', '后台自动化'])
        self.assertEqual(policy['task_recommendations'][1]['model'], 'gpt-6-luna')
        self.assertIn('Match the subtask to the scene labels', prompt)
        self.assertIn('后台自动化', prompt)
        self.assertNotIn('难题攻坚', prompt)
        args, _ = TEAM.command('codex', cfg, 'native')
        self.assertIn('agents.max_concurrent_threads_per_session=4', args)

    def test_catalog_descriptions_and_efforts_guide_worker_selection(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'CODEX_HOME': directory}):
            (Path(directory) / 'models_cache.json').write_text(json.dumps({'models': [
                {'slug': 'gpt-6-luna', 'description': 'Fast for easy tasks',
                 'supported_reasoning_levels': [{'effort': 'high'}, {'effort': 'max'}]}
            ]}))
            with patch.object(TEAM, 'latest_models_by_tier', return_value={1: 'gpt-6-luna', 2: 'gpt-6.1-terra'}):
                policy = TEAM.model_policy({'model': 'gpt-6.1-terra', 'effort': 'max'}, 'codex', 'native')
        self.assertEqual(policy['model_descriptions']['gpt-6-luna'], 'Fast for easy tasks')
        luna_efforts = [item['effort'] for item in policy['allowed_worker_settings'] if item['model'] == 'gpt-6-luna']
        self.assertEqual(luna_efforts, ['high', 'max'])

    def test_team_limit_default_and_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}):
            folder = Path(directory) / 'agent-team'
            folder.mkdir()
            self.assertEqual(TEAM.codex_settings(team=True, cache={})['limit'], 4)
            self.assertEqual(TEAM.settings('claude', False)['limit'], 4)
            self.assertEqual(TEAM.scene_settings('daily', team=True, tool='traex')['limit'], 4)
            for limit in (1, 6, 8):
                (folder / 'config.json').write_text(json.dumps({'codex': {'team': {'limit': limit}}}))
                self.assertEqual(TEAM.codex_settings(team=True, cache={})['limit'], limit)
            for limit in (0, 9, True):
                (folder / 'config.json').write_text(json.dumps({'codex': {'team': {'limit': limit}}}))
                with self.assertRaisesRegex(RuntimeError, 'Team limit'):
                    TEAM.codex_settings(team=True, cache={})

    def test_fast_service_tier_is_only_sent_when_explicit(self):
        for flags, expected in [([], None), (['--fast'], 'fast'), (['--no-fast'], 'default')]:
            with self.subTest(flags=flags), contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()):
                TEAM.start_codex(['--dry-run'] + flags)
            plan = json.loads(output.getvalue())
            tiers = [arg.split('=', 1)[1] for arg in plan['argv'] if arg.startswith('service_tier=')]
            self.assertEqual(tiers, [json.dumps(expected)] if expected else [])

        args, _ = TEAM.command('codex', {'limit': 2, 'fast': False}, 'native')
        self.assertFalse(any('service_tier=' in arg for arg in args))
        args, _ = TEAM.command('codex', {'limit': 2, 'fast': True, 'fast_explicit': True}, 'native')
        self.assertIn('service_tier="fast"', args)
    def test_trae_catalog_checks_aliases_effort_and_missing_models(self):
        cfg = TEAM.scene_settings('hard', team=True, tool='traex')
        catalog = {'models': [dict(slug=name.upper(), config_name=name,
                                   supported_reasoning_levels=[{'effort': 'xhigh'}])
                              for name in ('gpt-6-astra', 'gpt-6.1-sol')]}
        result = subprocess.CompletedProcess([], 0, json.dumps(catalog), '')
        with patch.object(TEAM.subprocess, 'run', return_value=result) as run:
            TEAM.validate_scene_models(cfg, team=True, tool='traex')
            self.assertEqual(run.call_args.args[0], ['traex', 'debug', 'models'])
            with self.assertRaisesRegex(RuntimeError, 'reasoning effort is unavailable'):
                TEAM.validate_scene_models(dict(cfg, effort='max'), tool='traex')
            with self.assertRaisesRegex(RuntimeError, 'no fallback'):
                TEAM.validate_scene_models(dict(cfg, model='gpt-6-luna'), tool='traex')

    def test_traex_budget_is_a_solo_wrapper_with_existing_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory,
                                          'AGENT_TEAM_ROLE': 'lead'}):
                args, env = TEAM.solo_command('traex', 'budget', ('resume', 'session-id'))
        self.assertEqual(args, ['traex', '--dangerously-bypass-approvals-and-sandbox',
                                '-m', 'GPT-5.6-Luna', '-c',
                                'model_reasoning_effort=' + json.dumps('xhigh'),
                                'resume', 'session-id'])
        self.assertNotIn('AGENT_TEAM_ROLE', env)
        self.assertFalse(any('developer_instructions=' in arg for arg in args))

    def test_direct_command_uses_team_budget_with_explicit_embedded_mode(self):
        args = TEAM.direct_command('codex', {
            'model': 'lead-model', 'effort': 'medium',
            'worker_model': 'budget-model', 'worker_effort': 'high', 'yolo': True,
        }, ['--help'], budget=True)
        self.assertEqual(args, [
            'codex', '--no-daemon', '--yolo', '-m', 'budget-model',
            '-c', 'model_reasoning_effort="high"', '--help',
        ])

    def test_direct_run_supports_budget_and_preserves_literal_arguments(self):
        cfg = {'model': 'budget-model', 'effort': 'high', 'yolo': True}
        with patch.object(TEAM, 'settings', return_value=cfg), \
                patch.object(TEAM.shutil, 'which', return_value='/bin/codex'), \
                patch.object(TEAM.os, 'execvpe') as execute:
            TEAM.run_direct('codex', ['--budget', '--', '--help'])
        self.assertEqual(execute.call_args.args[1], [
            'codex', '--no-daemon', '--yolo', '-m', 'budget-model',
            '-c', 'model_reasoning_effort="high"', '--', '--help',
        ])

    def test_scene_dry_run_preserves_cli_separator(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            TEAM.start_codex(['--dry-run', '--approve-for-me', '--', '--dry-run'])
        self.assertEqual(json.loads(output.getvalue())['argv'][-3:], ['--approve-for-me', '--', '--dry-run'])

    def test_codex_team_without_recommendation_uses_native_defaults_for_all_roles(self):
        cfg = TEAM.codex_settings(team=True, cache={
            'source_url': TEAM.codex_radar_options()['url'],
            'recommendations': {'items': []},
        })
        self.assertFalse(cfg['fast'])
        self.assertNotIn('model', cfg)
        self.assertNotIn('worker_model', cfg)
        for mode in ('native', 'tmux'):
            for worker in (False, True):
                args, _ = TEAM.command('codex', cfg, mode, worker=worker)
                self.assertFalse(any('service_tier=' in arg for arg in args))
                self.assertNotIn('-m', args)
                self.assertFalse(any('model_reasoning_effort=' in arg for arg in args))
    def test_codex_does_not_use_old_local_scene_tiers_as_a_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'agent-team'; folder.mkdir()
            (folder / 'config.json').write_text(json.dumps({
                'codex': {'scenes': {'daily': {'model': 'old-model', 'effort': 'max'}}}
            }))
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory}), \
                    patch.object(TEAM, 'CODEX_RADAR_RUNTIME', False):
                cfg = TEAM.codex_settings('daily', team=True)
            self.assertFalse(cfg['fast'])
            self.assertNotIn('model', cfg)
            self.assertNotIn('effort', cfg)
    def test_budget_prompt_matches_custom_resolved_settings(self):
        cfg = {'model': 'lead-model', 'worker_model': 'budget-worker',
               'worker_effort': 'high', 'limit': 2}
        args, _ = TEAM.command('codex', cfg, 'native', budget=True)
        prompt = json.loads(next(x.split('=', 1)[1] for x in args
                                 if x.startswith('developer_instructions=')))
        resolved = json.loads(next(line for line in prompt.splitlines() if line.startswith('{')))
        self.assertEqual(resolved['preset'], 'team-budget')
        self.assertEqual(resolved['worker_model'], 'budget-worker')
        self.assertEqual(resolved['worker_effort'], 'high')
        self.assertIn('agents.default_subagent_model="budget-worker"', args)
        self.assertNotIn('gpt-6-astra', prompt)

    def test_native_drops_inherited_pane_identity_for_every_adapter(self):
        inherited = {'AGENT_TEAM_DIR': '/previous/team', 'AGENT_TEAM_ROLE': 'lead',
                     'AGENT_TEAM_FUTURE_TOKEN': 'old'}
        with patch.dict(os.environ, inherited):
            for tool in TEAM.TOOLS:
                _, env = TEAM.command(tool, {'limit': 2}, 'native')
                self.assertFalse(any(k.startswith('AGENT_TEAM_') for k in env))
                self.assertEqual(os.environ['AGENT_TEAM_ROLE'], 'lead')

    def test_cwd_is_normalized_without_consuming_literal_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            for flag in (['-C', folder], ['--cd=' + folder]):
                cwd, rest = TEAM.working_directory('codex', flag + ['--', '-C', 'literal'])
                self.assertEqual(cwd, str(Path(folder).resolve()))
                self.assertEqual(rest, ['--', '-C', 'literal'])
        with self.assertRaisesRegex(RuntimeError, 'requires a directory'):
            TEAM.working_directory('codex', ['-C'])

    def test_preflight_keeps_selected_permissions_and_reports_denial(self):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, AGENT_TEAM_DIR=folder, AGENT_TEAM_ROLE='lead')
            result = subprocess.CompletedProcess([], 1, '', 'Operation not permitted')
            with patch.object(TEAM.subprocess, 'run', return_value=result) as run:
                with self.assertRaisesRegex(RuntimeError, 'no model session started'):
                    TEAM.codex_preflight(['codex', '--profile', 'team-budget',
                                          '--sandbox', 'workspace-write', '-a', 'never'], env, folder)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ['codex', 'sandbox'])
            self.assertIn('team-budget', argv)
            self.assertIn(':workspace', argv)
            self.assertNotIn('danger-full-access', ' '.join(argv))
            self.assertFalse(json.loads((Path(folder) / 'preflight-lead.json').read_text())['ok'])

    def test_preflight_does_not_prevent_normal_approval_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, AGENT_TEAM_DIR=folder, AGENT_TEAM_ROLE='lead')
            result = subprocess.CompletedProcess([], 1, '', 'socket denied')
            with patch.object(TEAM.subprocess, 'run', return_value=result), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                TEAM.codex_preflight(['codex', '-P', ':workspace', '-a', 'on-request'], env, folder)
            self.assertIn('normal approval flow', err.getvalue())

    def test_picker_selects_tool_preset_and_mode(self):
        with patch.object(TEAM.sys, 'argv', ['agent-team', '--tmux']), \
                patch.object(TEAM.sys.stdin, 'isatty', return_value=True), \
                patch.object(TEAM.shutil, 'which', return_value='/bin/tool'), \
                patch('builtins.input', side_effect=['4', '2']), \
                patch.object(TEAM, 'start_simple') as launch, \
                contextlib.redirect_stdout(io.StringIO()):
            TEAM.main()
        launch.assert_called_once_with(['--team', '--tmux'], tool='claude')

    def test_picker_uses_available_codex_recommendations_without_default_option(self):
        url = TEAM.codex_radar_options()['url']
        cache = {
            'version': 2,
            'source_url': url,
            'fetched_at': time.time(),
            'recommendations': {'items': [
                {'id': 'blue-team-1234567890', 'source_key': 'blue_team',
                 'label': '蓝队任务 · GPT-5.6 Sol xhigh', 'model': 'gpt-5.6-sol', 'effort': 'xhigh'}
            ]},
        }
        with patch.object(TEAM.sys, 'argv', ['agent-team']), \
                patch.object(TEAM.sys.stdin, 'isatty', return_value=True), \
                patch.object(TEAM.shutil, 'which', side_effect=lambda name: '/bin/tool' if name == 'codex' else None), \
                patch('builtins.input', side_effect=['1', '1', '2']), \
                patch.object(TEAM, 'prepare_codex_radar_for_menu', return_value=cache), \
                patch.object(TEAM, 'start_codex') as launch, \
                contextlib.redirect_stdout(io.StringIO()):
            TEAM.main()
        launch.assert_called_once_with(
            ['--recommendation', 'blue-team-1234567890', '--team'], tool='codex')

    def test_picker_refresh_replaces_displayed_recommendation(self):
        url = TEAM.codex_radar_options()['url']
        def cache(model):
            return {'version': 2, 'source_url': url, 'recommendations': {'items': [{
                'id': model, 'source_key': 'daily', 'label': model,
                'model': model, 'effort': 'max'}]}}
        with patch.object(TEAM.sys, 'argv', ['agent-team']), \
                patch.object(TEAM.sys.stdin, 'isatty', return_value=True), \
                patch.object(TEAM.shutil, 'which', side_effect=lambda name: '/bin/tool' if name == 'codex' else None), \
                patch('builtins.input', side_effect=['1', 'r', '1', '1']), \
                patch.object(TEAM, 'prepare_codex_radar_for_menu', return_value=cache('old-1')), \
                patch.object(TEAM, 'update_codex_radar_recommendations', return_value=(cache('new-2'), True)) as refresh, \
                patch.object(TEAM, 'start_codex') as launch, contextlib.redirect_stdout(io.StringIO()):
            TEAM.main()
        refresh.assert_called_once_with(force=True, strict=True)
        launch.assert_called_once_with(['--recommendation', 'new-2'], tool='codex')

    def test_picker_can_turn_off_recommended_fast(self):
        cache = {'source_url': TEAM.codex_radar_options()['url'], 'recommendations': {'items': [{
            'id': 'daily-123', 'source_key': 'daily', 'label': 'daily + Fast',
            'model': 'gpt-6.1-terra', 'effort': 'max', 'fast': True}]}}
        with patch.object(TEAM.sys, 'argv', ['agent-team']), \
                patch.object(TEAM.sys.stdin, 'isatty', return_value=True), \
                patch.object(TEAM.shutil, 'which', side_effect=lambda name: '/bin/tool' if name == 'codex' else None), \
                patch('builtins.input', side_effect=['1', 'f', '1', '1']), \
                patch.object(TEAM, 'prepare_codex_radar_for_menu', return_value=cache), \
                patch.object(TEAM, 'start_codex') as launch, contextlib.redirect_stdout(io.StringIO()):
            TEAM.main()
        launch.assert_called_once_with(['--recommendation', 'daily-123', '--no-fast'], tool='codex')

    def test_picker_keeps_trae_scene_selection(self):
        with patch.object(TEAM.sys, 'argv', ['agent-team']), \
                patch.object(TEAM.sys.stdin, 'isatty', return_value=True), \
                patch.object(TEAM.shutil, 'which', side_effect=lambda name: '/bin/tool' if name == 'traex' else None), \
                patch('builtins.input', side_effect=['1', '3', '1']), \
                patch.object(TEAM, 'start_codex') as launch, \
                contextlib.redirect_stdout(io.StringIO()):
            TEAM.main()
        launch.assert_called_once_with(['--scene', 'batch'], tool='traex')
    def test_computer_scene_is_not_available_to_trae(self):
        with self.assertRaisesRegex(RuntimeError, 'Unknown traex scene: computer'):
            TEAM.scene_settings('computer', tool='traex')
        with patch.object(TEAM.os, 'execvpe') as execute, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                TEAM.start_codex(['--scene', 'computer', '--dry-run'], tool='traex')
            execute.assert_not_called()

    def test_removed_codex_presets_and_dynamic_recommendation_selector(self):
        for flags in (['--team-expert'], ['--team-budget']):
            with self.assertRaisesRegex(RuntimeError, 'removed'):
                TEAM.launch('codex', flags + ['--team-dry-run'])
        cfg = TEAM.codex_settings('unknown-recommendation', team=True, cache={
            'source_url': TEAM.codex_radar_options()['url'],
            'recommendations': {'items': []},
        })
        self.assertNotIn('model', cfg)
        self.assertFalse(cfg['fast'])
        quick = TEAM.scene_settings('daily', True, 'quick', tool='traex')
        self.assertEqual((quick['model'], quick['effort']), ('gpt-6-astra', 'low'))
    def test_check_without_available_recommendation_still_uses_codex_defaults(self):
        with patch.object(TEAM, 'CODEX_RADAR_RUNTIME', False), \
                patch.object(TEAM, 'CODEX_RADAR_MENU_CACHE', None), \
                patch.object(TEAM, 'validate_scene_models') as validate, \
                patch.object(TEAM.os, 'execvpe') as execute, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            TEAM.start_codex(['--recommendation', 'missing-rec', '--team', '--check'])
        validate.assert_called_once()
        self.assertTrue(validate.call_args.args[1])
        plan = json.loads(output.getvalue())
        self.assertIsNone(plan['config']['recommendation_id'])
        self.assertNotIn('model', plan['config'])
        execute.assert_not_called()
    def test_model_catalog_rejects_unavailable_models_efforts_and_fast(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'CODEX_HOME': directory}):
            cfg = TEAM.scene_settings('daily', True)
            # Missing optional tiers use Codex defaults and do not require its model catalog.
            TEAM.validate_scene_models(cfg, True)
            cfg = dict(cfg, model='gpt-6.1-sol', effort='max',
                       worker_model='gpt-6.1-sol', worker_effort='max', fast=True)
            with self.assertRaisesRegex(RuntimeError, 'catalog is unavailable'):
                TEAM.validate_scene_models(cfg, True)
            path = Path(directory) / 'models_cache.json'
            path.write_text(json.dumps({'models': []}))
            with self.assertRaisesRegex(RuntimeError, 'no fallback'):
                TEAM.validate_scene_models(cfg, True)
            model = {'slug':'gpt-6.1-sol', 'supported_reasoning_levels':[{'effort':'max'}],
                     'additional_speed_tiers':['fast']}
            path.write_text(json.dumps({'models':[model]}))
            TEAM.validate_scene_models(cfg, True)
            model['additional_speed_tiers'] = []
            path.write_text(json.dumps({'models':[model]}))
            with self.assertRaisesRegex(RuntimeError, 'Fast mode is unavailable'):
                TEAM.validate_scene_models(cfg, True)
            cfg = dict(cfg, fast=False, effort='ultra')
            with self.assertRaisesRegex(RuntimeError, 'reasoning effort is unavailable'):
                TEAM.validate_scene_models(cfg, True)

    def test_codex_launch_is_not_blocked_by_missing_or_outdated_cache(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'CODEX_HOME': directory, 'XDG_CONFIG_HOME': directory}):
            path = Path(directory) / 'models_cache.json'
            for cache in (None, '{"models": []}', 'incomplete cache'):
                if cache is not None:
                    path.write_text(cache)
                for team in (False, True):
                    with self.subTest(cache=cache, team=team), \
                            patch.object(TEAM.shutil, 'which', return_value='/bin/codex'), \
                            patch.object(TEAM.os, 'execvpe') as execute, \
                            contextlib.redirect_stderr(io.StringIO()):
                        TEAM.start_codex(['--team'] if team else ['--yolo'])
                    args = execute.call_args.args[1]
                    self.assertNotIn('-m', args)
                    self.assertFalse(any('model_reasoning_effort=' in arg for arg in args))
                    self.assertFalse(any('service_tier=' in arg for arg in args))
                    self.assertTrue('--yolo' in args or '--dangerously-bypass-approvals-and-sandbox' in args)

    def test_check_uses_codex_defaults_without_a_model_catalog(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'CODEX_HOME': directory, 'XDG_CONFIG_HOME': directory}):
            for team in (False, True):
                with self.subTest(team=team), patch.object(TEAM.os, 'execvpe') as execute, \
                        contextlib.redirect_stdout(io.StringIO()) as output, \
                        contextlib.redirect_stderr(io.StringIO()):
                    TEAM.start_codex(['--check'] + (['--team'] if team else []))
                    execute.assert_not_called()
                    plan = json.loads(output.getvalue())
                    self.assertNotIn('-m', plan['argv'])
                    self.assertFalse(any('model_reasoning_effort=' in arg for arg in plan['argv']))

    def test_missing_codex_teams_file_uses_native_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / 'missing-config'
            config_dir.mkdir()
            for name in ('team-rules.md', 'team-pane.md', 'model-levels.json'):
                shutil.copy2(ROOT / 'config' / name, config_dir / name)
            with patch.object(TEAM, 'CONFIG', config_dir), patch.dict(os.environ, {
                    'CODEX_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'xdg')}):
                cfg = TEAM.scene_settings('daily', team=True)
                self.assertFalse(cfg['fast'])
                self.assertNotIn('model', cfg)
                self.assertNotIn('effort', cfg)
                with contextlib.redirect_stdout(io.StringIO()) as output, \
                        contextlib.redirect_stderr(io.StringIO()):
                    TEAM.start_codex(['--scene', 'daily', '--team', '--check'])
                plan = json.loads(output.getvalue())
                self.assertNotIn('-m', plan['argv'])
                self.assertFalse(any('model_reasoning_effort=' in arg for arg in plan['argv']))

    def test_codex_radar_html_keeps_dynamic_categories_and_model_choices(self):
        document = """<h2>站长推荐</h2>
          <h3>日常开发</h3><p>GPT-6.1 Terra Max + Fast</p>
          <h3>后台自动化</h3><p>GPT-5.6 Luna max + Fast / GPT-6 Luna max</p>
          <h3>蓝队任务</h3><p>GPT-5.6 Sol xhigh</p><h2>其他</h2>"""
        normalized = TEAM.normalize_codex_radar_html(document, TEAM.codex_radar_options())
        items = normalized['items']
        self.assertEqual([(x['model'], x['effort']) for x in items], [
            ('gpt-6.1-terra', 'max'), ('gpt-5.6-luna', 'max'),
            ('gpt-6-luna', 'max'), ('gpt-5.6-sol', 'xhigh')])
        self.assertEqual(len({item['id'] for item in items}), 4)
        self.assertEqual([item['fast'] for item in items], [True, True, False, False])
        self.assertTrue(items[0]['label'].endswith(' + Fast'))
        cache = {'source_url': TEAM.codex_radar_options()['url'], 'recommendations': normalized}
        selected = TEAM.codex_settings(items[2]['id'], team=True, cache=cache)
        self.assertEqual((selected['model'], selected['worker_model'], selected['worker_effort']),
                         ('gpt-6-luna', 'gpt-6-luna', 'max'))
        self.assertFalse(selected['fast'])
        self.assertTrue(selected['fast_explicit'])

    def test_recommended_fast_propagates_and_can_be_overridden(self):
        document = "<h2>站长推荐</h2><h3>日常开发</h3><p>GPT-6.1 Terra Max + Fast</p>"
        items = TEAM.normalize_codex_radar_html(document, TEAM.codex_radar_options())['items']
        cache = {'version': 2, 'source_url': TEAM.codex_radar_options()['url'],
                 'recommendations': {'items': items}}
        for team in (False, True):
            for override, expected in (([], 'fast'), (['--no-fast'], 'default')):
                with self.subTest(team=team, override=override), \
                        patch.object(TEAM, 'CODEX_RADAR_RUNTIME', True), \
                        patch.object(TEAM, 'CODEX_RADAR_MENU_CACHE', cache), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    TEAM.start_codex(['--recommendation', items[0]['id'], '--dry-run'] +
                                     (['--team'] if team else []) + override)
                plan = json.loads(output.getvalue())
                self.assertEqual(plan['config']['fast'], expected == 'fast')
                self.assertIn('service_tier="' + expected + '"', plan['argv'])
                if team:
                    worker, _ = TEAM.command('codex', plan['config'], 'tmux', worker=True)
                    self.assertIn('service_tier="' + expected + '"', worker)

    def test_codex_radar_refresh_fetches_and_caches_dynamic_recommendations(self):
        document = "<h2>站长推荐</h2><h3>日常开发</h3><p>GPT-6.1 Terra Max + Fast</p>"

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _limit): return document.encode()

        class Opener:
            def __init__(self): self.request = None
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            opener = Opener()
            with patch.object(TEAM.urllib.request, 'build_opener', return_value=opener):
                cache, updated = TEAM.update_codex_radar_recommendations(force=True)
            self.assertTrue(updated)
            self.assertEqual(opener.request.full_url, 'https://codexradar.com/')
            self.assertEqual(opener.request.get_header('Origin'), 'https://codexradar.com')
            self.assertEqual(cache['version'], 2)
            item = cache['recommendations']['items'][0]
            self.assertEqual(item['model'], 'gpt-6.1-terra')
            self.assertEqual(item['effort'], 'max')
            self.assertTrue(item['fast'])
            saved = TEAM.read_codex_radar_cache()
            self.assertEqual(saved['recommendations']['items'][0]['effort'], 'max')

    def test_old_html_cache_migrates_and_survives_failed_refresh(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            path = TEAM.codex_radar_cache_path()
            path.parent.mkdir(parents=True)
            TEAM.write(path, {'version': 2, 'source_url': 'https://codexradar.com/api/radar-insights',
                'fetched_from_url': 'https://codexradar.com/', 'source_format': 'html',
                'fetched_at': 42, 'recommendations': {'items': [{'model': 'old-model'}]}})
            with patch.object(TEAM, 'codex_radar_fetch', side_effect=OSError('offline')):
                cache, updated = TEAM.update_codex_radar_recommendations(force=True)
            self.assertFalse(updated)
            self.assertEqual(cache['source_url'], 'https://codexradar.com/')
            self.assertEqual(cache['recommendations']['items'][0]['model'], 'old-model')
            self.assertIn('last_error', cache)

    def test_legacy_scene_cache_migrates_to_empty_remote_recommendations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old-cache.json'
            TEAM.write(path, {
                'version': 1,
                'source_url': TEAM.codex_radar_options()['url'],
                'last_attempt_at': 123,
                'last_error': 'HTTPError:404',
                'scenes': {'daily': {'model': 'stale-model', 'effort': 'max'}},
            })
            cache = TEAM.read_codex_radar_cache(path)
        self.assertEqual(cache['version'], 2)
        self.assertEqual(cache['recommendations'], {'items': []})
        self.assertNotIn('scenes', cache)
        self.assertEqual(cache['last_error'], 'HTTPError:404')

    def test_recommendation_status_identifies_endpoint_for_cached_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            endpoint = TEAM.codex_radar_options()['url']
            cache = {
                'version': 2, 'source_url': endpoint, 'last_attempt_url': endpoint,
                'last_error': 'HTTPError:404', 'recommendations': {'items': []},
            }
            TEAM.codex_radar_cache_path().parent.mkdir(parents=True)
            TEAM.write(TEAM.codex_radar_cache_path(), cache)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                TEAM.codex_radar_command(['status'])
        status = json.loads(output.getvalue())
        self.assertEqual(status['last_error'], 'HTTPError:404')
        self.assertEqual(status['last_error_endpoint'], endpoint)
    def test_cache_without_fast_metadata_is_refreshed_without_waiting_a_day(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            path = TEAM.codex_radar_cache_path()
            path.parent.mkdir(parents=True)
            cache = {'version': 2, 'source_url': TEAM.codex_radar_options()['url'],
                     'last_attempt_at': time.time(), 'recommendations': {'items': [{'model': 'old'}]}}
            TEAM.write(path, cache)
            with patch.object(TEAM, 'start_codex_radar_background_refresh', return_value=True) as start:
                self.assertEqual(TEAM.prepare_codex_radar_for_menu(), cache)
                start.assert_called_once()
            cache['last_error'] = 'offline'
            TEAM.write(path, cache)
            with patch.object(TEAM, 'start_codex_radar_background_refresh') as start:
                TEAM.prepare_codex_radar_for_menu()
                start.assert_not_called()

    def test_codex_radar_refresh_is_staged_and_empty_cache_retries_quickly(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            url = TEAM.codex_radar_options()['url']
            old = {
                'version': 2,
                'source_url': url,
                'fetched_at': time.time() - 86401,
                'last_attempt_at': time.time() - 86401,
                'recommendations': {'items': [{
                    'id': 'daily-1111111111', 'source_key': 'daily_development',
                    'label': '日常开发 · old-model max', 'model': 'old-model', 'effort': 'max'
                }]},
            }
            TEAM.codex_radar_cache_path().parent.mkdir(parents=True)
            TEAM.write(TEAM.codex_radar_cache_path(), old)
            with patch.object(TEAM, 'start_codex_radar_background_refresh', return_value=True) as start, \
                    patch.object(TEAM, 'update_codex_radar_recommendations') as update:
                active = TEAM.prepare_codex_radar_for_menu()
                start.assert_called_once()
                update.assert_not_called()
            self.assertEqual(active['recommendations']['items'][0]['model'], 'old-model')

            new = {
                'version': 2,
                'source_url': url,
                'fetched_at': time.time(),
                'last_attempt_at': time.time(),
                'recommendations': {'items': [{
                    'id': 'daily-2222222222', 'source_key': 'daily_development',
                    'label': '日常开发 · new-model high', 'model': 'new-model', 'effort': 'high', 'fast': False
                }]},
            }
            failed_with_data = dict(old, last_attempt_at=time.time() - 301, last_error='network error')
            TEAM.write(TEAM.codex_radar_cache_path(), failed_with_data)
            with patch.object(TEAM, 'start_codex_radar_background_refresh', return_value=True) as start:
                self.assertEqual(TEAM.prepare_codex_radar_for_menu()['last_error'], 'network error')
                start.assert_called_once()

            TEAM.write(TEAM.codex_radar_pending_cache_path(), new)
            active = TEAM.prepare_codex_radar_for_menu()
            self.assertEqual(active['recommendations']['items'][0]['model'], 'new-model')
            self.assertFalse(TEAM.codex_radar_pending_cache_path().exists())

            failed = {
                'version': 2,
                'source_url': url,
                'last_attempt_at': time.time() - 301,
                'recommendations': {'items': []},
                'last_error': 'HTTPError:404 GET https://codexradar.com/api/radar-insights',
            }
            TEAM.write(TEAM.codex_radar_cache_path(), failed)
            with patch.object(TEAM, 'update_codex_radar_recommendations', return_value=(new, True)) as update:
                active = TEAM.prepare_codex_radar_for_menu()
            update.assert_called_once_with(force=True)
            self.assertEqual(active['recommendations']['items'][0]['model'], 'new-model')
    def test_trae_launches_still_validate_live_catalog_before_exec(self):
        for team in (False, True):
            with self.subTest(team=team), \
                    patch.object(TEAM, 'validate_scene_models', side_effect=RuntimeError('catalog rejected')) as validate, \
                    patch.object(TEAM.os, 'execvpe') as execute:
                with self.assertRaisesRegex(RuntimeError, 'catalog rejected'):
                    TEAM.start_codex(['--team'] if team else [], tool='traex')
                validate.assert_called_once()
                self.assertEqual(validate.call_args.kwargs['tool'], 'traex')
                execute.assert_not_called()

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
                    if tool in ('codex', 'traex'):
                        self.assertIn('--dangerously-bypass-approvals-and-sandbox', args)
                    elif tool == 'claude':
                        self.assertIn('--dangerously-skip-permissions', args)

    def test_team_prompt_keeps_primary_waiting_for_required_agents(self):
        args, _ = TEAM.command('codex', {'limit': 2}, 'native')
        prompt = json.loads(next(x.split('=', 1)[1] for x in args
                                 if x.startswith('developer_instructions=')))
        normalized_prompt = ' '.join(prompt.split())
        self.assertIn("must use the runtime's native agent-wait mechanism", normalized_prompt)
        self.assertIn('do not end the turn or return an idle prompt', normalized_prompt)
        self.assertIn(
            'wait times out while required agents are still live, wait again',
            normalized_prompt,
        )

    def test_yolo_defaults_and_opt_out_cover_every_member(self):
        for tool in TEAM.TOOLS:
            for mode in ('native', 'tmux'):
                for worker in (False, True):
                    for yolo in (False, True):
                        with self.subTest(tool=tool, mode=mode, worker=worker, yolo=yolo):
                            args, env = TEAM.command(tool, {'limit': 2, 'yolo': yolo}, mode, worker=worker)
                            if tool == 'opencode':
                                cfg = json.loads(env['OPENCODE_CONFIG_CONTENT'])
                                lead = cfg['agent']['team-lead']
                                self.assertEqual(lead.get('permission', {}).get('*') == 'allow', yolo)
                                if mode == 'tmux' or worker:
                                    self.assertEqual(lead['permission']['task'], 'deny')
                                elif yolo:
                                    self.assertEqual(cfg['agent']['team-worker']['permission'], {'*': 'allow', 'task': 'deny'})
                            else:
                                flag = '--dangerously-skip-permissions' if tool == 'claude' else '--dangerously-bypass-approvals-and-sandbox'
                                self.assertEqual(flag in args, yolo)

    def test_no_yolo_wrapper_flag_and_literal_prompt(self):
        with patch.object(TEAM, 'settings', return_value={'limit': 2}), patch.object(TEAM.os, 'execvpe') as execute:
            TEAM.launch('codex', ['--no-yolo', '--', '--no-yolo'])
        args = execute.call_args.args[1]
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
        self.assertEqual(args[-2:], ['--', '--no-yolo'])

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

    def test_claude_environment_default_respects_external_value(self):
        cfg = TEAM.settings('claude', False)
        self.assertEqual(cfg['env']['CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR'], '1')
        with patch.dict(os.environ, {'CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR': '0'}):
            _, env = TEAM.command('claude', cfg, 'native')
        self.assertEqual(env['CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR'], '0')

    def test_prompt_after_separator_is_not_a_wrapper_flag(self):
        with patch.object(TEAM, 'settings', return_value={'limit': 2}), \
                patch.object(TEAM.shutil, 'which', return_value='/bin/claude'), \
                patch.object(TEAM.os, 'execvpe') as execute:
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

    def report(self, name, text='evidence', status='ready'):
        assignment = self.state()['members'][name]['assignment']
        return json.loads(self.control('report', '--file', self.file(name + '-result', text),
                                       '--status', status, '--assignment', assignment['assignment_id'],
                                       '--attempt', assignment['attempt_id'], role=name))

    def test_codex_team_worker_uses_native_defaults_when_recommendations_are_missing(self):
        codex_home = self.root / 'codex-home'; codex_home.mkdir()
        (codex_home / 'models_cache.json').write_text(json.dumps({'models':[
            {'slug':model, 'supported_reasoning_levels':[{'effort':effort} for effort in ['high','max','xhigh']], 'additional_speed_tiers':['fast']}
            for model in ['gpt-6.1-sol','gpt-6-astra','gpt-6-luna','gpt-6.1-terra']]}))
        fake = self.bin / 'codex'
        fake.write_text('#!' + os.sys.executable + '\nimport json,os,sys\nfrom pathlib import Path\nPath(os.environ["AGENT_TEAM_DIR"], os.environ["AGENT_TEAM_ROLE"]+"-argv.json").write_text(json.dumps(sys.argv[1:]))\nprint("interactive-ready",flush=True)\nfor line in sys.stdin: pass\n')
        fake.chmod(0o755)
        self.env['CODEX_HOME'] = str(codex_home)
        previous = set((self.root / 'state/agent-team').iterdir())
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(TEAM, 'CODEX_RADAR_RUNTIME', False), \
                patch.object(TEAM, 'CODEX_RADAR_MENU_CACHE', None), \
                contextlib.redirect_stdout(io.StringIO()):
            TEAM.start_codex(['--team','--tmux'])
        self.directory = next(path for path in (self.root / 'state/agent-team').iterdir() if path not in previous)
        self.env['AGENT_TEAM_DIR'] = str(self.directory)
        self.wait_for(lambda: 'interactive-ready' in self.capture('lead'))
        worker = self.spawn('worker_a')
        for role in ('lead', 'worker_a'):
            args = json.loads((self.directory / (role+'-argv.json')).read_text())
            self.assertNotIn('-m', args)
            efforts = [arg for arg in args if arg.startswith('model_reasoning_effort=')]
            expected = [] if role == 'lead' else [
                'model_reasoning_effort=' + json.dumps(TEAM.model_policy_config()['default_thinking_level'])]
            self.assertEqual(efforts, expected)
            self.assertFalse(any('service_tier=' in arg for arg in args))
            if role == 'worker_a':
                self.assertIn(worker['assignment_id'], args[-1])
                self.assertIn(worker['attempt_id'], args[-1])
        self.assertIsNone(self.state()['config']['recommendation_id'])
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
        self.spawn('three')
        self.spawn('four')
        with self.assertRaisesRegex(RuntimeError, 'limit'):
            self.spawn('five')
        with self.assertRaisesRegex(RuntimeError, 'Only the lead'):
            self.control('spawn', 'recursive', '--file', self.file('x', 'x'), role='one')
        self.report('one')
        messages = json.loads(self.control('receive'))
        self.assertEqual(messages[0]['text'], 'evidence')
        self.assertEqual(json.loads(self.control('receive')), messages)
        self.control('ack', '--id', str(messages[0]['id']))
        self.assertEqual(json.loads(self.control('receive')), [])
        hostile = "literal `touch NO` $(touch NO)\nnew line"
        self.control('send', 'one', '--file', self.file('followup', hostile), '--wake')
        self.wait_for(lambda: 'team message is waiting' in self.capture('one'))
        self.assertEqual(json.loads(self.control('receive', role='one'))[0]['text'], hostile)
        self.assertEqual(len(self.state()['members']), 5)
        self.control('stop', 'two')
        self.assertEqual(self.state()['members']['two']['status'], 'stopped')

    def test_sidebar_is_preserved_during_spawn_and_stop(self):
        state = self.state()
        cmd = [self.real_tmux, '-L', self.socket]
        lead = state['members']['lead']['pane']
        sidebar = subprocess.check_output(cmd + ['split-window', '-d', '-b', '-h', '-l', '25',
                                          '-t', lead, '-P', '-F', '#{pane_id}', 'sleep 300'], text=True).strip()
        subprocess.run(cmd + ['set-option', '-p', '-t', sidebar, '@pane_role', 'sidebar'], check=True)

        def positions():
            rows = subprocess.check_output(cmd + ['list-panes', '-t', state['window'], '-F',
                '#{pane_id} #{pane_left} #{pane_top} #{pane_width}'], text=True).splitlines()
            return {pane: tuple(map(int, (left, top, width)))
                    for pane, left, top, width in (row.split() for row in rows)}

        for name in ('one', 'two'):
            self.spawn(name)
            pos = positions()
            self.assertEqual(pos[sidebar][0::2], (0, 25))
            self.assertLess(pos[lead][0], pos[self.state()['members'][name]['pane']][0])
        pos = positions()
        one, two = (state['pane'] for name, state in self.state()['members'].items() if name != 'lead')
        self.assertEqual(pos[one][0], pos[two][0])
        self.assertNotEqual(pos[one][1], pos[two][1])
        self.control('stop', 'two')
        pos = positions()
        self.assertEqual(pos[sidebar][0::2], (0, 25))
        self.assertLess(pos[lead][0], pos[one][0])
        self.control('stop', 'one')
        self.assertEqual(positions()[sidebar][0::2], (0, 25))

    def test_right_sidebar_keeps_position_and_width(self):
        state = self.state()
        cmd = [self.real_tmux, '-L', self.socket]
        lead = state['members']['lead']['pane']
        sidebar = subprocess.check_output(cmd + ['split-window', '-d', '-h', '-l', '24',
                                          '-t', lead, '-P', '-F', '#{pane_id}', 'sleep 300'], text=True).strip()
        subprocess.run(cmd + ['set-option', '-p', '-t', sidebar, '@pane_role', 'sidebar'], check=True)
        self.spawn('one')
        self.spawn('two')
        rows = subprocess.check_output(cmd + ['list-panes', '-t', state['window'], '-F',
            '#{pane_id} #{pane_left} #{pane_width}'], text=True).splitlines()
        pos = {pane: (int(left), int(width)) for pane, left, width in (row.split() for row in rows)}
        self.assertEqual(pos[sidebar][1], 24)
        self.assertGreater(pos[sidebar][0], max(pos[pane][0] for pane in pos if pane != sidebar))
        self.assertLess(pos[lead][0], pos[self.state()['members']['one']['pane']][0])

    def test_role_prefix_survives_application_title_updates(self):
        self.spawn('one')
        for name, role in [('lead', 'lead'), ('one', 'worker')]:
            pane = self.state()['members'][name]['pane']
            command = [self.real_tmux, '-L', self.socket]
            for title in ['Action Required | task', 'Working | new title']:
                subprocess.run(command + ['select-pane', '-t', pane, '-T', title], check=True)
                rendered = subprocess.check_output(command + ['display-message', '-p', '-t', pane,
                                                    '#{E:pane-border-format}'], text=True).strip()
                self.assertEqual(rendered, role + ' | ' + title)

    def test_doctor_and_denied_spawn_preserve_members(self):
        self.assertTrue(json.loads(self.control('doctor'))['ok'])
        before = self.state()
        denied = subprocess.CompletedProcess([], 1, '', 'Operation not permitted')
        with patch.object(TEAM.subprocess, 'run', return_value=denied):
            with self.assertRaisesRegex(RuntimeError, 'normal approval mechanism'):
                self.control('spawn', 'worker_a', '--file', self.file('brief', 'task'))
        self.assertEqual(self.state()['members'], before['members'])
        self.assertEqual(self.state()['messages'], [])

    def test_recorded_socket_survives_stripped_tmux_environment(self):
        env = dict(self.env, PATH=os.environ['PATH'])
        env.pop('TMUX', None)
        env.pop('TMUX_PANE', None)
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            TEAM.control(['doctor'])
        self.assertTrue(json.loads(out.getvalue())['ok'])

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
        receipt = json.loads(self.control('send', 'one', '--file', self.file('queued', 'later'), '--wake'))
        self.assertEqual(receipt['wake'], 'not_idle')
        self.assertEqual(json.loads(self.control('receive', role='one'))[0]['text'], 'later')

    def test_assign_retry_fences_stale_report_and_duplicate_report(self):
        first = self.spawn('one')
        first_result = self.report('one', 'blocked evidence', 'blocked')
        self.assertTrue(self.report('one', 'blocked evidence', 'blocked')['duplicate'])
        brief = self.file('retry', 'Try another approach')
        second = json.loads(self.control('assign', 'one', '--file', brief, '--retry-of', first['attempt_id'],
                                         '--request-id', 'retry-1'))
        self.assertEqual(second['assignment_id'], first['assignment_id'])
        self.assertNotEqual(second['attempt_id'], first['attempt_id'])
        again = json.loads(self.control('assign', 'one', '--file', brief, '--retry-of', first['attempt_id'],
                                        '--request-id', 'retry-1'))
        self.assertEqual(again['queued'], second['queued'])
        self.assertTrue(again['duplicate'])
        with self.assertRaisesRegex(RuntimeError, 'current assignment and attempt'):
            self.control('report', '--file', self.file('old-report', 'late'), '--assignment', first['assignment_id'],
                         '--attempt', first['attempt_id'], role='one')
        self.report('one', 'new result')
        self.assertEqual(self.state()['members']['one']['assignment']['status'], 'ready')
        self.assertEqual(first_result['queued'], 1)

    def test_mail_replay_ack_and_request_id(self):
        self.spawn('one')
        message_file = self.file('send', 'message')
        first = json.loads(self.control('send', 'one', '--file', message_file, '--request-id', 'send-1'))
        repeated = json.loads(self.control('send', 'one', '--file', message_file, '--request-id', 'send-1'))
        self.assertEqual(first['queued'], repeated['queued'])
        self.assertTrue(repeated['duplicate'])
        with self.assertRaisesRegex(RuntimeError, 'different message'):
            self.control('send', 'one', '--file', self.file('different', 'different'), '--request-id', 'send-1')
        for _ in range(2):
            self.assertEqual(json.loads(self.control('wait', '--timeout', '0', role='one'))[0]['id'], first['queued'])
        with self.assertRaisesRegex(RuntimeError, 'recipient'):
            self.control('ack', '--id', str(first['queued']))
        self.control('ack', '--id', str(first['queued']), role='one')
        self.control('ack', '--id', str(first['queued']), role='one')
        self.assertEqual(json.loads(self.control('receive', role='one')), [])

    def test_wake_failure_retains_assignment_and_returns_recovery_receipt(self):
        self.spawn('one')
        self.report('one')
        with patch.object(TEAM, 'wake', side_effect=RuntimeError('socket lost')):
            result = json.loads(self.control('assign', 'one', '--file', self.file('next', 'follow up'),
                                             '--request-id', 'next-1', '--wake'))
        self.assertIn('socket lost', result['wake'])
        self.assertEqual(self.state()['members']['one']['status'], 'assigned')
        self.assertEqual(json.loads(self.control('receive', role='one'))[0]['attempt_id'], result['attempt_id'])
        self.assertTrue(json.loads(self.control('assign', 'one', '--file', str(self.root / 'next'),
                                                '--request-id', 'next-1'))['duplicate'])

    def test_existing_team_keeps_legacy_receive_and_reporting(self):
        self.spawn('one')
        state = self.state()
        del state['protocol_version']
        del state['members']['one']['assignment']
        TEAM.write(self.directory / 'state.json', state)
        self.control('report', '--file', self.file('old-result', 'legacy'), role='one')
        self.assertEqual(json.loads(self.control('receive'))[0]['text'], 'legacy')
        self.assertEqual(json.loads(self.control('receive')), [])
        with self.assertRaisesRegex(RuntimeError, 'legacy protocol'):
            self.control('assign', 'one', '--file', self.file('next', 'work'))

    def test_legacy_team_can_spawn_worker_after_upgrade(self):
        state = self.state()
        del state['protocol_version']
        TEAM.write(self.directory / 'state.json', state)
        receipt = self.spawn('one')
        self.assertNotIn('assignment_id', receipt)
        self.assertIsNone(self.state()['members']['one']['assignment'])
        with patch.dict(os.environ, dict(self.env, AGENT_TEAM_ROLE='one'), clear=True):
            args, _ = TEAM.command('claude', self.state()['config'], 'tmux', worker=True, brief='work')
        self.assertIn('This existing team uses the legacy pane protocol', args[args.index('--append-system-prompt') + 1])
        self.control('report', '--file', self.file('old-worker-result', 'legacy'), role='one')
        self.assertEqual(json.loads(self.control('receive'))[0]['text'], 'legacy')

    def test_stop_unknown_does_not_claim_stopped(self):
        self.spawn('one')
        with patch.object(TEAM, 'pane_presence', return_value='unknown'):
            with self.assertRaisesRegex(RuntimeError, 'Stop outcome unknown'):
                self.control('stop', 'one')
        self.assertEqual(self.state()['members']['one']['status'], 'running')
        self.assertTrue(self.capture('one'))
