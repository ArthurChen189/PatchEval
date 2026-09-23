#!/usr/bin/env python3
"""Aggregate per-sample PatchEval evaluations into pass@k."""

import itertools
import json
import math
from pathlib import Path
import random

Z95 = 1.959963984540054
MAX_SUBSETS = 1000


def pass_at_k(n, c, k):
    """Unbiased pass@k for one case (Chen et al., 2021); None when n < k."""
    if k > n:
        return None
    if n - c < k:
        return 1.0
    miss = 1.0
    for i in range(k):
        miss *= (n - c - i) / (n - i)
    return 1.0 - miss


def load_sample(patches_file, summary_file):
    """Return (all evaluated CVEs with languages, solved CVEs, execution errors) for one sample."""
    cases = {}
    for line in Path(patches_file).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            cases[row["cve"]] = row.get("language") or row.get("programming_language") or "unknown"
    summary = json.loads(Path(summary_file).read_text(encoding="utf-8"))
    successful = summary.get("poc_evaluation", {}).get("successful_cves", {})
    solved = {cve for cves in successful.values() for cve in cves} if isinstance(successful, dict) else set(successful)
    unknown = solved - set(cases)
    if unknown:
        raise ValueError(f"{summary_file} reports solved CVEs absent from {patches_file}: {sorted(unknown)[:5]}")
    return cases, solved, summary.get("execution_errors") or []


def mean_variance_stderr(values):
    """Mean, sample variance (n-1), and standard error of the mean; None where undefined."""
    values = list(values)
    if not values:
        return None, None, None
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, None, None
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return mean, variance, math.sqrt(variance / len(values))


def interval(mean, stderr, low=0.0, high=1.0):
    """Normal-approximation 95% interval, clipped to [low, high]."""
    if stderr is None:
        return None
    return [max(low, mean - Z95 * stderr), min(high, mean + Z95 * stderr)]


def subsets(n, k, seed=0):
    """Every size-k subset of n runs, or a seeded random MAX_SUBSETS of them when there are more."""
    if math.comb(n, k) <= MAX_SUBSETS:
        return list(itertools.combinations(range(n), k))
    rng = random.Random(seed)
    return [tuple(sorted(rng.sample(range(n), k))) for _ in range(MAX_SUBSETS)]


def uncertainty(n, counts, solved_by, k):
    """Error bars for pass@k over the given CVEs.

    CVE-level: variance and standard error of the per-CVE unbiased estimates
    (CLT over CVEs; includes each CVE's sampling noise). Run-level: pass@k of
    every size-k subset of the n runs (fraction of CVEs solved by any run in the
    subset); their mean equals the unbiased estimate, their spread is the
    run-to-run variance. Subsets overlap, so use the CVE-level error for tests.
    """
    mean, variance, stderr = mean_variance_stderr(pass_at_k(n, c, k) for c in counts.values())
    runs = [sum(bool(solved_by[cve] & set(subset)) for cve in counts) / len(counts)
            for subset in subsets(n, k)] if counts else []
    _, run_variance, _ = mean_variance_stderr(runs)
    return {"stderr": stderr, "variance": variance, "ci95": interval(mean, stderr) if mean is not None else None,
            "run_values": runs, "run_variance": run_variance,
            "run_std": math.sqrt(run_variance) if run_variance is not None else None,
            "run_min": min(runs) if runs else None, "run_max": max(runs) if runs else None}


