import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import benchmark_sources as sources


def modeldial_document():
    entries, metrics = [], {}
    for model, effort, backend in (("gpt-6.2-sol", "high", 90), ("gpt-6-luna", "medium", 35)):
        key = "codex-local:" + model + ":" + effort
        entries.append({"candidate_id": key, "connection_id": "codex-local", "model_id": model,
                        "reasoning_effort": effort, "backend_score": backend, "frontend_score": 70,
                        "knowledge_score": 65, "score_policy": "five-axis"})
        metrics[key] = {axis: {"estimated_api_cost_usd": 0.5, "elapsed_ms": 60000,
                               "cost_coverage": "complete"}
                        for axis in ("backend", "frontend", "knowledge")}
    return {"snapshot": {"batch_id": "batch-1", "published_at": "2026-11-18", "entries": entries},
            "publicationKey": "sha256:test", "metrics": metrics}


def cua_row(model, effort, quality, cost, seconds, **extra):
    return {"model": model, "model_metadata": {"access_source":
            "https://developers.openai.com/api/docs/models/" + model.lower().replace(" ", "-")},
            "effort": effort, "performance": quality / 100, "cost_usd": cost,
            "time_per_task_sec": seconds, "run_id": "run-" + effort, "row_type": "evaluation",
            "variant": "Codex", "provisional": False, "ablation": False, **extra}


def cua_document(rows):
    return '<script type="application/json" data-results-data>' + json.dumps(rows) + '</script>'


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class Opener:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.calls = data, error, 0

    def open(self, request, timeout):
        self.calls += 1
        if self.error: raise self.error
        return Response(self.data.encode())


class BenchmarkSourceTests(unittest.TestCase):
    def test_modeldial_joins_exact_codex_efforts_and_complete_axes(self):
        doc = modeldial_document()
        extra = dict(doc["snapshot"]["entries"][0], connection_id="other")
        doc["snapshot"]["entries"].append(extra)
        doc["metrics"]["codex-local:gpt-6-luna:medium"]["frontend"]["cost_coverage"] = "partial"
        parsed = sources.parse_modeldial(json.dumps(doc))
        self.assertEqual(len(parsed["rows"]), 2)
        luna = next(row for row in parsed["rows"] if row["model"] == "gpt-6-luna")
        self.assertNotIn("frontend", luna["axes"])
        self.assertEqual(parsed["harness"], "codex-local")
        self.assertEqual(parsed["license"], "CC BY 4.0")
        doc["snapshot"]["entries"].append(doc["snapshot"]["entries"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            sources.parse_modeldial(json.dumps(doc))

    def test_desktop_rejects_wrong_agent_provisional_and_invalid_model_identity(self):
        rows = [cua_row("GPT-6.1 Terra", "medium", 70, 0.5, 60),
                cua_row("GPT-6.1 Terra", "high", 75, 0.8, 90),
                cua_row("GPT-5.6 Luna", "low", 50, 0.1, 40, variant="Direct API")]
        parsed = sources.parse_cua(cua_document(rows), sources.CUA_OSWORLD, "osworld-50", 50)
        self.assertEqual([(x["model"], x["effort"]) for x in parsed["rows"]],
                         [("gpt-6.1-terra", "medium"), ("gpt-6.1-terra", "high")])
        self.assertEqual(parsed["rows"][0]["minutes"], 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            sources.parse_cua(cua_document(rows + [rows[0]]), sources.CUA_OSWORLD, "osworld-50", 50)
        rows[1]["provisional"] = True
        with self.assertRaisesRegex(ValueError, "fewer than two"):
            sources.parse_cua(cua_document(rows), sources.CUA_OSWORLD, "osworld-50", 50)

    def test_failed_refresh_preserves_success_and_retries_without_overlapping(self):
        opener = Opener(json.dumps(modeldial_document()))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            self.assertEqual(sources.sync("modeldial", opener=opener, now=1000)["status"], "updated")
            bad = Opener(error=urllib.error.URLError("private diagnostic"))
            self.assertEqual(sources.sync("modeldial", opener=bad, force=True, now=1100)["status"], "failed")
            saved = sources.read_cache("modeldial")
            self.assertEqual(saved["fetched_at"], 1000)
            self.assertNotIn("private diagnostic", json.dumps(saved))
            self.assertEqual(sources.sync("modeldial", opener=opener, now=1399)["status"], "not_due")
            lock_path = str(sources.cache_path("modeldial")) + ".lock"
            with open(lock_path, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.assertEqual(sources.sync("modeldial", opener=opener, now=1400)["status"], "busy")
            self.assertEqual(sources.sync("modeldial", opener=opener, now=1400)["status"], "updated")
            self.assertEqual(Path(sources.cache_path("modeldial")).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
