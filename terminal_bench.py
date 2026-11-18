"""Read the public Terminal-Bench leaderboard as comparable coding evidence."""

import json
import http.client
import fcntl
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser


SOURCE = "https://www.tbench.ai/?version=4.1"
BOARD = "4-1-0"
PACKAGE = "terminal-bench/terminal-bench"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REFRESH_SECONDS = 24 * 60 * 60
RETRY_SECONDS = 5 * 60

# A coefficient is the number of quality points a user would trade for a
# doubling of cost or time. They are initial preferences, not fitted weights.
PROFILES = (
    ("daily", "日常开发", 2.0, 1.5),
    ("hard", "难题攻坚", 0.25, 0.25),
    ("background", "后台自动化", 8.0, 0.5),
)
WORKER_PROFILES = (
    ("daily", "日常开发", 5.0, 2.0),
    ("hard", "难题攻坚", 2.0, 1.5),
    ("background", "后台自动化", 12.0, 1.0),
)


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_script = False
        self.current = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.in_script = True
            self.current = []

    def handle_data(self, data):
        if self.in_script:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.in_script:
            self.scripts.append("".join(self.current))
            self.in_script = False


def number(value, low, high=None):
    return (type(value) in (int, float) and math.isfinite(value) and value >= low
            and (high is None or value <= high))


