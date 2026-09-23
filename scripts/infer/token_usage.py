#!/usr/bin/env python3
"""Token usage per task, run, evaluation, and harness, for cost comparisons.

Normalized fields (both harnesses):
  input_tokens         all prompt tokens, including cached ones
  cached_input_tokens  prompt tokens served from the prefix cache (None if not reported)
  uncached_input_tokens
  output_tokens        all generated tokens, including reasoning
  reasoning_tokens     subset of output_tokens
Sources: Codex native session `token_usage_record` (one per response; survives
timeouts), else its cumulative `token_count`, else stdout `turn.completed`.
OpenCode session database assistant messages (all agents, including subagents
such as `explore` that never appear in stdout), else stdout `step_finish`. OpenCode reports `input` without cached tokens and `output`
without reasoning; both are added back here.
"""

import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "patcheval/datasets/patcheval_verified.json"
FIELDS = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens", "reasoning_tokens")
SERVER_COUNTERS = ("prompt_tokens_total", "prompt_tokens_cached_total", "generation_tokens_total",
                   "request_success_total", "prefix_cache_hits_total", "prefix_cache_queries_total",
                   "spec_decode_num_draft_tokens_total", "spec_decode_num_accepted_tokens_total",
                   "num_preemptions_total")


def _lines(path):
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _record(requests, input_tokens, cached, output, reasoning, max_input, source, final, **extra):
    return {"requests": requests, "input_tokens": input_tokens, "cached_input_tokens": cached,
            "uncached_input_tokens": None if cached is None else input_tokens - cached,
            "output_tokens": output, "reasoning_tokens": reasoning, "max_request_input_tokens": max_input,
            "source": source, "final_record": final, **extra}


def codex_usage(trajectory):
    """Codex 0.155: per-response token_usage_record in native sessions (input includes cached;
    output includes reasoning), cross-checked with the cumulative token_count events."""
    trajectory = Path(trajectory)
    stdout = trajectory / "stdout.jsonl"
    finals = [e["usage"] for e in _lines(stdout) if e.get("type") == "turn.completed"] if stdout.is_file() else []
    responses, cumulative = {}, None
    for session in sorted((trajectory / "native").rglob("*.jsonl")) if (trajectory / "native").is_dir() else []:
        for event in _lines(session):
            payload = event.get("payload") or {}
            if event.get("type") == "token_usage_record" and payload.get("usage"):
                key = payload.get("response_id") or f"{session.name}:{event.get('ordinal')}"
                responses[key] = payload["usage"]
            elif event.get("type") == "event_msg" and payload.get("type") == "token_count":
                total = (payload.get("info") or {}).get("total_token_usage")
                if total:
                    cumulative = total
    if responses:
        usages = list(responses.values())
        record = _record(len(usages), sum(u.get("input_tokens", 0) for u in usages),
                         sum(u.get("cached_input_tokens", 0) for u in usages),
                         sum(u.get("output_tokens", 0) for u in usages),
                         sum(u.get("reasoning_output_tokens", 0) for u in usages),
                         max(u.get("input_tokens", 0) for u in usages), "codex_native", bool(finals),
                         cache_write_tokens=sum(u.get("cache_write_input_tokens", 0) for u in usages))
        if cumulative and finals:
            # A killed agent can write a response's record before its token_count
            # mirror, so the cumulative counter is only comparable after a clean finish.
            record["consistency"] = {"token_count_total_matches": all(
                cumulative.get(a, 0) == record[b] for a, b in (("input_tokens", "input_tokens"),
                                                              ("output_tokens", "output_tokens")))}
        return record
    usage = cumulative or (finals[-1] if finals else None)
    if usage is None:
        return None
    return _record(None, usage.get("input_tokens", 0), usage.get("cached_input_tokens", 0),
                   usage.get("output_tokens", 0), usage.get("reasoning_output_tokens", 0), None,
                   "codex_native_cumulative" if cumulative else "codex_stdout", bool(finals),
                   cache_write_tokens=usage.get("cache_write_input_tokens", 0))


