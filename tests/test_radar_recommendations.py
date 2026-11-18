import contextlib
import importlib.machinery
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader('agent_team_radar_test', str(ROOT / 'bin/agent-team'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
TEAM = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(TEAM)


COMPOSITE_DEFINITION = {'time_exponent': 2, 'reference_usd': 1, 'reference_minutes': 10}


def source_row(model, effort, score, cost, minutes, **availability):
    composite = (cost * (minutes / 10) ** 2 if type(cost) in (int, float)
                 and type(minutes) in (int, float) and math.isfinite(cost) and math.isfinite(minutes) else None)
    return {'model': model, 'effort': effort,
            'chart_metrics': {'score150': score, 'api_equivalent_usd_per_task': cost,
                              'end_to_end_minutes_per_task': minutes, 'composite_cost_index': composite},
            'availability': {'cost': True, 'time': True, 'composite': True, **availability}}


def source_payload():
    return {'schema': 'codex-model-tradeoffs/2', 'round_id': 'batch-1', 'protocol': 'four-1',
            'composite_definition': dict(COMPOSITE_DEFINITION), 'rows': [
        source_row('gpt-6.1-sol', 'high', 132.64, .1855, 5.65),
        source_row('gpt-6.1-sol', 'max', 135.98, .2624, 8.58),
        source_row('gpt-6-astra', 'ultra', 137.18, 1.302, 8.32),
        source_row('gpt-6-luna', 'xhigh', 100.94, .01274, 5.18),
        source_row('gpt-6.1-terra', 'ultra', 100.29, .04153, 1.91),
        source_row('gpt-6-astra', 'medium', 127.04, .4617, 2.88),
        # This pair is strictly worse in all dimensions than several others.
        source_row('gpt-6-sol', 'xhigh', 100, 1, 12),
    ]}


def cost_tradeoff_payload():
    # Representative trade-offs: a premium pair is only slightly more capable,
    # a mid-priced pair clears the computer-automation floor, and a cheap pair
    # is appropriate only for the lower-floor background profile.
    return {'schema': 'codex-model-tradeoffs/2', 'round_id': 'cost-tradeoff', 'protocol': 'four-1',
            'composite_definition': dict(COMPOSITE_DEFINITION), 'rows': [
        source_row('gpt-6.1-sol', 'low', 119.61, .0693, 2.07),
        source_row('gpt-6.1-sol', 'ultra', 136.84, .2569, 8.70),
        source_row('gpt-6.1-terra', 'xhigh', 117.30, .0487, 2.11),
        source_row('gpt-6.1-terra', 'max', 130.73, .1452, 6.60),
        source_row('gpt-6-astra', 'max', 137.18, 1.5244, 10.61),
        source_row('gpt-6-luna', 'max', 98.65, .00894, 3.96),
    ]}


def document(payload):
    return ('<h2>站长推荐</h2><h3>日常开发</h3><p>GPT-6.1 Terra max + Fast</p>'
            '<h2>其他</h2><script type="application/json" data-mtr-payload>'
            + json.dumps(payload) + '</script>')


def catalog():
    result = {}
    for row in source_payload()['rows']:
        metadata = result.setdefault(row['model'], {'supported_reasoning_levels': [], 'additional_speed_tiers': ['fast']})
        metadata['supported_reasoning_levels'].append({'effort': row['effort']})
    return result


class RadarRecommendationTests(unittest.TestCase):
    def test_extracts_only_complete_valid_rows_from_one_identified_batch(self):
        payload = source_payload()
        payload['rows'] += [source_row('unknown-model', 'max', 130, 1, 2, time=False),
                            source_row('bad-model', 'high', float('nan'), 1, 2),
                            source_row('bad-model', 'low', 130, None, 2)]
        benchmark = TEAM.normalize_codex_radar_tradeoffs(document(payload))
        self.assertEqual((benchmark['round_id'], benchmark['protocol']), ('batch-1', 'four-1'))
        self.assertEqual(len(benchmark['rows']), 7)
        self.assertEqual(benchmark['rows'][0]['cost_usd_per_task'], .1855)
        payload['rows'].append(payload['rows'][0])
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            TEAM.normalize_codex_radar_tradeoffs(document(payload))
        payload['schema'] = 'future-schema'
        with self.assertRaisesRegex(RuntimeError, 'schema'):
            TEAM.normalize_codex_radar_tradeoffs(document(payload))

    def test_requires_the_published_composite_definition_and_consistent_values(self):
        payload = source_payload()
        payload['composite_definition']['time_exponent'] = 1
        with self.assertRaisesRegex(RuntimeError, 'composite cost definition'):
            TEAM.normalize_codex_radar_tradeoffs(document(payload))
        payload = source_payload()
        payload['rows'][0]['chart_metrics']['composite_cost_index'] *= 2
        benchmark = TEAM.normalize_codex_radar_tradeoffs(document(payload))
        self.assertEqual(len(benchmark['rows']), len(payload['rows']) - 1)

    def test_different_scenarios_trade_off_quality_price_and_end_to_end_time(self):
        benchmark = TEAM.normalize_codex_radar_tradeoffs(document(source_payload()))
        ranked = TEAM.rank_codex_radar_tradeoffs(benchmark, catalog())
        picks = {row['source_key']: (row['model'], row['effort']) for row in ranked}
        self.assertEqual(picks['daily'], ('gpt-6.1-sol', 'high'))
        self.assertEqual(picks['hard'], ('gpt-6.1-sol', 'max'))
        self.assertEqual(picks['background'], ('gpt-6-luna', 'xhigh'))
        self.assertEqual(picks['interactive'], ('gpt-6.1-terra', 'ultra'))
        self.assertEqual(len(picks), 6)
        self.assertTrue(all(row['fast'] is False and row['catalog_verified'] for row in ranked))
        self.assertTrue(all(row['model'] != 'gpt-6-sol' for row in ranked))
        self.assertTrue(all(row['metrics']['score150'] >= row['quality_floor'] for row in ranked))
        reversed_benchmark = dict(benchmark, rows=list(reversed(benchmark['rows'])))
        self.assertEqual(ranked, TEAM.rank_codex_radar_tradeoffs(reversed_benchmark, catalog()))

    def test_quality_scale_does_not_change_with_floor_or_best_candidate(self):
        payload = {'schema': 'codex-model-tradeoffs/2', 'round_id': 'fixed-scale', 'protocol': 'four-1',
                   'composite_definition': dict(COMPOSITE_DEFINITION), 'rows': [
            source_row('cheap-model', 'high', 120, 0, 1),
            source_row('better-model', 'high', 130, 1, 1),
            source_row('best-model', 'high', 150, 10, 1),
        ]}
        benchmark = TEAM.normalize_codex_radar_tradeoffs(document(payload))
        weights = {'quality': .5, 'cost': .5, 'time': 0, 'composite': 0}
        profiles = {'profiles': [
            {'id': 'floor70', 'label': 'Floor 70', 'min_quality_ratio': .70, 'weights': weights},
            {'id': 'floor75', 'label': 'Floor 75', 'min_quality_ratio': .75, 'weights': weights},
            {'id': 'floor85', 'label': 'Floor 85', 'min_quality_ratio': .85, 'weights': weights},
        ]}
        with patch.object(TEAM, 'read', return_value=profiles):
            before = {item['source_key']: item for item in TEAM.rank_codex_radar_tradeoffs(
                dict(benchmark, rows=benchmark['rows'][:2]))}
            after = {item['source_key']: item for item in TEAM.rank_codex_radar_tradeoffs(benchmark)}
        for key in ('floor70', 'floor75'):
            self.assertEqual(before[key]['model'], 'cheap-model')
            self.assertEqual(after[key]['model'], 'cheap-model')
            self.assertEqual(before[key]['selection_score'], after[key]['selection_score'])
        self.assertEqual(after['floor70']['selection_score'], after['floor75']['selection_score'])
        self.assertEqual(before['floor85']['model'], 'cheap-model')
        self.assertEqual(after['floor85']['model'], 'better-model')
        self.assertGreater(after['floor85']['quality_floor'], 120)

    def test_site_frontiers_use_strict_dominance_and_keep_equal_points(self):
        rows = [
            {'model': 'cheap-a', 'effort': 'high', 'score150': 100, 'cost_usd_per_task': .1, 'minutes_per_task': 10},
            {'model': 'cheap-b', 'effort': 'high', 'score150': 100, 'cost_usd_per_task': .1, 'minutes_per_task': 10},
            {'model': 'fast', 'effort': 'high', 'score150': 105, 'cost_usd_per_task': .2, 'minutes_per_task': 2},
            {'model': 'worse', 'effort': 'high', 'score150': 100, 'cost_usd_per_task': .3, 'minutes_per_task': 20},
        ]
        names = lambda measure: {row['model'] for row in TEAM.codex_radar_frontier(rows, measure)}
        self.assertEqual(names(lambda row: row['cost_usd_per_task']), {'cheap-a', 'cheap-b', 'fast'})
        self.assertEqual(names(lambda row: row['minutes_per_task']), {'fast'})
        self.assertEqual(names(TEAM.codex_radar_composite), {'fast'})
        self.assertAlmostEqual(TEAM.codex_radar_composite(rows[2]), .008)

    def test_scene_frontier_uses_the_same_combined_cost_as_ranking(self):
        payload = {'schema': 'codex-model-tradeoffs/2', 'round_id': 'pareto', 'protocol': 'four-1',
                   'composite_definition': dict(COMPOSITE_DEFINITION), 'rows': [
            source_row('cheap-slow', 'high', 120, .1, 10),
            source_row('fast-better', 'high', 125, .2, 2),
        ]}
        benchmark = TEAM.normalize_codex_radar_tradeoffs(document(payload))
        profiles = {'profiles': [{'id': 'example', 'label': 'Example', 'min_quality_ratio': .70,
                                  'weights': {'quality': .70, 'cost': .20, 'time': .05, 'composite': .05}}]}
        with patch.object(TEAM, 'read', return_value=profiles):
            item = TEAM.rank_codex_radar_tradeoffs(benchmark)[0]
        self.assertEqual(item['model'], 'fast-better')
        self.assertEqual(item['alternatives'], [])
        self.assertEqual(item['site_frontiers'], ['cost', 'time', 'composite'])
        self.assertAlmostEqual(item['metrics']['composite_cost_index'], .008)

    def test_filter_before_ranking_and_avoid_invented_catalog_or_efforts(self):
        benchmark = TEAM.normalize_codex_radar_tradeoffs(document(source_payload()))
        catalog_subset = {'gpt-6.1-sol': {'supported_reasoning_levels': [{'effort': 'high'}]}}
        ranked = TEAM.rank_codex_radar_tradeoffs(benchmark, catalog_subset)
        self.assertEqual({(r['model'], r['effort']) for r in ranked}, {('gpt-6.1-sol', 'high')})
        self.assertFalse(TEAM.rank_codex_radar_tradeoffs(benchmark, catalog_subset, {('not-allowed', 'max')}))
        unverified = TEAM.rank_codex_radar_tradeoffs(benchmark, {})
        self.assertTrue(unverified)
        self.assertFalse(any(item['catalog_verified'] for item in unverified))

    def test_refresh_publishes_measured_choices_for_workbench_and_launch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                patch.object(TEAM, 'codex_radar_fetch', return_value=document(source_payload()).encode()), \
                patch.object(TEAM, 'cached_codex_model_catalog', return_value=catalog()):
            cache, updated = TEAM.update_codex_radar_recommendations(force=True)
            self.assertTrue(updated)
            saved = TEAM.read_codex_radar_cache()
            self.assertEqual(saved['benchmark']['round_id'], 'batch-1')
            self.assertTrue(saved['station_recommendations']['items'][0]['fast'])
            selected = saved['recommendations']['items'][0]
            self.assertEqual(selected['method'], 'measured_pareto_v3')
            self.assertIn('算法', selected['label'])
            cfg = TEAM.codex_settings(selected['id'], team=True, cache=saved)
            self.assertEqual((cfg['model'], cfg['effort']), (selected['model'], selected['effort']))
            self.assertEqual(cfg['recommendation_metrics'], selected['metrics'])
            self.assertFalse(cfg['fast'])
            with patch.object(TEAM, 'CODEX_RADAR_RUNTIME', True), \
                    patch.object(TEAM, 'CODEX_RADAR_MENU_CACHE', saved), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                TEAM.start_codex(['--recommendation', selected['id'], '--dry-run'])
            plan = json.loads(output.getvalue())
            self.assertIn(selected['model'], plan['argv'])
            self.assertIn('model_reasoning_effort="' + selected['effort'] + '"', plan['argv'])
            self.assertIn('service_tier="default"', plan['argv'])
            with patch.object(TEAM, 'latest_models_by_tier', return_value={
                    1: 'gpt-6-luna', 2: 'gpt-6.1-terra', 3: 'gpt-6.1-sol', 4: 'gpt-6-astra'}):
                policy = TEAM.model_policy(cfg, 'codex', 'native')
            allowed = {(i['model'], i['effort']) for i in policy['allowed_worker_settings']}
            self.assertEqual(len(policy['task_recommendations']), 6)
            self.assertTrue(all((i['model'], i['effort']) in allowed for i in policy['task_recommendations']))
            self.assertNotIn('gpt-6-astra', {i['model'] for i in policy['task_recommendations']})

    def test_catalog_change_recalculates_the_cache_used_by_workbench(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                patch.object(TEAM, 'codex_radar_fetch', return_value=document(source_payload()).encode()), \
                patch.object(TEAM, 'cached_codex_model_catalog', return_value=catalog()):
            TEAM.update_codex_radar_recommendations(force=True)
            subset = {'gpt-6.1-sol': {'supported_reasoning_levels': [{'effort': 'high'}]}}
            with patch.object(TEAM, 'cached_codex_model_catalog', return_value=subset):
                cache = TEAM.prepare_codex_radar_for_menu()
            self.assertEqual({(r['model'], r['effort']) for r in cache['recommendations']['items']},
                             {('gpt-6.1-sol', 'high')})
            self.assertEqual(TEAM.read_codex_radar_cache()['recommendations'], cache['recommendations'])

    def test_current_codex_catalog_includes_new_models_despite_stale_shared_file(self):
        payload = source_payload()
        payload['rows'] += [
            source_row('gpt-6.2-sol', 'low', 121.52, .1159, 1.87),
            source_row('gpt-6.2-sol', 'medium', 131.18, .1947, 4.04),
            source_row('gpt-6.2-astra', 'xhigh', 149.78, .9292, 21.12),
            source_row('gpt-6.2-astra', 'ultra', 148.76, 1.108, 13.25),
        ]
        by_model = {}
        for row in payload['rows']:
            model = by_model.setdefault(row['model'], {'slug': row['model'], 'supported_reasoning_levels': []})
            model['supported_reasoning_levels'].append({'effort': row['effort']})
        output = json.dumps({'models': list(by_model.values())})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config'),
                'CODEX_HOME': str(Path(directory) / 'codex')}):
            codex_home = Path(os.environ['CODEX_HOME'])
            codex_home.mkdir()
            (codex_home / 'models_cache.json').write_text(json.dumps({'models': [by_model['gpt-6.1-sol']]}))
            result = TEAM.subprocess.CompletedProcess(['codex', 'debug', 'models'], 0, output, '')
            with patch.object(TEAM.subprocess, 'run', return_value=result) as command, \
                    patch.object(TEAM, 'codex_radar_fetch', return_value=document(payload).encode()):
                cache, updated = TEAM.update_codex_radar_recommendations(force=True)
            self.assertTrue(updated)
            self.assertEqual(command.call_args.args[0], ['codex', 'debug', 'models'])
            self.assertNotIn('gpt-6.2-astra', TEAM.cached_codex_model_catalog())
            picks = {r['source_key']: r for r in cache['recommendations']['items']}
            self.assertEqual((picks['hard']['model'], picks['hard']['effort']), ('gpt-6.2-astra', 'ultra'))
            self.assertEqual((picks['interactive']['model'], picks['interactive']['effort']), ('gpt-6.2-sol', 'low'))
            self.assertIn('gpt-6.2-astra', cache['model_catalog'])
            cfg = TEAM.codex_settings(picks['hard']['id'], team=True, cache=cache)
            with patch.object(TEAM, 'CODEX_RADAR_RUNTIME', True), \
                    patch.object(TEAM, 'CODEX_RADAR_MENU_CACHE', cache):
                policy = TEAM.model_policy(cfg, 'codex', 'native')
            allowed = {(x['model'], x['effort']) for x in policy['allowed_worker_settings']}
            self.assertTrue(all((x['model'], x['effort']) in allowed for x in policy['task_recommendations']))
            self.assertTrue(all(x['model'] in cache['model_catalog'] for x in policy['allowed_worker_settings']))

    def test_catalog_query_failure_is_unverified_not_filtered_by_stale_cache(self):
        payload = source_payload()
        payload['rows'].append(source_row('gpt-6.2-astra', 'ultra', 148.76, 1.108, 13.25))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config'),
                'CODEX_HOME': str(Path(directory) / 'codex')}):
            codex_home = Path(os.environ['CODEX_HOME'])
            codex_home.mkdir()
            (codex_home / 'models_cache.json').write_text(json.dumps({'models': [
                {'slug': 'gpt-6.1-sol', 'supported_reasoning_levels': [{'effort': 'max'}]}]}))
            result = TEAM.subprocess.CompletedProcess(['codex', 'debug', 'models'], 1, '', '')
            with patch.object(TEAM.subprocess, 'run', return_value=result), \
                    patch.object(TEAM, 'codex_radar_fetch', return_value=document(payload).encode()):
                cache, _ = TEAM.update_codex_radar_recommendations(force=True)
            picks = cache['recommendations']['items']
            self.assertIn('unverified', cache['catalog_warning'])
            self.assertEqual(cache['model_catalog'], {})
            self.assertTrue(all(not item['catalog_verified'] and '可用性未验证' in item['label'] for item in picks))
            self.assertEqual(next(x['model'] for x in picks if x['source_key'] == 'hard'), 'gpt-6.2-astra')

    def test_fixed_scale_replaces_old_menu_cache(self):
        payload = cost_tradeoff_payload()
        supported = {}
        for row in payload['rows']:
            supported.setdefault(row['model'], {'supported_reasoning_levels': []})['supported_reasoning_levels'].append(
                {'effort': row['effort']})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                patch.object(TEAM, 'codex_radar_fetch', return_value=document(payload).encode()), \
                patch.object(TEAM, 'cached_codex_model_catalog', return_value=supported):
            old, _ = TEAM.update_codex_radar_recommendations(force=True)
            old = json.loads(json.dumps(old))
            for item in old['recommendations']['items']:
                item['method'] = 'measured_tradeoff_v2'
            daily = old['recommendations']['items'][0]
            daily.update(model='gpt-6.1-sol', effort='low', id='old-daily', quality_floor=116.6)
            TEAM.write(TEAM.codex_radar_cache_path(), old)

            cache = TEAM.prepare_codex_radar_for_menu()
            picks = {row['source_key']: row for row in cache['recommendations']['items']}
            self.assertEqual((picks['daily']['model'], picks['daily']['effort']), ('gpt-6.1-terra', 'max'))
            self.assertEqual((picks['computer']['model'], picks['computer']['effort']), ('gpt-6.1-terra', 'max'))
            self.assertEqual((picks['hard']['model'], picks['hard']['effort']), ('gpt-6.1-sol', 'ultra'))
            self.assertEqual((picks['background']['model'], picks['background']['effort']), ('gpt-6-luna', 'max'))
            self.assertEqual(picks['daily']['quality_floor'], picks['computer']['quality_floor'])
            self.assertEqual(cache['recommendations'], TEAM.read_codex_radar_cache()['recommendations'])
            self.assertTrue(all(item['method'] == 'measured_pareto_v3' for item in picks.values()))
            for item in picks.values():
                self.assertGreaterEqual(item['metrics']['score150'], item['quality_floor'])
                self.assertEqual(TEAM.codex_settings(item['id'], team=True, cache=cache)['model'], item['model'])

    def test_failed_refresh_keeps_last_successful_measured_batch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                patch.object(TEAM, 'cached_codex_model_catalog', return_value=catalog()):
            with patch.object(TEAM, 'codex_radar_fetch', return_value=document(source_payload()).encode()):
                original, _ = TEAM.update_codex_radar_recommendations(force=True)
            with patch.object(TEAM, 'codex_radar_fetch', side_effect=OSError('offline')):
                retained, updated = TEAM.update_codex_radar_recommendations(force=True)
            self.assertFalse(updated)
            self.assertEqual(retained['benchmark'], original['benchmark'])
            self.assertEqual(retained['recommendations'], original['recommendations'])
            self.assertIn('last_error', retained)

    def test_periodic_sync_stages_new_batch_and_retries_without_losing_it(self):
        first = source_payload()
        second = dict(source_payload(), round_id='batch-2')
        third = dict(source_payload(), round_id='batch-3')
        now = 1_000_000
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                patch.object(TEAM, 'cached_codex_model_catalog', return_value=catalog()), \
                patch.object(TEAM.time, 'time', return_value=now):
            with patch.object(TEAM, 'codex_radar_fetch', return_value=document(first).encode()):
                TEAM.update_codex_radar_recommendations(force=True)
            with patch.object(TEAM, 'codex_radar_fetch') as fetch:
                self.assertEqual(TEAM.sync_codex_radar_if_due()['status'], 'not_due')
                fetch.assert_not_called()

            with patch.object(TEAM.time, 'time', return_value=now + 86401), \
                    patch.object(TEAM, 'codex_radar_fetch', return_value=document(second).encode()) as fetch:
                self.assertEqual(TEAM.sync_codex_radar_if_due()['status'], 'updated')
                self.assertEqual(TEAM.sync_codex_radar_if_due()['status'], 'not_due')
                fetch.assert_called_once()
            self.assertEqual(TEAM.read_codex_radar_cache()['benchmark']['round_id'], 'batch-1')
            staged = TEAM.read_codex_radar_cache(TEAM.codex_radar_pending_cache_path())
            self.assertEqual(staged['benchmark']['round_id'], 'batch-2')

            failed_at = now + 86401 * 2
            with patch.object(TEAM.time, 'time', return_value=failed_at), \
                    patch.object(TEAM, 'codex_radar_fetch', side_effect=OSError('offline')):
                self.assertEqual(TEAM.sync_codex_radar_if_due()['status'], 'failed')
            retained = TEAM.read_codex_radar_cache(TEAM.codex_radar_pending_cache_path())
            self.assertEqual(retained['benchmark']['round_id'], 'batch-2')
            self.assertIn('last_error', retained)
            with patch.object(TEAM.time, 'time', return_value=failed_at + 299), \
                    patch.object(TEAM, 'codex_radar_fetch') as fetch:
                self.assertEqual(TEAM.sync_codex_radar_if_due()['status'], 'not_due')
                fetch.assert_not_called()
            with patch.object(TEAM.time, 'time', return_value=failed_at + 301), \
                    patch.object(TEAM, 'codex_radar_fetch', return_value=document(third).encode()):
                self.assertEqual(TEAM.sync_codex_radar_if_due()['status'], 'updated')
                promoted = TEAM.prepare_codex_radar_for_menu()
            self.assertEqual(promoted['benchmark']['round_id'], 'batch-3')
            self.assertTrue(all(r['method'] == 'measured_pareto_v3'
                                for r in promoted['recommendations']['items']))
            with patch.object(TEAM, 'codex_radar_fetch', return_value=document(third).encode()) as fetch:
                self.assertEqual(TEAM.sync_codex_radar_if_due(force=True)['status'], 'updated')
                fetch.assert_called_once()

    def test_periodic_sync_does_not_overlap_background_refresh(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            self.assertTrue(TEAM.acquire_codex_radar_refresh_lock())
            try:
                with patch.object(TEAM, 'codex_radar_fetch') as fetch:
                    self.assertEqual(TEAM.sync_codex_radar_if_due()['status'], 'busy')
                    fetch.assert_not_called()
            finally:
                TEAM.release_codex_radar_refresh_lock()

    def test_promoting_pending_cache_does_not_remove_a_newer_concurrent_fetch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            options = TEAM.codex_radar_options()
            def cache(batch, timestamp):
                return {'version': 2, 'source_url': options['url'], 'last_attempt_at': timestamp,
                        'recommendations': {'items': []}, 'benchmark': {'round_id': batch}}
            active, pending, newer = cache('first', 1), cache('second', 2), cache('third', 3)
            path = TEAM.codex_radar_cache_path()
            pending_path = TEAM.codex_radar_pending_cache_path()
            path.parent.mkdir(parents=True)
            TEAM.write(path, active)
            TEAM.write(pending_path, pending)
            original_write = TEAM.write
            def write_with_concurrent_fetch(destination, value):
                original_write(destination, value)
                if destination == path:
                    original_write(pending_path, newer)
            with patch.object(TEAM, 'write', side_effect=write_with_concurrent_fetch):
                promoted = TEAM.promote_codex_radar_pending(options)
            self.assertEqual(promoted['benchmark']['round_id'], 'second')
            self.assertEqual(TEAM.read_codex_radar_cache()['benchmark']['round_id'], 'second')
            self.assertEqual(TEAM.read_codex_radar_cache(pending_path)['benchmark']['round_id'], 'third')

    def test_late_failure_cannot_replace_a_newer_successful_batch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            options = TEAM.codex_radar_options()
            active = {'version': 2, 'source_url': options['url'], 'fetched_at': 100, 'last_attempt_at': 100,
                      'recommendations': {'items': []}, 'benchmark': {'round_id': 'newer-success'}}
            pending = {'version': 2, 'source_url': options['url'], 'fetched_at': 50, 'last_attempt_at': 110,
                       'last_error': 'offline', 'recommendations': {'items': []},
                       'benchmark': {'round_id': 'older-data'}}
            path = TEAM.codex_radar_cache_path()
            path.parent.mkdir(parents=True)
            TEAM.write(path, active)
            TEAM.write(TEAM.codex_radar_pending_cache_path(), pending)
            newest = TEAM.newest_codex_radar_cache(options)
            self.assertEqual(newest['benchmark']['round_id'], 'newer-success')
            self.assertEqual(newest['last_error'], 'offline')
            promoted = TEAM.promote_codex_radar_pending(options)
            self.assertEqual(promoted['benchmark']['round_id'], 'newer-success')
            self.assertEqual(promoted['last_attempt_at'], 110)
            self.assertEqual(TEAM.read_codex_radar_cache(), promoted)

    def test_failed_pending_promotion_preserves_the_staged_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}):
            options = TEAM.codex_radar_options()
            pending = {'version': 2, 'source_url': options['url'], 'last_attempt_at': 2,
                       'recommendations': {'items': []}, 'benchmark': {'round_id': 'staged'}}
            path = TEAM.codex_radar_pending_cache_path()
            path.parent.mkdir(parents=True)
            TEAM.write(path, pending)
            with patch.object(TEAM, 'write', side_effect=OSError('disk full')):
                self.assertIsNone(TEAM.promote_codex_radar_pending(options))
            self.assertEqual(TEAM.read_codex_radar_cache(path), pending)

            with patch.object(TEAM, 'write', side_effect=OSError('disk full')), \
                    patch.object(TEAM.os, 'link', side_effect=OSError('link unavailable')):
                with self.assertRaisesRegex(OSError, 'link unavailable'):
                    TEAM.promote_codex_radar_pending(options)
            claimed = list(path.parent.glob(path.name + '.*.claimed'))
            self.assertEqual(len(claimed), 1)
            self.assertEqual(TEAM.read_codex_radar_cache(claimed[0]), pending)

    def test_invalid_measurements_fall_back_to_station_without_synthesizing_values(self):
        payload = source_payload()
        payload['schema'] = 'unsupported'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                patch.object(TEAM, 'codex_radar_fetch', return_value=document(payload).encode()):
            cache, _ = TEAM.update_codex_radar_recommendations(force=True)
            self.assertIsNone(cache['benchmark'])
            self.assertIn('using station picks', cache['source_warning'])
            self.assertEqual(cache['recommendations'], cache['station_recommendations'])
            self.assertTrue(cache['recommendations']['items'][0]['fast'])

    def test_changed_composite_definition_keeps_last_measured_batch(self):
        payload = source_payload()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'XDG_CACHE_HOME': directory, 'XDG_CONFIG_HOME': str(Path(directory) / 'config')}), \
                patch.object(TEAM, 'cached_codex_model_catalog', return_value=catalog()):
            with patch.object(TEAM, 'codex_radar_fetch', return_value=document(payload).encode()):
                original, _ = TEAM.update_codex_radar_recommendations(force=True)
            payload['composite_definition']['time_exponent'] = 3
            with patch.object(TEAM, 'codex_radar_fetch', return_value=document(payload).encode()):
                retained, updated = TEAM.update_codex_radar_recommendations(force=True)
            self.assertFalse(updated)
            self.assertEqual(retained['benchmark'], original['benchmark'])
            self.assertEqual(retained['recommendations'], original['recommendations'])
            self.assertIn('retaining last measured batch', retained['last_error'])


if __name__ == '__main__':
    unittest.main()