def summarize(samples):
    """samples: list of (cases, solved, execution_errors) with identical case sets."""
    if not samples:
        raise ValueError("No evaluated samples")
    cases = samples[0][0]
    for index, (other, _, _) in enumerate(samples[1:], 1):
        if set(other) != set(cases):
            raise ValueError(f"Sample {index} evaluated a different CVE set than sample 0")
    n = len(samples)
    solved_by = {cve: {i for i, (_, solved, _) in enumerate(samples) if cve in solved} for cve in sorted(cases)}
    counts = {cve: len(runs) for cve, runs in solved_by.items()}

    def rates(selected):
        chosen = {cve: counts[cve] for cve in selected}
        result, errors = {}, {}
        for k in range(1, n + 1):
            values = [pass_at_k(n, c, k) for c in chosen.values()]
            result[f"pass@{k}"] = sum(values) / len(values) if values else None
            errors[f"pass@{k}"] = uncertainty(n, chosen, solved_by, k)
        return {**result, "uncertainty": errors}

    languages = sorted(set(cases.values()))
    return {
        "n_samples": n,
        "n_cves": len(cases),
        "estimator": "unbiased pass@k over all samples; pass@1 is the mean single-sample solve rate",
        "uncertainty_method": ("stderr/variance/ci95: over CVEs of the per-CVE unbiased pass@k (normal 95% CI); "
                               "run_*: pass@k of each size-k subset of the sample runs (run-to-run spread)"),
        **rates(list(counts)),
        "per_sample_solved": [len(solved) for _, solved, _ in samples],
        "per_sample_execution_errors": [len(errors) for _, _, errors in samples],
        "per_language": {lang: {"n_cves": sum(v == lang for v in cases.values()),
                                **rates([c for c in counts if cases[c] == lang])} for lang in languages},
        "per_cve_solved_count": counts,
        "per_cve_solved_samples": {cve: sorted(runs) for cve, runs in solved_by.items()},
        "per_cve_language": {cve: cases[cve] for cve in sorted(cases)},
    }


def aggregate(sample_dirs, destination, metadata=None):
    """sample_dirs: [(patches.jsonl, summary.json), ...] in sample order."""
    result = {**(metadata or {}),
              **summarize([load_sample(patches, summary) for patches, summary in sample_dirs])}
    Path(destination).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _yaml_value(text, section, key):
    """Read `section.key` from a resolved.yaml without importing a YAML parser."""
    import re
    block = re.search(rf"^{section}:\n((?:  .*\n)+)", text, re.M)
    match = re.search(rf"^  {key}: (.*)$", block.group(1), re.M) if block else None
    return match.group(1).strip() if match else None


def evaluated_samples(evaluation):
    """(name, patches.jsonl, summary.json) for each sample scored by one evaluation invocation."""
    evaluation = Path(evaluation)
    output = evaluation / "evaluation_output"
    names = sorted(d.name for d in output.iterdir() if d.is_dir() and d.name.startswith("sample_")) if output.is_dir() else []
    pairs = [(name, evaluation / "eval_inputs" / name / "patches.jsonl", output / name / "summary.json") for name in names]
    if not pairs and (output / "summary.json").is_file():
        pairs = [("sample_0", evaluation / "eval_inputs/patches.jsonl", output / "summary.json")]
    missing = [str(s) for _, p, s in pairs for f in (p, s) if not f.is_file()] + ([] if pairs else [str(output)])
    if missing:
        raise ValueError(f"Incomplete evaluation output: {missing[:3]}")
    return pairs


def provenance(evaluation):
    """Where an evaluation's samples came from and how they were served and generated."""
    evaluation = Path(evaluation)
    run_dir = _yaml_value((evaluation / "resolved.yaml").read_text(), "evaluation", "run_dir")
    generation = Path(run_dir) if run_dir else None
    invocation = None
    while generation is not None and generation != generation.parent:
        if (generation / "resolved.yaml").is_file() and generation != evaluation:
            invocation = generation
            break
        generation = generation.parent
    info = {"evaluation": str(evaluation), "generation_run_dir": run_dir, "invocation": str(invocation) if invocation else None}
    if invocation:
        resolved = (invocation / "resolved.yaml").read_text()
        info["harness"] = _yaml_value(resolved, "harness", "name")
        for name, key in (("server.json", "server"), ("harness-version.json", "harness_version")):
            path = invocation / name
            info[key] = json.loads(path.read_text()) if path.is_file() else None
    return info


