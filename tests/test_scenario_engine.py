import importlib.machinery
import importlib.util
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import scenario_engine as engine


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader('agent_team_multisource_test', str(ROOT / 'bin/agent-team'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
TEAM = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(TEAM)


def model_row(model, effort, quality, cost, minutes):
    axis = {"quality": quality, "cost_usd": cost, "minutes": minutes}
    return {"model": model, "effort": effort, "candidate_id": model + ':' + effort,
            "axes": {name: dict(axis) for name in ("backend", "frontend", "knowledge")}}


def desktop_row(model, effort, quality, cost, minutes):
    return {"model": model, "effort": effort, "quality": quality,
            "cost_usd": cost, "minutes": minutes, "run_id": model + ':' + effort, "task_count": 50}


def inputs():
    rows = [model_row("gpt-6.2-sol", "high", 95, 1, 8),
            model_row("gpt-6.2-sol", "medium", 90, .5, 5),
            model_row("gpt-6.1-terra", "xhigh", 78, .15, 3),
            model_row("gpt-6-luna", "medium", 24, .01, 1)]
    first = [desktop_row("gpt-6.1-terra", "medium", 82, .1, 2),
             desktop_row("gpt-6.1-terra", "high", 84, .2, 3),
             desktop_row("gpt-5.6-luna", "low", 70, .03, 4)]
    second = [desktop_row("gpt-6.1-terra", "medium", 60, .5, 10),
              desktop_row("gpt-6.1-terra", "high", 70, .8, 15)]
    terminal = [dict(model="gpt-6.2-sol", effort="max", quality=70, ci95=2,
                     cost_usd_per_task=1, minutes_per_task=12),
                dict(model="gpt-6.2-astra", effort="max", quality=70.5, ci95=2,
                     cost_usd_per_task=5, minutes_per_task=18)]
    return {"modeldial": {"source": "https://example.test/modeldial", "board": "m1", "rows": rows},
            "cua_osworld": {"source": "https://example.test/os1", "board": "os1", "rows": first},
            "cua_osworld2": {"source": "https://example.test/os2", "board": "os2", "rows": second},
            "terminal_bench": {"source": "https://example.test/terminal", "board": "tb", "rows": terminal}}


class ScenarioEngineTests(unittest.TestCase):
    def test_cost_time_and_quality_are_computed_and_small_margins_are_explicit(self):
        profile = {"id": "daily", "label": "daily", "axes": {"backend": 1.0},
                   "lead": {"cost": 2, "time": 1}}
        benchmark = inputs()["modeldial"]
        result = engine.modeldial_rank(benchmark, profile, "lead", 1.0)
        lead = result["candidates"][0]
        self.assertEqual((lead["model"], lead["effort"]), ("gpt-6.2-sol", "high"))
        self.assertEqual(lead["utility"], round(95 - math.log2(8 / 10), 3))
        self.assertEqual(result["decision_rule"], "lowest_cost_within_fixed_utility_margin")
        self.assertEqual(result["close_points"], 1)

    def test_desktop_only_compares_exact_pairs_present_on_both_task_sets(self):
        sources = inputs()
        profile = {"id": "computer", "label": "computer", "lead": {"cost": 4, "time": 2}}
        result = engine.desktop_rank(sources["cua_osworld"], sources["cua_osworld2"], profile, "lead", 1)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["model_count"], 1)
        self.assertTrue(all(r["model"] == "gpt-6.1-terra" for r in result["candidates"]))
        selected = result["candidates"][0]
        left, right = selected["benchmarks"]
        self.assertAlmostEqual(selected["quality"], (left["quality"] + right["quality"]) / 2)
        self.assertAlmostEqual(selected["cost_proxy"], math.sqrt(left["cost_usd"] * right["cost_usd"]))

    def test_four_scenes_use_distinct_sources_and_worker_ceiling(self):
        snapshot = TEAM.calculate_scenario_recommendations(inputs(), {})
        items = {item["source_key"]: item for item in snapshot["recommendations"]["items"]}
        self.assertEqual(set(items), {"daily", "computer", "hard", "background"})
        self.assertEqual((items["daily"]["model"], items["daily"]["effort"]), ("gpt-6.2-sol", "high"))
        self.assertEqual((items["daily"]["worker_model"], items["daily"]["worker_effort"]),
                         ("gpt-6.2-sol", "medium"))
        self.assertEqual((items["hard"]["model"], items["hard"]["effort"]), ("gpt-6.2-sol", "max"))
        self.assertEqual((items["background"]["model"], items["background"]["worker_model"]),
                         ("gpt-6.1-terra", "gpt-6-luna"))
        self.assertEqual(items["computer"]["metrics"]["lead"]["source"], "cua")
        self.assertEqual(items["hard"]["metrics"]["lead"]["source"], "terminal_bench")
        for item in items.values():
            self.assertEqual(item["label"], item["metrics"]["lead"]["label"] + " · "
                             + item["model"] + " / " + item["effort"])
            self.assertIn("source_url", item)
            self.assertIn("source", item["metrics"]["lead"])
            cfg = TEAM.recommendation_settings(item["id"], team=True, cache=snapshot)
            TEAM.validate_model_ceiling(cfg["model"], cfg["effort"], cfg["worker_model"], cfg["worker_effort"])
            policy = TEAM.model_policy(cfg, "codex", "native")
            self.assertIn((cfg["worker_model"], cfg["worker_effort"]),
                          {(choice["model"], choice["effort"]) for choice in policy["allowed_worker_settings"]})
            self.assertTrue(any(choice["model"] == cfg["worker_model"] and choice["effort"] == cfg["worker_effort"]
                                for choice in policy["task_recommendations"]))
            for suggestion in cfg["worker_recommendations"]:
                TEAM.validate_model_ceiling(cfg["model"], cfg["effort"], suggestion["model"], suggestion["effort"])

    def test_missing_source_is_not_silently_filled_from_another_benchmark(self):
        sources = inputs()
        del sources["cua_osworld2"]
        snapshot = TEAM.calculate_scenario_recommendations(sources, {})
        self.assertNotIn("computer", {x["source_key"] for x in snapshot["recommendations"]["items"]})
        self.assertIn("computer", {x["key"] for x in snapshot["unavailable_scenes"]})

    def test_catalog_filters_exact_model_and_effort_before_recommendation(self):
        catalog = {"gpt-6.2-sol": {"supported_reasoning_levels": [{"effort": "medium"}]}}
        snapshot = TEAM.calculate_scenario_recommendations(inputs(), catalog)
        self.assertTrue(snapshot["recommendations"]["items"])
        for item in snapshot["recommendations"]["items"]:
            self.assertEqual((item["model"], item["effort"]), ("gpt-6.2-sol", "medium"))
            self.assertEqual((item["worker_model"], item["worker_effort"]), ("gpt-6.2-sol", "medium"))
        self.assertIn("computer", {item["key"] for item in snapshot["unavailable_scenes"]})

    def test_hard_scene_falls_back_to_measured_terminal_worker_when_modeldial_is_missing(self):
        sources = inputs()
        del sources["modeldial"]
        snapshot = TEAM.calculate_scenario_recommendations(sources, {})
        item = next(item for item in snapshot["recommendations"]["items"] if item["source_key"] == "hard")
        self.assertEqual(item["metrics"]["worker"]["source"], "terminal_bench")
        self.assertIn("daily", {entry["key"] for entry in snapshot["unavailable_scenes"]})
        self.assertIn("background", {entry["key"] for entry in snapshot["unavailable_scenes"]})

    def test_mixed_refresh_keeps_independent_source_status_and_busy_is_not_reported_as_current(self):
        with patch.object(TEAM, "sync_terminal_bench", return_value={"status": "updated"}), \
                patch.object(TEAM, "sync_extra_benchmarks", return_value={"modeldial": {"status": "failed"}}):
            result = TEAM.sync_all_benchmarks(force=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"]["modeldial"]["status"], "failed")
        with patch.object(TEAM, "sync_terminal_bench", return_value={"status": "busy"}), \
                patch.object(TEAM, "sync_extra_benchmarks", return_value={"modeldial": {"status": "busy"}}):
            self.assertEqual(TEAM.sync_all_benchmarks()["status"], "busy")


if __name__ == "__main__":
    unittest.main()