def _opencode_messages(native):
    """Assistant-message token dicts from an archived OpenCode database (read from a copy)."""
    database = native / "opencode.db"
    if not database.is_file():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        for suffix in ("", "-wal", "-shm"):
            source = native / f"opencode.db{suffix}"
            if source.is_file():
                shutil.copy2(source, Path(tmp) / source.name)
        connection = sqlite3.connect(Path(tmp) / "opencode.db")
        try:
            rows = connection.execute("select data from message").fetchall()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
    messages = []
    for (data,) in rows:
        try:
            message = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            continue
        tokens = message.get("tokens") or {}
        cache = tokens.get("cache") or {}
        # Skip the empty placeholder of a response still in flight when the agent was stopped.
        used = any(tokens.get(k) for k in ("input", "output", "reasoning")) or cache.get("read") or cache.get("write")
        if message.get("role") == "assistant" and used:
            messages.append((message.get("agent") or message.get("mode") or "unknown", message["tokens"],
                             message.get("finish")))
    return messages


# OpenCode 1.18.31 agents whose model calls do not appear in `opencode run` stdout.
HIDDEN_FROM_STDOUT = {"explore", "general", "title", "summary"}


def opencode_usage(trajectory, cache_reported=False):
    """OpenCode 1.18: `input` excludes cache reads/writes and `output` excludes reasoning.

    Cached tokens are only known when the server returned prompt_tokens_details
    (vLLM --enable-prompt-tokens-details); otherwise they are None, not 0.
    """
    trajectory = Path(trajectory)
    messages = _opencode_messages(trajectory / "native")
    source = "opencode_db"
    stdout = trajectory / "stdout.jsonl"
    steps = [(None, e["part"]["tokens"], e["part"].get("reason")) for e in _lines(stdout)
             if e.get("type") == "step_finish" and (e.get("part") or {}).get("tokens")] if stdout.is_file() else []
    if not messages:
        messages, source = steps, "opencode_stdout"
    if not messages:
        return None
    by_agent, largest = {}, 0
    for agent, tokens, _ in messages:
        cache = tokens.get("cache") or {}
        prompt = tokens.get("input", 0) + cache.get("read", 0) + cache.get("write", 0)
        largest = max(largest, prompt)
        totals = by_agent.setdefault(agent or "build", {"requests": 0, "input_tokens": 0, "cache_read": 0,
                                                        "cache_write": 0, "output_tokens": 0,
                                                        "reasoning_tokens": 0})
        totals["requests"] += 1
        totals["input_tokens"] += prompt
        totals["cache_read"] += cache.get("read", 0)
        totals["cache_write"] += cache.get("write", 0)
        totals["output_tokens"] += tokens.get("output", 0) + tokens.get("reasoning", 0)
        totals["reasoning_tokens"] += tokens.get("reasoning", 0)
    total = {key: sum(a[key] for a in by_agent.values()) for key in next(iter(by_agent.values()))}
    cached = total["cache_read"] + total["cache_write"]
    known = cache_reported or cached > 0
    final = bool(steps) and steps[-1][2] == "stop"
    record = _record(total["requests"], total["input_tokens"], cached if known else None,
                     total["output_tokens"], total["reasoning_tokens"], largest, source, final,
                     cache_write_tokens=total["cache_write"] if known else None, by_agent=by_agent)
    record["subagent_requests"] = sum(a["requests"] for name, a in by_agent.items() if name in HIDDEN_FROM_STDOUT)
    if source == "opencode_db" and final:
        # stdout streams only the primary agent's steps (subagents such as `explore`
        # are recorded in the database alone) and can miss the last events of a
        # stopped agent, so compare primary-agent totals after a clean finish.
        in_stdout = sum(t.get("input", 0) + t.get("output", 0) + t.get("reasoning", 0) for _, t, _ in steps)
        in_db = sum(t.get("input", 0) + t.get("output", 0) + t.get("reasoning", 0)
                    for a, t, _ in messages if a not in HIDDEN_FROM_STDOUT)
        record["consistency"] = {"stdout_matches_primary_agents": in_stdout == in_db}
    return record


def task_usage(trajectory, harness, cache_reported=False):
    if harness == "codex":
        return codex_usage(trajectory)
    if harness == "opencode":
        return opencode_usage(trajectory, cache_reported)
    return None


def _invocation(path):
    for candidate in (Path(path), *Path(path).parents):
        if (candidate / "resolved.yaml").is_file():
            return candidate
    return None


