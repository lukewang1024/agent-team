"""Reproducible scenario rankings over separately measured benchmark sources."""

import math

import terminal_bench


def supported(row, catalog, allowed_pairs):
    pair = (row["model"], row["effort"])
    if allowed_pairs is not None and pair not in allowed_pairs:
        return False
    if catalog:
        metadata = catalog.get(pair[0])
        if not isinstance(metadata, dict):
            return False
        levels = metadata.get("supported_reasoning_levels")
        if isinstance(levels, list) and pair[1] not in {
                item.get("effort") for item in levels if isinstance(item, dict)}:
            return False
    return True


def choose(rows, cost_weight, time_weight, close_points):
    if not rows:
        return None
    # Scores and resources all belong to the same scene and source set.
    frontier = [a for a in rows if not any(
        b["quality"] >= a["quality"] and b["cost_proxy"] <= a["cost_proxy"]
        and b["minutes_proxy"] <= a["minutes_proxy"]
        and (b["quality"] > a["quality"] or b["cost_proxy"] < a["cost_proxy"]
             or b["minutes_proxy"] < a["minutes_proxy"])
        for b in rows if b is not a)]

    def utility(row):
        return (row["quality"] - cost_weight * math.log2(row["cost_proxy"])
                - time_weight * math.log2(row["minutes_proxy"] / 10))

    ordered = sorted(frontier, key=lambda row: (-utility(row), -row["quality"],
                                                row["cost_proxy"], row["minutes_proxy"],
                                                row["model"], row["effort"]))
    highest = ordered[0]
    close = [row for row in ordered if utility(row) >= utility(highest) - close_points]
    chosen = min(close, key=lambda row: (row["cost_proxy"], row["minutes_proxy"],
                                         -utility(row), row["model"], row["effort"]))
    ordered = [chosen] + [row for row in ordered if row is not chosen]
    return {"cost_points_per_doubling": cost_weight, "time_points_per_doubling": time_weight,
            "close_points": close_points, "decision_rule": "lowest_cost_within_fixed_utility_margin",
            "candidate_count": len(rows), "model_count": len({row["model"] for row in rows}),
            "highest_utility_pair": {"model": highest["model"], "effort": highest["effort"]},
            "candidates": [dict(row, utility=round(utility(row), 3)) for row in ordered[:5]]}


def modeldial_rank(benchmark, profile, role, close_points, catalog=None, allowed_pairs=None):
    axes = profile["axes"]
    rows = []
    for entry in benchmark.get("rows", []):
        if not supported(entry, catalog, allowed_pairs):
            continue
        measured = entry.get("axes", {})
        if not all(axis in measured for axis in axes):
            continue
        quality = sum(weight * measured[axis]["quality"] for axis, weight in axes.items())
        # Each axis contains a whole evaluation's recorded consumption. This is
        # a geometric proxy for the selected task mix, not a per-task bill.
        cost = math.exp(sum(weight * math.log(measured[axis]["cost_usd"]) for axis, weight in axes.items()))
        minutes = math.exp(sum(weight * math.log(measured[axis]["minutes"]) for axis, weight in axes.items()))
        rows.append({"model": entry["model"], "effort": entry["effort"], "quality": quality,
                     "cost_proxy": cost, "minutes_proxy": minutes,
                     "axes": {axis: measured[axis] for axis in axes}, "candidate_id": entry["candidate_id"]})
    settings = profile[role]
    result = choose(rows, settings["cost"], settings["time"], close_points)
    if result:
        result.update(scene=profile["id"], label=profile["label"], role=role,
                      source="modeldial", source_url=benchmark["source"], board=benchmark["board"],
                      axis_weights=axes, confidence="limited_private_evaluation_no_reported_interval")
    return result


def desktop_rank(first, second, profile, role, close_points, catalog=None, allowed_pairs=None):
    # Only exact Codex pairs measured on both task sets are compared. Each
    # source's utility has equal weight; missing measurements are never filled.
    other = {(r["model"], r["effort"]): r for r in second.get("rows", [])}
    rows = []
    for entry in first.get("rows", []):
        match = other.get((entry["model"], entry["effort"]))
        if not match or not supported(entry, catalog, allowed_pairs):
            continue
        rows.append({"model": entry["model"], "effort": entry["effort"],
                     "quality": (entry["quality"] + match["quality"]) / 2,
                     "cost_proxy": math.sqrt(entry["cost_usd"] * match["cost_usd"]),
                     "minutes_proxy": math.sqrt(entry["minutes"] * match["minutes"]),
                     "benchmarks": [{"source": first["source"], "board": first["board"], **entry},
                                    {"source": second["source"], "board": second["board"], **match}]})
    settings = profile[role]
    result = choose(rows, settings["cost"], settings["time"], close_points)
    if result:
        result.update(scene=profile["id"], label=profile["label"], role=role,
                      source="cua", source_url=first["source"],
                      boards=[first["board"], second["board"]],
                      confidence="limited_task_subsets_no_reported_interval")
    return result


def rank_scene(profile, sources, role, config, catalog=None, allowed_pairs=None):
    source = profile.get("worker_source", profile["source"]) if role == "worker" else profile["source"]
    if source == "modeldial" and isinstance(sources.get("modeldial"), dict):
        return modeldial_rank(sources["modeldial"], profile, role, config["modeldial_close_points"],
                              catalog, allowed_pairs)
    if source == "cua" and isinstance(sources.get("cua_osworld"), dict) and isinstance(sources.get("cua_osworld2"), dict):
        return desktop_rank(sources["cua_osworld"], sources["cua_osworld2"], profile, role,
                            config["desktop_close_points"], catalog, allowed_pairs)
    if source == "terminal_bench" and isinstance(sources.get("terminal_bench"), dict):
        ranked = terminal_bench.rank(sources["terminal_bench"], catalog, allowed_pairs, role=role)
        profile_result = next((entry for entry in ranked if entry["scene"] == profile["id"]), None)
        if profile_result:
            profile_result.update(source="terminal_bench", source_url=sources["terminal_bench"]["source"],
                                  board=sources["terminal_bench"]["board"],
                                  confidence="reported_quality_interval")
        return profile_result
    return None