def parse_leaderboard(document):
    """Find the versioned public board in Next's serialized page data.

    The displayed table rounds aggregate cost and omits time; its serialized
    leaderboard includes total cost, trial count and average trial duration.
    Reject changed or ambiguous formats instead of guessing a benchmark version.
    """
    parser = Scripts()
    parser.feed(document)
    parser.close()
    chunks = []
    for script in parser.scripts:
        match = re.fullmatch(r"\s*self\.__next_f\.push\((\[.*\])\)\s*;?\s*", script, re.DOTALL)
        if not match:
            continue
        try:
            chunk = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(chunk, list) and len(chunk) > 1 and isinstance(chunk[1], str):
            chunks.append(chunk[1])
    content = "".join(chunks)
    decoder = json.JSONDecoder()
    boards = []
    for match in re.finditer(r'\{"leaderboard":\{', content):
        try:
            candidate, _ = decoder.raw_decode(content, match.start())
        except ValueError:
            continue
        board = candidate.get("leaderboard") if isinstance(candidate, dict) else None
        if (isinstance(board, dict) and board.get("package") == PACKAGE
                and board.get("name") == BOARD and isinstance(candidate.get("rows"), list)):
            boards.append(candidate)
    if len(boards) != 1:
        raise ValueError("Terminal-Bench 4.1 leaderboard is missing or ambiguous")

    rows = []
    seen = set()
    for entry in boards[0]["rows"]:
        if not isinstance(entry, dict) or entry.get("status") != "display":
            continue
        meta, metrics = entry.get("metadata"), entry.get("metrics")
        if not isinstance(meta, dict) or not isinstance(metrics, dict):
            continue
        agent = meta.get("agent_display")
        model = meta.get("model_display")
        if not isinstance(agent, dict) or agent.get("label") != "Codex" or not isinstance(model, dict):
            continue
        url = model.get("url")
        if not isinstance(url, str):
            continue
        parsed = urllib.parse.urlsplit(url)
        slug = parsed.path.rsplit("/", 1)[-1].lower()
        effort = meta.get("reasoning_effort")
        if (parsed.scheme != "https" or parsed.hostname != "developers.openai.com"
                or not re.fullmatch(r"gpt-[a-z0-9][a-z0-9.-]{0,55}", slug)
                or not isinstance(effort, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", effort)):
            continue
        quality, count = metrics.get("accuracy"), metrics.get("n_trials")
        total_cost, seconds = metrics.get("total_cost_usd"), metrics.get("avg_trial_duration_sec")
        ci = metrics.get("accuracy_ci95_half_width")
        if (not number(quality, 0, 100) or type(count) is not int or count < 1
                or not number(total_cost, 0) or total_cost == 0
                or not number(seconds, 0) or seconds == 0 or not number(ci, 0, 100)):
            continue
        pair = (slug, effort)
        if pair in seen:
            raise ValueError("Terminal-Bench has duplicate Codex model/effort rows")
        seen.add(pair)
        rows.append({"model": slug, "effort": effort, "quality": quality, "ci95": ci,
                     "cost_usd_per_task": total_cost / count, "minutes_per_task": seconds / 60,
                     "n_trials": count, "run_date": meta.get("date"),
                     "updated_at": entry.get("updated_at")})
    if len(rows) < 2:
        raise ValueError("Terminal-Bench has fewer than two complete Codex rows")
    return {"source": SOURCE, "board": BOARD, "harness": "Codex", "rows": rows}


def rank(benchmark, catalog=None, allowed_pairs=None, role="lead"):
    """Rank complete candidates; an overlapping quality CI stays on the frontier."""
    rows = []
    for row in benchmark.get("rows", []):
        if not isinstance(row, dict):
            continue
        model, effort = row.get("model"), row.get("effort")
        q, ci, cost, minutes = (row.get(k) for k in
                                ("quality", "ci95", "cost_usd_per_task", "minutes_per_task"))
        if (not isinstance(model, str) or not isinstance(effort, str)
                or not number(q, 0, 100) or not number(ci, 0, 100)
                or not number(cost, 0) or cost == 0 or not number(minutes, 0) or minutes == 0):
            continue
        if allowed_pairs is not None and (model, effort) not in allowed_pairs:
            continue
        if catalog:
            metadata = catalog.get(model)
            if not isinstance(metadata, dict):
                continue
            levels = metadata.get("supported_reasoning_levels")
            if isinstance(levels, list) and effort not in {
                    item.get("effort") for item in levels if isinstance(item, dict)}:
                continue
        rows.append(row)
    if not rows:
        return []

    def confidently_dominates(a, b):
        return (a["quality"] - a["ci95"] >= b["quality"] + b["ci95"]
                and a["cost_usd_per_task"] <= b["cost_usd_per_task"]
                and a["minutes_per_task"] <= b["minutes_per_task"]
                and (a["quality"] - a["ci95"] > b["quality"] + b["ci95"]
                     or a["cost_usd_per_task"] < b["cost_usd_per_task"]
                     or a["minutes_per_task"] < b["minutes_per_task"]))

    frontier = [a for a in rows if not any(confidently_dominates(b, a) for b in rows if b is not a)]
    if role not in ("lead", "worker"):
        raise ValueError("Unknown recommendation role")
    results = []
    profiles = PROFILES if role == "lead" else WORKER_PROFILES
    for key, label, cost_weight, time_weight in profiles:
        def utility(row):
            return (row["quality"] - cost_weight * math.log2(row["cost_usd_per_task"])
                    - time_weight * math.log2(row["minutes_per_task"] / 10))

        ordered = sorted(frontier, key=lambda row: (-utility(row), -row["quality"],
                                                    row["cost_usd_per_task"], row["minutes_per_task"],
                                                    row["model"], row["effort"]))
        score_best = ordered[0]
        # Cost breaks close calls when the score intervals overlap. This is a
        # conservative decision rule, not a statistical equivalence test:
        # benchmark CIs do not include uncertainty in cost, time, or weights.
        close = [row for row in ordered if utility(row) + row["ci95"] >=
                 utility(score_best) - score_best["ci95"]]
        best = min(close, key=lambda row: (row["cost_usd_per_task"], row["minutes_per_task"],
                                           -utility(row), row["model"], row["effort"]))
        ordered = [best] + [row for row in ordered if row is not best]
        choices = []
        for row in ordered[:4]:
            choices.append(dict(row, utility=round(utility(row), 3),
                                quality_interval_overlaps_best=(
                                    row["quality"] - row["ci95"] <= best["quality"] + best["ci95"]
                                    and best["quality"] - best["ci95"] <= row["quality"] + row["ci95"])))
        results.append({"scene": key, "label": label, "role": role, "cost_points_per_doubling": cost_weight,
                        "time_points_per_doubling": time_weight,
                        "decision_rule": "lowest_cost_if_utility_intervals_overlap",
                        "highest_utility_pair": {"model": score_best["model"], "effort": score_best["effort"]},
                        "candidates": choices})
    return results


def cache_path():
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "agent-team/terminal-bench.json"


def read_cache(path=None):
    try:
        cache = json.loads((path or cache_path()).read_text())
    except (OSError, ValueError, TypeError):
        return None
    return cache if isinstance(cache, dict) and cache.get("version") == 1 else None


def write_cache(cache, path=None):
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sync(force=False, opener=None, now=None):
    """Serialize manual and scheduled refreshes so a failure cannot erase a success."""
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        try:
            return sync_locked(force=force, opener=opener, now=now)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def sync_locked(force=False, opener=None, now=None):
    now = time.time() if now is None else now
    cache = read_cache()
    last_attempt = cache.get("last_attempt_at", 0) if cache else 0
    if not number(last_attempt, 0):
        last_attempt = 0
    delay = RETRY_SECONDS if not cache or cache.get("last_error") else REFRESH_SECONDS
    if not force and now - last_attempt < delay:
        return {"status": "not_due"}
    try:
        request = urllib.request.Request(SOURCE, headers={"User-Agent": "agent-team/1 benchmark-evidence"})
        client = opener or urllib.request.build_opener()
        for attempt in range(2):
            try:
                with client.open(request, timeout=12) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                break
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
                if attempt == 1:
                    raise
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Terminal-Bench response exceeds 8 MiB")
        benchmark = parse_leaderboard(body.decode("utf-8"))
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError,
            UnicodeError, ValueError, TypeError) as exc:
        # Do not erase the last good data or emit URLs, credentials or response bodies.
        error = type(exc).__name__
        if isinstance(exc, urllib.error.HTTPError):
            error += ":" + str(exc.code)
        cache = dict(cache or {"version": 1}, last_attempt_at=now, last_error=error)
        write_cache(cache)
        return {"status": "failed", "error": error}
    cache = {"version": 1, "source": SOURCE, "fetched_at": now, "last_attempt_at": now,
             "last_error": None, "benchmark": benchmark}
    write_cache(cache)
    return {"status": "updated", "fetched_at": now, "rows": len(benchmark["rows"])}