def _harness(run_dir):
    invocation = _invocation(run_dir)
    if invocation is None:
        return None
    match = re.search(r"^harness:\n(?:  .*\n)*?  name: (\S+)", (invocation / "resolved.yaml").read_text(), re.M)
    return match.group(1) if match else None


def cache_reported(run_dir):
    """Whether the serving run recorded --enable-prompt-tokens-details (Chat Completions cache counts)."""
    invocation = _invocation(run_dir)
    server = invocation / "server.json" if invocation else None
    if server is None or not server.is_file():
        return False
    return "--enable-prompt-tokens-details" in (json.loads(server.read_text()).get("argv") or [])


def languages(dataset=DATASET):
    return {row["cve_id"]: row.get("programming_language") or "unknown"
            for row in json.loads(Path(dataset).read_text(encoding="utf-8"))}


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)] if ordered else None


def totals(rows):
    """Sum usage rows; cached fields stay None if any row lacks them (reported in cache_unknown_tasks)."""
    measured = [r for r in rows if r.get("usage")]
    result = {"tasks": len(rows), "tasks_with_usage": len(measured),
              "tasks_incomplete": sum(not r.get("complete") for r in measured),
              "requests": sum(r["usage"].get("requests") or 0 for r in measured)}
    for field in FIELDS:
        values = [r["usage"].get(field) for r in measured]
        result[field] = None if any(v is None for v in values) else sum(values)
    result["cache_unknown_tasks"] = sum(r["usage"].get("cached_input_tokens") is None for r in measured)
    result["total_tokens"] = (result["input_tokens"] or 0) + (result["output_tokens"] or 0)
    for field in ("input_tokens", "output_tokens"):
        values = [r["usage"][field] for r in measured]
        result[f"per_task_{field}"] = {"mean": statistics.fmean(values) if values else None,
                                       "median": statistics.median(values) if values else None,
                                       "p90": _percentile(values, 0.9), "max": max(values) if values else None}
    return result


def _grouped(rows, key):
    groups = {}
    for row in rows:
        groups.setdefault(str(row.get(key)), []).append(row)
    return {name: totals(members) for name, members in sorted(groups.items())}