def merge(evaluations, destination, select=None):
    """Pool samples from several evaluation invocations into one pass@k report.

    select maps an evaluation path to the sample names to take from it (all by
    default). Samples must cover the same CVEs; the report records each
    sample's origin and flags differing serving or harness settings.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    files, sources = [], []
    for evaluation in evaluations:
        info = provenance(evaluation)
        wanted = (select or {}).get(str(evaluation))
        for name, patches, summary in evaluated_samples(evaluation):
            if wanted and name not in wanted:
                continue
            files.append((patches, summary))
            sources.append({**info, "sample": name})
    servers = {json.dumps((s.get("server") or {}).get("argv")) for s in sources}
    harnesses = {json.dumps([s.get("harness"), (s.get("harness_version") or {}).get("version")]) for s in sources}
    metadata = {"merged_from": sources, "mixed_serving_settings": len(servers) > 1,
                "mixed_harness_versions": len(harnesses) > 1}
    return aggregate(files, destination / "pass_at_k.json", metadata)


def _paired(a, b, draws, rng):
    """Paired difference b - a over CVEs: SE, normal and sign-flip permutation p-values."""
    d = [y - x for x, y in zip(a, b)]
    diff, variance, stderr = mean_variance_stderr(d)
    if stderr in (None, 0.0):
        z, p_normal = None, (1.0 if diff == 0 else 0.0)
    else:
        z = diff / stderr
        p_normal = math.erfc(abs(z) / math.sqrt(2))
    nonzero = [v for v in d if v]
    observed = abs(sum(nonzero))
    hits = 0
    for _ in range(draws):
        bits = rng.getrandbits(len(nonzero)) if nonzero else 0
        total = sum(v if bits >> i & 1 else -v for i, v in enumerate(nonzero))
        hits += abs(total) >= observed - 1e-12
    return {"diff": diff, "stderr": stderr, "variance": variance, "ci95": interval(diff, stderr, -1.0, 1.0),
            "z": z, "p_normal": p_normal, "p_permutation": (hits + 1) / (draws + 1),
            "better": sum(v > 0 for v in d), "worse": sum(v < 0 for v in d), "tied": sum(v == 0 for v in d)}


def compare(report_a, report_b, labels=("a", "b"), draws=20000, seed=0):
    """Paired pass@k comparison of two reports over the same CVEs (difference = b - a)."""
    counts_a, counts_b = report_a["per_cve_solved_count"], report_b["per_cve_solved_count"]
    if set(counts_a) != set(counts_b):
        raise ValueError("Reports cover different CVE sets")
    n_a, n_b = report_a["n_samples"], report_b["n_samples"]
    language = report_a.get("per_cve_language") or report_b.get("per_cve_language") or {}
    groups = {"overall": sorted(counts_a)}
    for lang in sorted(set(language.values())):
        groups[lang] = sorted(c for c in counts_a if language.get(c) == lang)
    rng = random.Random(seed)
    result = {"labels": list(labels), "difference": f"{labels[1]} - {labels[0]}", "n_cves": len(counts_a),
              "n_samples": [n_a, n_b],
              "method": ("paired over CVEs: stderr = sd(per-CVE difference of unbiased pass@k)/sqrt(N); "
                         "p_normal = two-sided normal test; p_permutation = two-sided paired sign-flip "
                         f"permutation test ({draws} draws, seed {seed}); 'better' counts CVEs where "
                         f"{labels[1]} scores higher"),
              "mixed_serving_settings": {labels[0]: report_a.get("mixed_serving_settings", False),
                                         labels[1]: report_b.get("mixed_serving_settings", False)},
              "groups": {}}
    for name, cves in groups.items():
        result["groups"][name] = {"n_cves": len(cves), **{
            f"pass@{k}": _paired([pass_at_k(n_a, counts_a[c], k) for c in cves],
                                 [pass_at_k(n_b, counts_b[c], k) for c in cves], draws, rng)
            for k in range(1, min(n_a, n_b) + 1)}}
    return result


def _cell(report, key, group="overall"):
    block = report if group == "overall" else report["per_language"][group]
    error = block["uncertainty"][key]
    ci = error["ci95"]
    run = f" (runs sd {error['run_std']:.2%})" if error.get("run_std") is not None else ""
    if error["stderr"] is None:
        return f"{block[key]:.2%}{run}"
    return f"{block[key]:.2%} ± {error['stderr']:.2%} [{ci[0]:.1%}, {ci[1]:.1%}]{run}"


def comparison_markdown(report_a, report_b, comparison):
    labels = comparison["labels"]
    ks = [f"pass@{k}" for k in range(1, min(comparison["n_samples"]) + 1)]
    lines = [f"# pass@k with error bars: {labels[0]} vs {labels[1]}", "",
             f"{comparison['n_cves']} CVEs; samples per CVE: {labels[0]} {comparison['n_samples'][0]}, "
             f"{labels[1]} {comparison['n_samples'][1]}.", "",
             "Cells: estimate ± standard error over CVEs [95% CI] (sd across runs / run subsets).", ""]
    for group in comparison["groups"]:
        lines += [f"## {group} ({comparison['groups'][group]['n_cves']} CVEs)", "",
                  "| | " + " | ".join(ks) + " |", "|---|" + "---|" * len(ks)]
        for label, report in zip(labels, (report_a, report_b)):
            lines.append(f"| {label} | " + " | ".join(_cell(report, k, group) for k in ks) + " |")
        cells = []
        for k in ks:
            p = comparison["groups"][group][k]
            spread = (f" ± {p['stderr']:.2%} [{p['ci95'][0]:+.1%}, {p['ci95'][1]:+.1%}]"
                      if p["stderr"] is not None else "")
            cells.append(f"{p['diff']:+.2%}{spread}; p={p['p_permutation']:.3f} (normal {p['p_normal']:.3f})")
        lines += [f"| {comparison['difference']} | " + " | ".join(cells) + " |", ""]
    lines += ["Method: " + comparison["method"] + ".", ""]
    return "\n".join(lines)


def svg_error_bars(reports, labels, path):
    """Grouped bar chart of pass@k with 95% CI whiskers: overall plus one panel per language."""
    colors = ["#4C78A8", "#F58518", "#54A24B", "#B279A2"]
    n = min(r["n_samples"] for r in reports)
    groups = ["overall"] + sorted(reports[0].get("per_language", {}))
    pw, ph, top, left, bottom = 300, 260, 50, 45, 40
    width, height = left + pw * len(groups) + 20, top + ph + bottom + 30
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'font-family="sans-serif" font-size="11">', f'<rect width="{width}" height="{height}" fill="white"/>',
           f'<text x="{left}" y="18" font-size="14" font-weight="bold">pass@k with 95% CI (error bars) '
           f'over CVEs</text>']
    lo = 0.4
    y = lambda v: top + ph - (max(v, lo) - lo) / (1 - lo) * ph
    for g, group in enumerate(groups):
        x0 = left + g * pw
        out.append(f'<text x="{x0 + pw / 2}" y="{top - 8}" text-anchor="middle" font-weight="bold">{group}</text>')
        for tick in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
            out.append(f'<line x1="{x0}" x2="{x0 + pw - 20}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" stroke="#ddd"/>')
            if g == 0:
                out.append(f'<text x="{x0 - 5}" y="{y(tick) + 4:.1f}" text-anchor="end">{tick:.0%}</text>')
        slot = (pw - 20) / n
        bar = slot * 0.8 / len(reports)
        for k in range(1, n + 1):
            key = f"pass@{k}"
            out.append(f'<text x="{x0 + (k - 0.5) * slot:.1f}" y="{top + ph + 15}" text-anchor="middle">{key}</text>')
            for r, report in enumerate(reports):
                block = report if group == "overall" else report["per_language"][group]
                value = block[key]
                ci = block["uncertainty"][key]["ci95"] or [value, value]
                bx = x0 + (k - 1) * slot + slot * 0.1 + r * bar
                out.append(f'<rect class="bar" x="{bx:.1f}" y="{y(value):.1f}" width="{bar:.1f}" '
                           f'height="{y(lo) - y(value):.1f}" fill="{colors[r % len(colors)]}">'
                           f'<title>{labels[r]} {group} {key}: {value:.2%} [{ci[0]:.1%}, {ci[1]:.1%}]</title></rect>')
                cx = bx + bar / 2
                out.append(f'<g class="whisker" stroke="black"><line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(ci[0]):.1f}" '
                           f'y2="{y(ci[1]):.1f}"/><line x1="{cx - 3:.1f}" x2="{cx + 3:.1f}" y1="{y(ci[0]):.1f}" '
                           f'y2="{y(ci[0]):.1f}"/><line x1="{cx - 3:.1f}" x2="{cx + 3:.1f}" y1="{y(ci[1]):.1f}" '
                           f'y2="{y(ci[1]):.1f}"/></g>')
    for r, label in enumerate(labels):
        lx = left + r * 110
        out.append(f'<rect x="{lx}" y="{height - 18}" width="10" height="10" fill="{colors[r % len(colors)]}"/>'
                   f'<text x="{lx + 14}" y="{height - 9}">{label}</text>')
    out.append("</svg>")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def write_comparison(report_a_path, report_b_path, labels, destination, draws=20000):
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in (report_a_path, report_b_path)]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    result = compare(*reports, labels=labels, draws=draws)
    result["reports"] = [str(report_a_path), str(report_b_path)]
    (destination / "comparison.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (destination / "comparison.md").write_text(comparison_markdown(*reports, result), encoding="utf-8")
    svg_error_bars(reports, labels, destination / "error_bars.svg")
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Merge per-sample evaluations into one pass@k report, "
                                                 "or compare two reports with error bars and p-values.")
    parser.add_argument("--eval", action="append", metavar="DIR[:sample_0,sample_1]",
                        help="Evaluation invocation, optionally restricted to named samples; repeatable")
    parser.add_argument("--compare", nargs=2, metavar=("A_PASS_AT_K_JSON", "B_PASS_AT_K_JSON"),
                        help="Paired comparison (B - A) of two pass_at_k.json reports")
    parser.add_argument("--labels", default="a,b", help="Names for the two --compare reports (default a,b)")
    parser.add_argument("--draws", type=int, default=20000, help="Permutation draws for --compare")
    parser.add_argument("--out", required=True, help="New directory for the merged pass_at_k.json, "
                                                     "or directory for comparison.{json,md} and error_bars.svg")
    args = parser.parse_args(argv)
    if args.compare:
        labels = args.labels.split(",")
        if len(labels) != 2:
            parser.error("--labels needs two comma-separated names")
        result = write_comparison(*args.compare, labels, args.out, args.draws)
        for k in range(1, min(result["n_samples"]) + 1):
            p = result["groups"]["overall"][f"pass@{k}"]
            print(f"pass@{k} {result['difference']}: {p['diff']:+.2%} ± {p['stderr']:.2%} "
                  f"(p_permutation={p['p_permutation']:.3f}, p_normal={p['p_normal']:.3f})")
        print(f"Wrote {Path(args.out) / 'comparison.md'} and error_bars.svg")
        return
    if not args.eval:
        parser.error("--eval is required unless --compare is given")
    evaluations, select = [], {}
    for item in args.eval:
        path, _, names = item.partition(":")
        path = str(Path(path).resolve())
        evaluations.append(path)
        if names:
            select[path] = names.split(",")
    result = merge(evaluations, args.out, select)
    print(f"{result['n_cves']} CVEs x {result['n_samples']} samples: {format_scores(result)}")
    if result["mixed_serving_settings"] or result["mixed_harness_versions"]:
        print("NOTE: merged samples differ in serving settings or harness versions; see merged_from")
    print(f"Wrote {Path(args.out) / 'pass_at_k.json'}")


def format_scores(result):
    """pass@k with standard error over CVEs, for console summaries."""
    parts = []
    for k in range(1, result["n_samples"] + 1):
        stderr = result.get("uncertainty", {}).get(f"pass@{k}", {}).get("stderr")
        parts.append(f"pass@{k}={result[f'pass@{k}']:.2%}" + (f"±{stderr:.2%}" if stderr is not None else ""))
    return "  ".join(parts)


if __name__ == "__main__":
    main()
