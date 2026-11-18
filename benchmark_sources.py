"""Fetch independently published model/effort measurements without model calls."""

import fcntl
from html.parser import HTMLParser
import http.client
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MODELDIAL = "https://modeldial.com/data/overview.json"
CUA_OSWORLD = "https://cuaspeedrun.com/dataset-osworld-energy50-representative.html"
CUA_OSWORLD2 = "https://cuaspeedrun.com/dataset-osworld2-k52.html"
LIMIT = 8 * 1024 * 1024
REFRESH_SECONDS = 24 * 60 * 60
RETRY_SECONDS = 5 * 60


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def score(value, maximum=100):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def valid_pair(model, effort):
    return (isinstance(model, str) and re.fullmatch(r"gpt-[a-z0-9][a-z0-9.-]{0,55}", model)
            and isinstance(effort, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", effort))


def parse_modeldial(raw):
    data = json.loads(raw)
    snap = data.get("snapshot") if isinstance(data, dict) else None
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if (not isinstance(snap, dict) or not isinstance(snap.get("batch_id"), str)
            or not isinstance(snap.get("published_at"), str) or not isinstance(snap.get("entries"), list)
            or not isinstance(metrics, dict)):
        raise ValueError("ModelDial overview format changed")
    rows, seen = [], set()
    for item in snap["entries"]:
        if not isinstance(item, dict) or item.get("connection_id") != "codex-local":
            continue
        model, effort = item.get("model_id"), item.get("reasoning_effort")
        if not valid_pair(model, effort) or item.get("candidate_id") != "codex-local:" + model + ":" + effort:
            continue
        if item.get("score_policy") != "five-axis":
            continue
        values = metrics.get(item["candidate_id"])
        if not isinstance(values, dict):
            continue
        axes = {}
        for axis in ("backend", "frontend", "knowledge"):
            measure = values.get(axis)
            quality = item.get(axis + "_score")
            if not isinstance(measure, dict):
                continue
            cost, elapsed = measure.get("estimated_api_cost_usd"), measure.get("elapsed_ms")
            if (not score(quality) or not positive(cost) or not positive(elapsed)
                    or measure.get("cost_coverage") != "complete"):
                continue
            axes[axis] = {"quality": quality, "cost_usd": cost, "minutes": elapsed / 60000,
                          "elapsed_basis": measure.get("elapsed_basis"),
                          "result_id": measure.get("result_id"),
                          "run_manifest_sha256": measure.get("run_manifest_sha256")}
        if not axes:
            continue
        pair = (model, effort)
        if pair in seen:
            raise ValueError("ModelDial has duplicate Codex model/effort pairs")
        seen.add(pair)
        rows.append({"model": model, "effort": effort, "axes": axes,
                     "candidate_id": item["candidate_id"]})
    if len(rows) < 2:
        raise ValueError("ModelDial has fewer than two comparable Codex configurations")
    return {"source": MODELDIAL, "board": snap["batch_id"], "published_at": snap["published_at"],
            "publication_key": data.get("publicationKey"), "harness": "codex-local",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "rows": rows}


class ResultsScript(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.chunks = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and any(key == "data-results-data" for key, _ in attrs):
            self.inside = True
            self.chunks = []

    def handle_data(self, data):
        if self.inside:
            self.chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.inside:
            self.documents.append("".join(self.chunks))
            self.inside = False


def parse_cua(raw, source, board, task_count):
    parser = ResultsScript()
    parser.feed(raw)
    parser.close()
    if len(parser.documents) != 1:
        raise ValueError("CUA Speedrun leaderboard is missing or ambiguous")
    data = json.loads(parser.documents[0])
    if not isinstance(data, list):
        raise ValueError("CUA Speedrun leaderboard is not a list")
    rows, seen = [], set()
    for item in data:
        if (not isinstance(item, dict) or item.get("variant") != "Codex"
                or item.get("row_type") != "evaluation" or item.get("ablation") is not False
                or item.get("provisional") is not False):
            continue
        metadata = item.get("model_metadata")
        url = metadata.get("access_source") if isinstance(metadata, dict) else None
        if not isinstance(url, str):
            continue
        parsed = urllib.parse.urlsplit(url)
        model = parsed.path.rsplit("/", 1)[-1].lower()
        display = re.sub(r"\s+", "-", str(item.get("model", "")).lower())
        effort = item.get("effort")
        if (parsed.scheme != "https" or parsed.hostname != "developers.openai.com"
                or not valid_pair(model, effort) or display != model
                or not isinstance(item.get("run_id"), str) or not item["run_id"]):
            continue
        quality, cost, seconds = item.get("performance"), item.get("cost_usd"), item.get("time_per_task_sec")
        if not score(quality, 1) or not positive(cost) or not positive(seconds):
            continue
        pair = (model, effort)
        if pair in seen:
            raise ValueError("CUA Speedrun has duplicate Codex model/effort pairs")
        seen.add(pair)
        rows.append({"model": model, "effort": effort, "quality": 100 * quality,
                     "cost_usd": cost, "minutes": seconds / 60, "run_id": item["run_id"],
                     "cost_label": item.get("cost_label"), "task_count": task_count})
    if len(rows) < 2:
        raise ValueError("CUA Speedrun has fewer than two comparable Codex configurations")
    return {"source": source, "board": board, "harness": "Codex", "task_count": task_count, "rows": rows}


SOURCES = {
    "modeldial": (MODELDIAL, lambda raw: parse_modeldial(raw)),
    "cua_osworld": (CUA_OSWORLD, lambda raw: parse_cua(raw, CUA_OSWORLD, "osworld-50", 50)),
    "cua_osworld2": (CUA_OSWORLD2, lambda raw: parse_cua(raw, CUA_OSWORLD2, "osworld2-52", 52)),
}


def cache_path(name):
    if name not in SOURCES:
        raise ValueError("Unknown benchmark source")
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "agent-team" / (name + ".json")


def read_cache(name):
    try:
        data = json.loads(cache_path(name).read_text())
        return data if isinstance(data, dict) and data.get("version") == 1 else None
    except (OSError, ValueError, TypeError):
        return None


def write_cache(name, data):
    path = cache_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sync(name, force=False, opener=None, now=None):
    url, parser = SOURCES[name]
    now = time.time() if now is None else now
    path = cache_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        old = read_cache(name)
        last = old.get("last_attempt_at", 0) if old else 0
        last = last if score(last, maximum=1e20) else 0
        delay = RETRY_SECONDS if not old or old.get("last_error") else REFRESH_SECONDS
        if not force and now - last < delay:
            return {"status": "not_due"}
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "agent-team/1 benchmark-evidence"})
            client = opener or urllib.request.build_opener()
            with client.open(request, timeout=15) as response:
                body = response.read(LIMIT + 1)
            if len(body) > LIMIT:
                raise ValueError("Benchmark response exceeds 8 MiB")
            benchmark = parser(body.decode("utf-8"))
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError,
                UnicodeError, ValueError, TypeError, KeyError) as exc:
            error = type(exc).__name__
            if isinstance(exc, urllib.error.HTTPError):
                error += ":" + str(exc.code)
            write_cache(name, dict(old or {"version": 1}, last_attempt_at=now, last_error=error))
            return {"status": "failed", "error": error}
        write_cache(name, {"version": 1, "source": url, "fetched_at": now,
                           "last_attempt_at": now, "last_error": None, "benchmark": benchmark})
        return {"status": "updated", "fetched_at": now, "rows": len(benchmark["rows"])}
