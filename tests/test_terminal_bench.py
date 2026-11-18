import io
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import terminal_bench as bench


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader('agent_team_terminal_test', str(ROOT / 'bin/agent-team'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
TEAM = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(TEAM)


def row(model, effort, quality, ci, cost, minutes, count=100, status="display"):
    return {
        "status": status,
        "metadata": {
            "agent_display": {"label": "Codex"},
            "model_display": {"label": model, "url": "https://developers.openai.com/api/docs/models/" + model},
            "reasoning_effort": effort,
            "date": "2026-11-17",
        },
        "metrics": {"accuracy": quality, "accuracy_ci95_half_width": ci, "n_trials": count,
                    "total_cost_usd": cost * count, "avg_trial_duration_sec": minutes * 60},
    }


def document(rows, version="4-1-0"):
    board = {"leaderboard": {"package": bench.PACKAGE, "name": version}, "rows": rows}
    serialized = "prefix:" + json.dumps(board, separators=(",", ":"))
    # Split the serialized data over chunks, like a streaming Next.js response.
    midpoint = len(serialized) // 2
    chunks = (serialized[:midpoint], serialized[midpoint:])
    return "".join("<script>self.__next_f.push(" + json.dumps([1, chunk]) + ")</script>" for chunk in chunks)


def dataset():
    return [row("gpt-6.2-sol", "high", 62, 2, 1, 10),
            row("gpt-6.2-astra", "max", 64, 2, 3, 15),
            row("gpt-6.1-terra", "high", 40, 2, 4, 20)]


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Opener:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error
        self.count = 0

    def open(self, request, timeout):
        self.count += 1
        if self.error:
            raise self.error
        return Response(self.data.encode())


class TerminalBenchmarkTests(unittest.TestCase):
    def test_decodes_versioned_board_and_mean_cost_and_wall_time(self):
        parsed = bench.parse_leaderboard(document(dataset()))
        self.assertEqual(parsed["board"], "4-1-0")
        self.assertEqual(parsed["rows"][0]["cost_usd_per_task"], 1)
        self.assertEqual(parsed["rows"][0]["minutes_per_task"], 10)
        self.assertEqual(parsed["rows"][0]["n_trials"], 100)

    def test_version_drift_duplicate_or_incomplete_data_is_not_mixed(self):
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            bench.parse_leaderboard(document(dataset(), version="4-2-0"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            bench.parse_leaderboard(document(dataset() + [dataset()[0]]))
        missing_time = dataset()
        missing_time[0]["metrics"].pop("avg_trial_duration_sec")
        parsed = bench.parse_leaderboard(document(missing_time))
        self.assertNotIn("gpt-6.2-sol", {r["model"] for r in parsed["rows"]})
        with self.assertRaisesRegex(ValueError, "fewer than two"):
            bench.parse_leaderboard(document(missing_time[:2]))

    def test_scenarios_trade_quality_against_cost_without_a_relative_quality_floor(self):
        parsed = bench.parse_leaderboard(document(dataset()))
        profiles = {x["scene"]: x for x in bench.rank(parsed)}
        self.assertEqual(profiles["daily"]["candidates"][0]["model"], "gpt-6.2-sol")
        # The numerically best utility differs by less than its uncertainty;
        # the published decision rule chooses the cheaper overlapping option.
        self.assertEqual(profiles["hard"]["highest_utility_pair"]["model"], "gpt-6.2-astra")
        self.assertEqual(profiles["hard"]["candidates"][0]["model"], "gpt-6.2-sol")
        self.assertEqual(profiles["background"]["candidates"][0]["model"], "gpt-6.2-sol")
        # Overlapping quality intervals must not be treated as proven dominance.
        self.assertTrue(profiles["hard"]["candidates"][1]["quality_interval_overlaps_best"])
        self.assertNotIn("gpt-6.1-terra", {x["model"] for x in profiles["daily"]["candidates"]})
        self.assertEqual(profiles["daily"]["candidates"][0]["utility"], 62)

    def test_filters_exact_catalog_pairs_before_ranking(self):
        parsed = bench.parse_leaderboard(document(dataset()))
        catalog = {"gpt-6.2-sol": {"supported_reasoning_levels": [{"effort": "high"}]}}
        self.assertEqual({p["candidates"][0]["model"] for p in bench.rank(parsed, catalog)}, {"gpt-6.2-sol"})
        self.assertEqual(bench.rank(parsed, catalog, {("gpt-6.2-sol", "max")}), [])

    def test_failed_refresh_keeps_last_good_data_and_retries_after_five_minutes(self):
        opener = Opener(document(dataset()))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            self.assertEqual(bench.sync(opener=opener, now=1000)["status"], "updated")
            self.assertEqual(bench.sync(opener=opener, now=1100)["status"], "not_due")
            failure = Opener(error=urllib.error.URLError("secret-like diagnostic"))
            self.assertEqual(bench.sync(force=True, opener=failure, now=1200)["status"], "failed")
            cache = bench.read_cache()
            self.assertEqual(cache["fetched_at"], 1000)
            self.assertEqual(len(cache["benchmark"]["rows"]), 3)
            self.assertNotIn("secret-like", json.dumps(cache))
            self.assertEqual(bench.sync(opener=opener, now=1499)["status"], "not_due")
            self.assertEqual(bench.sync(opener=opener, now=1500)["status"], "updated")
            self.assertEqual(Path(bench.cache_path()).stat().st_mode & 0o777, 0o600)

    def test_concurrent_refresh_does_not_write_or_fetch(self):
        opener = Opener(document(dataset()))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            path = bench.cache_path()
            path.parent.mkdir(parents=True)
            with open(str(path) + ".lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.assertEqual(bench.sync(opener=opener, now=1000)["status"], "busy")
                self.assertEqual(opener.count, 0)
                self.assertIsNone(bench.read_cache())

    def test_terminal_cli_reports_unmeasured_efforts_and_separates_desktop_sources(self):
        parsed = bench.parse_leaderboard(document(dataset()))
        catalog = {"gpt-6.2-sol": {"supported_reasoning_levels": [{"effort": "high"}, {"effort": "medium"}]}}
        output = io.StringIO()
        with patch.object(bench, "read_cache", return_value={"benchmark": parsed}), \
                patch.object(TEAM, "cached_codex_model_catalog", return_value=catalog), \
                contextlib.redirect_stdout(output):
            TEAM.coding_benchmark_command(["status"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["unmeasured_catalog_pairs"], [{"model": "gpt-6.2-sol", "effort": "medium"}])
        self.assertEqual(result["computer_automation"]["status"], "not_measured_by_terminal_bench")
        self.assertTrue(result["catalog_verified"])

    def test_scheduled_refresh_never_fetches_radar(self):
        output = io.StringIO()
        with patch.object(TEAM, "sync_codex_radar_if_due") as radar, \
                patch.object(TEAM, "sync_terminal_bench", return_value={"status": "not_due"}), \
                patch.object(TEAM, "sync_extra_benchmarks", return_value={"modeldial": {"status": "not_due"}}), \
                contextlib.redirect_stdout(output):
            TEAM.recommendation_command(["sync"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "not_due")
        radar.assert_not_called()

    def test_separate_worker_scoring_is_reproducible_and_within_lead_ceiling(self):
        parsed = bench.parse_leaderboard(document([
            row("gpt-6.2-astra", "max", 75, 1, 4, 10),
            row("gpt-6.2-sol", "max", 70, 1, 1, 10),
            row("gpt-6-luna", "max", 30, 1, 0.2, 10),
        ]))
        snapshot = TEAM.calculate_recommendations(parsed, {}, 1234)
        hard = next(item for item in snapshot["recommendations"]["items"] if item["source_key"] == "hard")
        self.assertEqual((hard["model"], hard["effort"]), ("gpt-6.2-astra", "max"))
        self.assertEqual((hard["worker_model"], hard["worker_effort"]), ("gpt-6.2-sol", "max"))
        lead = hard["metrics"]["lead"]
        worker = hard["metrics"]["worker"]
        self.assertEqual(lead["candidates"][0]["utility"], round(75 - 0.25 * math.log2(4), 3))
        self.assertEqual(worker["candidates"][0]["utility"], 70)
        cfg = TEAM.recommendation_settings(hard["id"], team=True, cache=snapshot)
        policy = TEAM.model_policy(cfg, "codex", "native")
        self.assertEqual((policy["default_worker_model"], policy["default_worker_effort"]),
                         (hard["worker_model"], hard["worker_effort"]))
        for item in policy["task_recommendations"]:
            TEAM.validate_model_ceiling(cfg["model"], cfg["effort"], item["model"], item["effort"])
        # Launch must not silently replace a calculated worker with the latest
        # generation in the same tier, even if the lead is a different model.
        cfg["worker_model"] = "gpt-6.1-sol"
        with patch.object(TEAM, "latest_models_by_tier", return_value={3: "gpt-6.2-sol"}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            TEAM.launch("codex", ["--team-dry-run"], resolved=cfg)
        self.assertEqual(json.loads(output.getvalue())["config"]["worker_model"], "gpt-6.1-sol")
        # A worker cannot use Astra when the selected lead is Sol.
        pairs = TEAM.worker_pairs_for_lead(parsed, {"model": "gpt-6.2-sol", "effort": "max"})
        self.assertNotIn(("gpt-6.2-astra", "max"), pairs)

    def test_menu_snapshot_survives_a_later_prepare_and_missing_id_fails_closed(self):
        old = bench.parse_leaderboard(document(dataset()))
        newer = bench.parse_leaderboard(document([row("gpt-6.2-sol", "high", 61, 2, 1, 10),
                                                   row("gpt-6.2-astra", "max", 64, 2, 3, 15)]))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), \
                patch.object(TEAM, "cached_codex_model_catalog", return_value={}), \
                patch.object(bench, "read_cache", side_effect=[{"benchmark": old}, {"benchmark": newer}]):
            first = TEAM.prepare_recommendations(fetch_if_missing=False)
            item = first["recommendations"]["items"][0]
            second = TEAM.prepare_recommendations(fetch_if_missing=False)
            self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertEqual(second["history"][0]["model_policy"], first["model_policy"])
            saved = TEAM.recommendation_settings(item["id"], team=True, cache=second)
            self.assertEqual(saved["model"], item["model"])
            self.assertEqual(second["history"][0]["sources"]["terminal_bench"], old)
            self.assertEqual(saved["recommendation_method"], TEAM.RECOMMENDATION_METHOD)
            with self.assertRaisesRegex(RuntimeError, "expired or unavailable"):
                TEAM.recommendation_settings("missing", team=True, cache=second)


if __name__ == "__main__":
    unittest.main()
