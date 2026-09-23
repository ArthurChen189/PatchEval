#!/usr/bin/env python3
"""Aggregate per-sample PatchEval evaluations into pass@k."""

import json
from pathlib import Path


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


def summarize(samples):
    """samples: list of (cases, solved, execution_errors) with identical case sets."""
    if not samples:
        raise ValueError("No evaluated samples")
    cases = samples[0][0]
    for index, (other, _, _) in enumerate(samples[1:], 1):
        if set(other) != set(cases):
            raise ValueError(f"Sample {index} evaluated a different CVE set than sample 0")
    n = len(samples)
    counts = {cve: sum(cve in solved for _, solved, _ in samples) for cve in sorted(cases)}

    def rates(selected):
        result = {}
        for k in range(1, n + 1):
            values = [pass_at_k(n, counts[cve], k) for cve in selected]
            result[f"pass@{k}"] = sum(values) / len(values) if values else None
        return result

    languages = sorted(set(cases.values()))
    return {
        "n_samples": n,
        "n_cves": len(cases),
        "estimator": "unbiased pass@k over all samples; pass@1 is the mean single-sample solve rate",
        **rates(list(counts)),
        "per_sample_solved": [len(solved) for _, solved, _ in samples],
        "per_sample_execution_errors": [len(errors) for _, _, errors in samples],
        "per_language": {lang: {"n_cves": sum(v == lang for v in cases.values()),
                                **rates([c for c in counts if cases[c] == lang])} for lang in languages},
        "per_cve_solved_count": counts,
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


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Merge per-sample evaluations into one pass@k report.")
    parser.add_argument("--eval", action="append", required=True, metavar="DIR[:sample_0,sample_1]",
                        help="Evaluation invocation, optionally restricted to named samples; repeatable")
    parser.add_argument("--out", required=True, help="New directory for the merged pass_at_k.json")
    args = parser.parse_args(argv)
    evaluations, select = [], {}
    for item in args.eval:
        path, _, names = item.partition(":")
        path = str(Path(path).resolve())
        evaluations.append(path)
        if names:
            select[path] = names.split(",")
    result = merge(evaluations, args.out, select)
    scores = "  ".join(f"pass@{k}={result[f'pass@{k}']:.2%}" for k in range(1, result["n_samples"] + 1))
    print(f"{result['n_cves']} CVEs x {result['n_samples']} samples: {scores}")
    if result["mixed_serving_settings"] or result["mixed_harness_versions"]:
        print("NOTE: merged samples differ in serving settings or harness versions; see merged_from")
    print(f"Wrote {Path(args.out) / 'pass_at_k.json'}")


if __name__ == "__main__":
    main()
