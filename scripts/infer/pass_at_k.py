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