def run_usage(run_dir, harness=None, dataset=DATASET, write=True):
    """Per-task usage for one generation run; writes token_usage.jsonl and token_usage_summary.json."""
    run_dir = Path(run_dir)
    harness = harness or _harness(run_dir)
    reported = cache_reported(run_dir)
    language = languages(dataset)
    rows = []
    for result in _lines(run_dir / "results.jsonl"):
        trajectory = result.get("trajectory_path")
        if trajectory and not Path(trajectory).is_absolute():
            trajectory = run_dir / trajectory
        usage = task_usage(trajectory, harness, reported) if trajectory and Path(trajectory).is_dir() else None
        rows.append({"cve": result["cve"], "language": language.get(result["cve"], "unknown"),
                     "status": result.get("status"), "timed_out": bool(result.get("timed_out")),
                     "duration_s": result.get("duration_s"),
                     "complete": bool(usage) and not result.get("timed_out") and result.get("agent_exit_code") == 0,
                     "usage": usage})
    summary = {"run": str(run_dir), "harness": harness, "cache_split_reported_by_server": reported,
               **totals(rows), "per_language": _grouped(rows, "language"), "per_status": _grouped(rows, "status"),
               "consistency_mismatches": sum(1 for r in rows if r["usage"] and
                                             not all((r["usage"].get("consistency") or {}).values()))}
    if write:
        (run_dir / "token_usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        (run_dir / "token_usage_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return rows, summary


def parse_metrics(text):
    """Sum selected vLLM Prometheus counters over all engines/labels."""
    values = {name: 0.0 for name in SERVER_COUNTERS}
    seen = set()
    for line in text.splitlines():
        match = re.match(r"^vllm:([a-z_]+)(?:\{[^}]*\})?\s+([0-9.eE+-]+)$", line.strip())
        if match and match.group(1) in values:
            values[match.group(1)] += float(match.group(2))
            seen.add(match.group(1))
    return {name: values[name] for name in SERVER_COUNTERS if name in seen}


def metrics_delta(before, after, seconds, gpus):
    delta = {name: after[name] - before.get(name, 0.0) for name in after}
    return {"counters": delta, "wall_seconds": seconds, "gpus": gpus, "gpu_hours": seconds * gpus / 3600,
            "note": "server-wide counters: only attributable to this sample if nothing else used the server"}


def _sample_runs(evaluation):
    """(sample name, generation run, solved CVEs) for each sample scored by an evaluation."""
    from scripts.infer.pass_at_k import evaluated_samples, load_sample, provenance
    info = provenance(evaluation)
    invocation = Path(info["invocation"]) if info.get("invocation") else None
    run_dir = Path(info["generation_run_dir"]) if info.get("generation_run_dir") else None
    found = []
    for name, patches, summary in evaluated_samples(evaluation):
        _, solved, _ = load_sample(patches, summary)
        candidates = []
        if invocation is not None and (invocation / "generation" / name).is_dir():
            candidates = [d for d in (invocation / "generation" / name).iterdir()
                          if (d / "results.jsonl").is_file()]
        elif run_dir is not None and (run_dir / "results.jsonl").is_file():
            candidates = [run_dir]
        if len(candidates) != 1:
            raise ValueError(f"Cannot locate the generation run of {evaluation} {name}: {candidates}")
        found.append((name, candidates[0], solved))
    return info, found


def evaluation_usage(evaluations, destination=None, select=None, dataset=DATASET):
    """Pool per-task usage over the scored samples of one or more evaluations, joined with solved flags."""
    samples, harnesses = [], set()
    for evaluation in evaluations:
        info, found = _sample_runs(evaluation)
        wanted = (select or {}).get(str(evaluation))
        for name, run, solved in found:
            if wanted and name not in wanted:
                continue
            rows, summary = run_usage(run, dataset=dataset)
            harnesses.add(summary["harness"])
            for row in rows:
                row["solved"] = row["cve"] in solved
            metrics = run.parent / "server_metrics.json"
            samples.append({"evaluation": str(evaluation), "sample": name, "run": str(run),
                            "cache_split_reported_by_server": summary["cache_split_reported_by_server"],
                            "rows": rows,
                            "server_metrics": json.loads(metrics.read_text()) if metrics.is_file() else None})
    rows = [row for sample in samples for row in sample["rows"]]
    solved = sum(row["solved"] for row in rows)
    overall = totals(rows)
    per_solved = {field: (overall[field] / solved if overall[field] is not None and solved else None)
                  for field in (*FIELDS, "total_tokens", "requests")}
    report = {
        "harness": sorted(h for h in harnesses if h), "n_samples": len(samples),
        "n_cves": len({row["cve"] for row in rows}),
        "definitions": __doc__.split("Sources:")[0].split("\n", 2)[2].strip(),
        "overall": overall, "solved_runs": solved,
        "tokens_per_solved_run": per_solved,
        "per_sample": [{k: v for k, v in s.items() if k != "rows"} | {"totals": totals(s["rows"]),
                       "solved": sum(r["solved"] for r in s["rows"])} for s in samples],
        "per_language": _grouped(rows, "language"),
        "solved_vs_unsolved": _grouped(rows, "solved"),
        "per_cve": {cve: [{"sample": s["sample"], "solved": r["solved"], "complete": r["complete"],
                           **{k: (r["usage"] or {}).get(k) for k in ("requests", *FIELDS)}}
                          for s in samples for r in s["rows"] if r["cve"] == cve]
                    for cve in sorted({row["cve"] for row in rows})},
    }
    if destination is not None:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "token_usage.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def parse_prices(text):
    """'input=0.3,cached=0.03,output=1.2' ($ per 1M tokens) -> dict."""
    prices = {}
    for item in text.split(","):
        key, _, value = item.partition("=")
        if key.strip() not in ("input", "cached", "output"):
            raise ValueError(f"Unknown price key {key!r}; use input, cached, output")
        prices[key.strip()] = float(value)
    if "input" not in prices or "output" not in prices:
        raise ValueError("Prices need at least input and output")
    return prices


def cost(block, prices):
    """Dollar cost of a totals block; unknown cache split prices all input as uncached (upper bound)."""
    cached = block.get("cached_input_tokens")
    upper = cached is None
    uncached = block["input_tokens"] if upper else block["uncached_input_tokens"]
    dollars = (uncached * prices["input"] + (0 if upper else cached * prices.get("cached", prices["input"]))
               + block["output_tokens"] * prices["output"]) / 1e6
    return {"usd": dollars, "upper_bound_cache_unknown": upper}


def _bar_parts(block, per):
    cached = block.get("cached_input_tokens")
    parts = [("uncached input", (block["uncached_input_tokens"] if cached is not None else None)),
             ("cached input", cached),
             ("input (cache split not reported)", block["input_tokens"] if cached is None else None),
             ("output (non-reasoning)", block["output_tokens"] - block["reasoning_tokens"]),
             ("reasoning", block["reasoning_tokens"])]
    return [(label, value / per) for label, value in parts if value is not None and per]


def svg_tokens(reports, labels, path, prices=None):
    """Stacked bars: mean tokens per task and per solved run, one bar per harness."""
    colors = {"uncached input": "#4C78A8", "cached input": "#9ECAE9", "input (cache split not reported)": "#72B7B2",
              "output (non-reasoning)": "#F58518", "reasoning": "#E45756"}
    panels = [("mean tokens per task (all runs)", lambda r: r["overall"]["tasks_with_usage"]),
              ("tokens per solved run", lambda r: r["solved_runs"])]
    width, panel_w, height, top, plot_h = 760, 340, 420, 60, 280
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="sans-serif" '
           f'font-size="11">', f'<rect width="{width}" height="{height}" fill="white"/>',
           '<text x="20" y="22" font-size="14" font-weight="bold">Token usage by harness</text>']
    for p, (title, per) in enumerate(panels):
        x0 = 60 + p * (panel_w + 30)
        stacks = [_bar_parts(r["overall"], per(r)) for r in reports]
        peak = max((sum(v for _, v in s) for s in stacks), default=1) or 1
        out.append(f'<text x="{x0 + panel_w / 2}" y="{top - 12}" text-anchor="middle" font-weight="bold">{title}</text>')
        for tick in range(5):
            value = peak * tick / 4
            y = top + plot_h - plot_h * tick / 4
            out.append(f'<line x1="{x0}" x2="{x0 + panel_w}" y1="{y:.1f}" y2="{y:.1f}" stroke="#ddd"/>'
                       f'<text x="{x0 - 4}" y="{y + 4:.1f}" text-anchor="end">{value / 1000:,.0f}k</text>')
        bar = panel_w / (len(reports) * 2)
        for i, (stack, label) in enumerate(zip(stacks, labels)):
            bx, y = x0 + bar * (0.5 + 2 * i), top + plot_h
            for part, value in stack:
                h = plot_h * value / peak
                y -= h
                out.append(f'<rect class="segment" x="{bx:.1f}" y="{y:.1f}" width="{bar:.1f}" height="{h:.1f}" '
                           f'fill="{colors[part]}"><title>{label} {part}: {value:,.0f}</title></rect>')
            note = ""
            if prices:
                c = cost(reports[i]["overall"], prices)
                note = f' ${c["usd"] / per(reports[i]):.4f}' + ("*" if c["upper_bound_cache_unknown"] else "")
            out.append(f'<text x="{bx + bar / 2:.1f}" y="{top + plot_h + 15}" text-anchor="middle">{label}{note}</text>')
    for i, (part, color) in enumerate(colors.items()):
        out.append(f'<rect x="{20 + (i % 3) * 240}" y="{height - 44 + (i // 3) * 16}" width="10" height="10" '
                   f'fill="{color}"/><text x="{34 + (i % 3) * 240}" y="{height - 35 + (i // 3) * 16}">{part}</text>')
    if prices:
        out.append(f'<text x="20" y="{height - 4}">* all input priced as uncached (cache split not reported)</text>')
    out.append("</svg>")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def compare(report_paths, labels, destination, prices=None):
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in report_paths]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    rows = []
    for label, report in zip(labels, reports):
        o, per = report["overall"], report["tokens_per_solved_run"]
        entry = {"label": label, "samples": report["n_samples"], "tasks": o["tasks"],
                 "tasks_incomplete": o["tasks_incomplete"], "requests": o["requests"],
                 **{f: o[f] for f in FIELDS}, "total_tokens": o["total_tokens"],
                 "per_task_mean_input": o["per_task_input_tokens"]["mean"],
                 "per_task_mean_output": o["per_task_output_tokens"]["mean"],
                 "solved_runs": report["solved_runs"], "per_solved_run": per,
                 "cache_unknown_tasks": o["cache_unknown_tasks"]}
        if prices:
            entry["cost"] = cost(o, prices) | {"prices_usd_per_1m": prices}
            entry["cost"]["usd_per_solved_run"] = (entry["cost"]["usd"] / report["solved_runs"]
                                                   if report["solved_runs"] else None)
        rows.append(entry)
    result = {"labels": list(labels), "reports": [str(p) for p in report_paths], "harnesses": rows}
    (destination / "token_comparison.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    fmt = lambda v: "n/a" if v is None else f"{v:,.0f}"
    lines = ["# Token usage comparison", "",
             "Input includes cached tokens; output includes reasoning. `n/a` cached = not reported by the server.", "",
             "| | samples x tasks | requests | input | cached input | output | reasoning | mean input/task | "
             "mean output/task | solved runs | tokens/solved run |" + (" cost (USD) |" if prices else ""),
             "|---|---|---|---|---|---|---|---|---|---|---|" + ("---|" if prices else "")]
    for r in rows:
        lines.append(f"| {r['label']} | {r['samples']} x {r['tasks'] // max(r['samples'], 1)} | {fmt(r['requests'])} | "
                     f"{fmt(r['input_tokens'])} | {fmt(r['cached_input_tokens'])} | {fmt(r['output_tokens'])} | "
                     f"{fmt(r['reasoning_tokens'])} | {fmt(r['per_task_mean_input'])} | {fmt(r['per_task_mean_output'])} | "
                     f"{r['solved_runs']} | {fmt(r['per_solved_run']['total_tokens'])} |"
                     + (f" {r['cost']['usd']:.2f}{'*' if r['cost']['upper_bound_cache_unknown'] else ''} |" if prices else ""))
    if any(r["cache_unknown_tasks"] for r in rows):
        lines += ["", "Cached input is n/a where the server did not report prompt-cache hits for that harness "
                  "(vLLM Chat Completions without --enable-prompt-tokens-details)."]
    (destination / "token_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    svg_tokens(reports, labels, destination / "token_usage.svg", prices)
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Extract, pool, and compare token usage.")
    parser.add_argument("--run", action="append", help="Generation run directory (writes token_usage.jsonl)")
    parser.add_argument("--harness", help="Override the harness detected from resolved.yaml")
    parser.add_argument("--eval", action="append", metavar="DIR[:sample_0,sample_1]",
                        help="Evaluation invocation, optionally restricted to named samples; repeatable")
    parser.add_argument("--compare", nargs="+", metavar="TOKEN_USAGE_JSON")
    parser.add_argument("--labels", help="Comma-separated names for --compare reports")
    parser.add_argument("--prices", help="USD per 1M tokens, e.g. input=0.3,cached=0.03,output=1.2")
    parser.add_argument("--out", help="Output directory for --eval or --compare")
    args = parser.parse_args(argv)
    prices = parse_prices(args.prices) if args.prices else None
    for run in args.run or []:
        _, summary = run_usage(run, args.harness)
        print(f"{run}: {summary['tasks_with_usage']}/{summary['tasks']} tasks, input={summary['input_tokens']:,} "
              f"output={summary['output_tokens']:,}")
    if args.eval:
        if not args.out:
            parser.error("--eval needs --out")
        evaluations, select = [], {}
        for item in args.eval:
            path, _, names = item.partition(":")
            path = str(Path(path).resolve())
            evaluations.append(path)
            if names:
                select[path] = names.split(",")
        report = evaluation_usage(evaluations, args.out, select)
        o = report["overall"]
        print(f"{report['n_samples']} samples: input={o['input_tokens']:,} output={o['output_tokens']:,} "
              f"-> {Path(args.out) / 'token_usage.json'}")
    if args.compare:
        labels = args.labels.split(",") if args.labels else [Path(p).parent.name for p in args.compare]
        if len(labels) != len(args.compare) or not args.out:
            parser.error("--compare needs one label per report and --out")
        compare(args.compare, labels, args.out, prices)
        print(f"Wrote {Path(args.out) / 'token_comparison.md'} and token_usage.svg")
    if not (args.run or args.eval or args.compare):
        parser.error("give --run, --eval, or --compare")


if __name__ == "__main__":
    main()
