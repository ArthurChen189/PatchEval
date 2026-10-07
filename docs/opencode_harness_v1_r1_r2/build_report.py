#!/usr/bin/env python3
"""Build docs/opencode_harness_v1_r1_r2/index.html: how the OpenCode harness variants differ, which gains are
environment fixes and which are claimable harness improvements.

Naming (run ids from analysis_reports/20261006_051507-opencode-secpatch2-val46 in brackets):
  B0     plain OpenCode, original environment                         [baseline, 2026-10-02 offline rerun]
  v1     secpatch v1 profile, original environment                    [v1]
  B1     plain OpenCode, fixed environment = the fair baseline        [R1 without continuation: R1's episodes, each
                                                                       continued one scored at its first length stop]
  B1c    B1 + continue-on-length                                      [R1]
  B1p    B1 + secpatch2 profile                                       [R2 without continuation]
  full   B1 + continue-on-length + secpatch2 = the full harness       [R2]

Reads only saved artifacts (no Docker, no model): each evaluation's pass_at_k.json and token_usage.json, and the
analysis folder's episodes/ and comparisons/. Standard library only; charts are inline SVG.

    python docs/opencode_harness_v1_r1_r2/build_report.py
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "patcheval/exp_agent/agent_runs/analysis_reports/20261006_051507-opencode-secpatch2-val46"
OUT = Path(__file__).resolve().parent / "index.html"


def env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


E = env(REPORT / "inputs.env")
EVALS = {"B0": E["BE"], "v1": E["VE"], "B1": E["N1"], "B1c": E["E1"], "B1p": E["N2"], "full": E["E2"]}
RUN_ID = {"B0": "baseline", "v1": "v1", "B1": "R1 w/o continuation", "B1c": "R1", "B1p": "R2 w/o continuation",
          "full": "R2"}
SUMMARY_KEY = {"B0": "baseline", "v1": "v1", "B1c": "R1", "full": "R2"}  # arms with their own generated episodes
GENERATED = ["B0", "v1", "B1c", "full"]
ALL = ["B0", "v1", "B1", "B1c", "B1p", "full"]
SHORT = {"B0": "B0", "v1": "v1", "B1": "B1", "B1c": "B1 + cont.", "B1p": "B1 + secpatch2", "full": "Full harness"}
NAMES = {"B0": "B0 · plain OpenCode, original env", "v1": "v1 · secpatch v1, original env",
         "B1": "B1 · plain OpenCode, fixed env", "B1c": "B1 + continuation", "B1p": "B1 + secpatch2",
         "full": "Full harness · B1 + continuation + secpatch2"}
CI_NAMES = {"B0": "B0 · plain, original env", "v1": "v1 · secpatch v1, original env", "B1": "B1 · plain, fixed env",
            "B1c": "B1 + continuation", "B1p": "B1 + secpatch2", "full": "Full · B1 + cont. + secpatch2"}
COLORS = {"B0": "var(--c-b0)", "v1": "var(--c-v1)", "B1": "var(--c-b1)", "B1c": "var(--c-cont)",
          "B1p": "var(--c-prof)", "full": "var(--c-full)"}

pk = {a: json.load(open(REPO / p / "pass_at_k.json")) for a, p in EVALS.items()}
summary = json.load(open(REPORT / "episodes/summary.json"))
comparisons = {d.name: json.load(open(d / "comparison.json")) for d in sorted((REPORT / "comparisons").iterdir())}
tokens = {}
for a in GENERATED:
    t = REPO / EVALS[a] / "token_usage.json"
    if t.is_file():
        o = json.load(open(t))["overall"]
        tokens[a] = {"in": o["input_tokens"] / o["tasks"], "out": o["output_tokens"] / o["tasks"]}
esc = html.escape

# ---------------------------------------------------------------- cost
# OpenRouter list prices for Qwen3.8-27B (USD per 1M tokens), as in
# analysis_reports/20260924_161849-cost-vs-pass1-qwen27b-9b-codex-opencode (cached rate = its --cached-price example)
PRICE_IN, PRICE_CACHED, PRICE_OUT = 0.094, 0.0094, 4.40
episodes = json.load(open(REPORT / "episodes/episodes.json"))
cost = {}
for a in GENERATED:
    tu = json.load(open(REPO / EVALS[a] / "token_usage.json"))
    o, n = tu["overall"], tu["overall"]["tasks"]
    solved = round(pk[a]["pass@1"] * n)
    eps = episodes[SUMMARY_KEY[a]]
    dur = sorted(e["duration_s"] or 0 for e in eps)
    sm = [s.get("server_metrics") or {} for s in tu["per_sample"]]
    list_usd = (o["input_tokens"] * PRICE_IN + o["output_tokens"] * PRICE_OUT) / 1e6
    cache_usd = (o["uncached_input_tokens"] * PRICE_IN + o["cached_input_tokens"] * PRICE_CACHED
                 + o["output_tokens"] * PRICE_OUT) / 1e6
    cost[a] = {
        "n": n, "solved": solved,
        "in_total": o["input_tokens"], "cached_total": o["cached_input_tokens"], "uncached_total": o["uncached_input_tokens"],
        "out_total": o["output_tokens"], "reason_total": o["reasoning_tokens"], "requests_total": o["requests"],
        "in_ep": o["input_tokens"] / n, "uncached_ep": o["uncached_input_tokens"] / n, "out_ep": o["output_tokens"] / n,
        "reason_ep": o["reasoning_tokens"] / n, "cache_rate": o["cached_input_tokens"] / o["input_tokens"],
        "requests_ep": o["requests"] / n, "tools_ep": summary[SUMMARY_KEY[a]]["tool_calls_mean"],
        "agent_min_mean": sum(dur) / len(dur) / 60, "agent_min_median": dur[len(dur) // 2] / 60,
        "agent_hours": sum(dur) / 3600,
        "wall_hours": sum(m.get("wall_seconds") or 0 for m in sm) / 3600,
        "gpu_hours": sum(m.get("gpu_hours") or 0 for m in sm),
        "usd_list": list_usd, "usd_cache": cache_usd,
        "usd_ep_list": list_usd / n, "usd_ep_cache": cache_usd / n,
        "usd_solved_list": list_usd / solved, "usd_solved_cache": cache_usd / solved,
        "tok_solved": (o["input_tokens"] + o["output_tokens"]) / solved,
    }


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}"


def cmp1(key: str, k: str = "pass@1") -> dict:
    return comparisons[key]["groups"]["overall"][k]


def S(a: str) -> dict:
    return summary[SUMMARY_KEY[a]]


FULL_B1, CONT, PROF_NC, PROF_C, ENV = (cmp1("R2_vs_R1nocont"), cmp1("R1_vs_R1nocont"), cmp1("R2nocont_vs_R1nocont"),
                                      cmp1("R2_vs_R1"), cmp1("R1nocont_vs_baseline"))
FULL_B0, FULL_V1 = cmp1("R2_vs_baseline"), cmp1("R2_vs_v1")

# Held-out check on nexus iid100 (cyber-train, 2026-10-07): the full harness vs plain OpenCode, back to back.
# results.json is written by cyber-train's scripts/nexus/arthur/secpatch2/iid100_stats.py.
NEXUS_DOC = Path(os.environ.get("CYBER_TRAIN_ROOT", "/home/ubuntu/Workplace/cyber-train")) / \
    "docs/arthur/qwen38-27b-secpatch2-iid100"
NX = json.load(open(NEXUS_DOC / "results.json")) if (NEXUS_DOC / "results.json").is_file() else None
NXD = NX["comparisons"]["R2 - baseline"]["groups"]["overall"]["pass@1"] if NX else None


def pp(d: dict) -> str:
    return f"{d['diff'] * 100:+.1f} pp"


def ci(d: dict) -> str:
    return f"[{d['ci95'][0] * 100:+.1f}, {d['ci95'][1] * 100:+.1f}]"


# ---------------------------------------------------------------- charts (inline SVG)
def passk_chart() -> str:
    arms = ["B0", "v1", "B1", "B1c", "full"]
    w, h, l, r, t, b = 640, 300, 52, 160, 16, 40
    y0, y1 = 0.55, 0.95
    X = lambda k: l + (k - 1) * (w - l - r) / 3  # noqa: E731
    Y = lambda v: t + (y1 - v) / (y1 - y0) * (h - t - b)  # noqa: E731
    s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="pass@k by configuration">']
    for g in range(55, 100, 5):
        s.append(f'<line x1="{l}" x2="{w - r}" y1="{Y(g / 100):.1f}" y2="{Y(g / 100):.1f}" class="grid"/>'
                 f'<text x="{l - 8}" y="{Y(g / 100) + 4:.1f}" class="tick" text-anchor="end">{g}%</text>')
    for k in range(1, 5):
        s.append(f'<text x="{X(k):.1f}" y="{h - b + 22}" class="tick" text-anchor="middle">pass@{k}</text>')
    labels = []
    for a in arms:
        pts = [(X(k), Y(pk[a][f"pass@{k}"])) for k in range(1, 5)]
        c = COLORS[a]
        dash = ' stroke-dasharray="5 4"' if a in ("B0", "v1") else ""
        s.append(f'<polyline fill="none" stroke="{c}" stroke-width="{3 if a == "full" else 2}"{dash} '
                 f'points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>')
        for (x, y), k in zip(pts, range(1, 5)):
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{c}"><title>{esc(NAMES[a])} pass@{k}: '
                     f'{pct(pk[a][f"pass@{k}"])}%</title></circle>')
        labels.append([pts[-1][1], a])
    labels.sort()
    for i in range(1, len(labels)):  # keep end labels from overlapping
        labels[i][0] = max(labels[i][0], labels[i - 1][0] + 15)
    for y, a in labels:
        s.append(f'<text x="{w - r + 10}" y="{y + 4:.1f}" class="lbl" fill="{COLORS[a]}">{esc(SHORT[a])} '
                 f'{pct(pk[a]["pass@4"])}%</text>')
    s.append("</svg>")
    return "".join(s)


def ci_chart() -> str:
    w, rowh, l, r, t = 640, 34, 300, 64, 10
    h = t + rowh * len(ALL) + 34
    x0, x1 = 0.45, 0.95
    X = lambda v: l + (v - x0) / (x1 - x0) * (w - l - r)  # noqa: E731
    s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="pass@1 with 95% CI">']
    for g in range(45, 100, 10):
        s.append(f'<line x1="{X(g / 100):.1f}" x2="{X(g / 100):.1f}" y1="{t}" y2="{h - 28}" class="grid"/>'
                 f'<text x="{X(g / 100):.1f}" y="{h - 10}" class="tick" text-anchor="middle">{g}%</text>')
    for i, a in enumerate(ALL):
        y = t + i * rowh + rowh / 2
        v = pk[a]["pass@1"]
        lo, hi = pk[a]["uncertainty"]["pass@1"]["ci95"]
        c = COLORS[a]
        hollow = a in ("B1", "B1p")
        s.append(f'<text x="{l - 10}" y="{y + 4:.1f}" class="lbl" text-anchor="end">{esc(CI_NAMES[a])}</text>'
                 f'<line x1="{X(lo):.1f}" x2="{X(hi):.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{c}" stroke-width="2"'
                 f'{" stroke-dasharray=\"4 3\"" if hollow else ""}/>'
                 f'<circle cx="{X(v):.1f}" cy="{y:.1f}" r="5" fill="{"var(--panel)" if hollow else c}" stroke="{c}" '
                 f'stroke-width="2"><title>{esc(NAMES[a])}: {pct(v)}% [{pct(lo)}, {pct(hi)}]</title></circle>'
                 f'<text x="{w - r + 8}" y="{y + 4:.1f}" class="num">{pct(v)}%</text>')
    s.append("</svg>")
    return "".join(s)


def waterfall() -> str:
    steps = [(("B0", "plain, original env"), pk["B0"]["pass@1"], None, "var(--c-b0)", False),
             (("+ environment fixes", "= B1, fair baseline"), pk["B1"]["pass@1"], ENV, "var(--c-b1)", False),
             (("+ continuation", "= B1 + cont."), pk["B1c"]["pass@1"], CONT, "var(--c-cont)", True),
             (("+ secpatch2", "= full harness"), pk["full"]["pass@1"], PROF_C, "var(--c-full)", True)]
    w, h, l, r, t, b = 640, 340, 52, 12, 64, 72
    y0, y1 = 0.60, 0.78
    Y = lambda v: t + (y1 - v) / (y1 - y0) * (h - t - b)  # noqa: E731
    bw = (w - l - r) / len(steps)
    s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="pass@1 decomposition">',
         '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         '<rect width="6" height="6" fill="var(--c-b1)"/><line x1="0" y1="0" x2="0" y2="6" stroke="var(--panel)" '
         'stroke-width="2"/></pattern></defs>']
    for g in range(60, 80, 4):
        s.append(f'<line x1="{l}" x2="{w - r}" y1="{Y(g / 100):.1f}" y2="{Y(g / 100):.1f}" class="grid"/>'
                 f'<text x="{l - 8}" y="{Y(g / 100) + 4:.1f}" class="tick" text-anchor="end">{g}%</text>')
    prev = None
    xs = []
    for i, (name, v, cmp_, c, claim) in enumerate(steps):
        x = l + i * bw + bw * 0.18
        bwid = bw * 0.64
        xs.append((x, bwid))
        lo = y0 if prev is None else min(prev, v)
        hi = v if prev is None else max(prev, v)
        fill = "url(#hatch)" if i == 1 else c
        s.append(f'<rect x="{x:.1f}" y="{Y(hi):.1f}" width="{bwid:.1f}" height="{max(1.5, Y(lo) - Y(hi)):.1f}" fill="{fill}" rx="2"/>')
        if prev is not None:
            s.append(f'<line x1="{x - bw * 0.36:.1f}" x2="{x:.1f}" y1="{Y(prev):.1f}" y2="{Y(prev):.1f}" class="conn"/>')
        lab = f"{pct(v)}%" if cmp_ is None else f"{(v - prev) * 100:+.1f} pp"
        s.append(f'<text x="{x + bwid / 2:.1f}" y="{Y(hi) - 7:.1f}" class="num" text-anchor="middle">{lab}</text>')
        if cmp_ is not None:
            tag = "claimable" if claim else "not claimable"
            s.append(f'<text x="{x + bwid / 2:.1f}" y="{h - b + 52:.1f}" class="tick" text-anchor="middle">'
                     f'p = {cmp_["p_permutation"]:.2f} · {tag}</text>')
        s.append(f'<text x="{x + bwid / 2:.1f}" y="{h - b + 18:.1f}" class="lbl" text-anchor="middle">{esc(name[0])}</text>'
                 f'<text x="{x + bwid / 2:.1f}" y="{h - b + 33:.1f}" class="tick" text-anchor="middle">{esc(name[1])}</text>')
        prev = v
    # bracket over the claimable steps (B1 -> full)
    bx0, bx1 = xs[2][0], xs[3][0] + xs[3][1]
    by = 22
    s.append(f'<path d="M{bx0:.1f},{by + 10} V{by} H{bx1:.1f} V{by + 10}" fill="none" stroke="var(--c-full)" stroke-width="1.5"/>'
             f'<text x="{bx1:.1f}" y="{by - 6}" class="lbl" text-anchor="end" fill="var(--c-full)">'
             f'claimable over B1: {pp(FULL_B1)} (p = {FULL_B1["p_permutation"]:.2f})</text>')
    s.append("</svg>")
    return "".join(s)


def outcomes_chart() -> str:
    cats = [("solved", "Solved", "var(--ok)"), ("patch_failed", "Wrong patch", "var(--bad)"),
            ("empty_length", "Empty (length stop)", "var(--warn)"), ("timeout", "Timeout", "var(--muted-strong)")]
    w, rowh, l, r, t = 640, 34, 120, 14, 8
    h = t + rowh * len(GENERATED) + 40
    n = 184
    X = lambda v: l + v / n * (w - l - r)  # noqa: E731
    s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="episode outcomes">']
    for i, a in enumerate(GENERATED):
        y = t + i * rowh
        o = S(a)["outcomes"]
        acc = 0
        s.append(f'<text x="{l - 10}" y="{y + rowh / 2 + 4:.1f}" class="lbl" text-anchor="end">{esc(SHORT[a])}</text>')
        for key, name, c in cats:
            v = o.get(key, 0)
            if not v:
                continue
            s.append(f'<rect x="{X(acc):.1f}" y="{y + 5}" width="{X(acc + v) - X(acc):.1f}" height="{rowh - 10}" fill="{c}">'
                     f'<title>{esc(NAMES[a])}: {name} {v}/184</title></rect>')
            if X(acc + v) - X(acc) > 18:
                s.append(f'<text x="{(X(acc) + X(acc + v)) / 2:.1f}" y="{y + rowh / 2 + 4:.1f}" class="inbar" text-anchor="middle">{v}</text>')
            acc += v
    lx = l
    for key, name, c in cats:
        s.append(f'<rect x="{lx}" y="{h - 24}" width="11" height="11" fill="{c}" rx="2"/>'
                 f'<text x="{lx + 16}" y="{h - 14}" class="tick">{name}</text>')
        lx += 22 + 7 * len(name)
    s.append("</svg>")
    return "".join(s)


def heatmap() -> str:
    cols = ["B0", "v1", "B1", "B1c", "full"]
    lang = pk["full"]["per_cve_language"]
    cves = sorted(lang, key=lambda c: (lang[c], c))
    cw, rh, l, t = 74, 15, 150, 30
    w = l + cw * len(cols) + 110
    h = t + rh * len(cves) + 12
    s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="per-CVE solves out of 4">']
    for j, a in enumerate(cols):
        s.append(f'<text x="{l + j * cw + cw / 2:.1f}" y="{t - 10}" class="lbl" text-anchor="middle">'
                 f'{esc({"B1c": "B1+cont", "full": "Full"}.get(a, a))}</text>')
    prev_lang = None
    for i, c in enumerate(cves):
        y = t + i * rh
        if lang[c] != prev_lang:
            if prev_lang is not None:
                s.append(f'<line x1="{l - 140}" x2="{l + cw * len(cols)}" y1="{y:.1f}" y2="{y:.1f}" class="sep"/>')
            s.append(f'<text x="{l + cw * len(cols) + 12}" y="{y + 11:.1f}" class="lbl">{esc(lang[c])}</text>')
            prev_lang = lang[c]
        s.append(f'<text x="{l - 8}" y="{y + 11:.1f}" class="mono" text-anchor="end">{esc(c)}</text>')
        for j, a in enumerate(cols):
            n = pk[a]["per_cve_solved_count"].get(c, 0)
            s.append(f'<rect x="{l + j * cw + 2}" y="{y + 1}" width="{cw - 4}" height="{rh - 2}" rx="2" '
                     f'class="h{n}"><title>{c} · {esc(NAMES[a])}: solved {n}/4</title></rect>')
            if n:
                s.append(f'<text x="{l + j * cw + cw / 2:.1f}" y="{y + 11:.1f}" class="hnum{" hdark" if n >= 3 else ""}" '
                         f'text-anchor="middle">{n}</text>')
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------- tables
def comparison_rows() -> str:
    groups = [
        ("Claimable harness changes (vs the fair baseline B1)", [
            ("Full harness", "Full − B1", "R2_vs_R1nocont"),
            ("Continue-on-length alone", "B1+cont − B1", "R1_vs_R1nocont"),
            ("secpatch2 profile alone", "B1+secpatch2 − B1", "R2nocont_vs_R1nocont"),
            ("secpatch2 on top of continuation", "Full − B1+cont", "R2_vs_R1"),
            ("Continuation on top of secpatch2", "Full − B1+secpatch2", "R2_vs_R2nocont")]),
        ("Environment fixes (not claimable)", [
            ("ripgrep + project Python venv + id-leak fix", "B1 − B0", "R1nocont_vs_baseline")]),
        ("Historical, mixes environment and harness (do not cite as a harness gain)", [
            ("Full harness vs original-env baseline", "Full − B0", "R2_vs_baseline"),
            ("Continuation + env vs original-env baseline", "B1+cont − B0", "R1_vs_baseline"),
            ("Full harness vs v1 (v1 ran in the original env)", "Full − v1", "R2_vs_v1")]),
    ]
    out = []
    for title, rows in groups:
        out.append(f'<tr class="grp"><td colspan="9">{esc(title)}</td></tr>')
        for what, lab, key in rows:
            g = comparisons[key]["groups"]["overall"]
            cells = []
            for k in ("pass@1", "pass@4"):
                d = g[k]
                sig = d["p_permutation"] < 0.05
                cells.append(f'<td class="num{" sig" if sig else ""}">{d["diff"] * 100:+.1f}</td>'
                             f'<td class="num muted">{ci(d)}</td>'
                             f'<td class="num{" sig" if sig else ""}">{d["p_permutation"]:.3f}</td>')
            b = g["pass@1"]
            out.append(f'<tr><td>{esc(what)}</td><td class="mono">{esc(lab)}</td>{"".join(cells)}'
                       f'<td class="num">{b["better"]}/{b["worse"]}</td></tr>')
    return "\n".join(out)


def behavior_rows() -> str:
    def tok(a, key, unit):
        v = (tokens.get(a) or {}).get(key)
        return "–" if not v else (f"{v / 1e6:.2f} M" if unit == "M" else f"{v / 1e3:.0f} k")
    rows = [
        ("pass@1 (%)", lambda a: pct(S(a)["pass@1"])),
        ("Episodes with a length stop", lambda a: S(a)["episodes_with_length_stop"]),
        ("Empty patches", lambda a: S(a)["outcomes"].get("empty_length", 0)),
        ("Continued episodes · rescued / harmed vs no continuation",
         lambda a: (f'{S(a)["continued_episodes"]} · {S(a)["counterfactual"]["rescued"]} / '
                    f'{S(a)["counterfactual"]["harmed"]}') if S(a).get("counterfactual") else "–"),
        ("Timeouts (40 min)", lambda a: S(a)["outcomes"].get("timeout", 0)),
        ("ripgrep tool errors", lambda a: S(a)["ripgrep_errors"]),
        ("`skill` tool: loads / errors",
         lambda a: f'{S(a)["skill_loaded_episodes"]} / {S(a)["skill_errors"]}' if a in ("v1", "full") else "–"),
        ("Episodes reading a weakness-family file", lambda a: S(a)["episodes_reading_class_files"] if a == "full" else "–"),
        ("Python episodes with the project venv", lambda a: (S(a)["python_env"] or {}).get("relocated", 0)),
        ("Mean tool calls", lambda a: S(a)["tool_calls_mean"]),
        ("Median minutes per episode", lambda a: S(a)["median_duration_min"]),
        ("Input tokens per task", lambda a: tok(a, "in", "M")),
        ("Output tokens per task", lambda a: tok(a, "out", "k")),
    ]
    out = []
    for name, f in rows:
        out.append(f"<tr><td>{esc(name).replace('`skill`', '<code>skill</code>')}</td>" +
                   "".join(f'<td class="num">{esc(str(f(a)))}</td>' for a in GENERATED) + "</tr>")
    return "\n".join(out)


def passk_rows() -> str:
    out = []
    for a in ALL:
        p = pk[a]
        lo, hi = p["uncertainty"]["pass@1"]["ci95"]
        per = p["per_sample_solved"]
        per = " · ".join(str(x) for x in (per if isinstance(per, list) else per.values()))
        cls = ' class="strong"' if a in ("B1", "full") else ""
        out.append(f'<tr{cls}><td><span class="sw" style="background:{COLORS[a]}"></span>{esc(NAMES[a])}</td>'
                   f'<td class="mono muted">{esc(RUN_ID[a])}</td>'
                   + "".join(f'<td class="num">{pct(p[f"pass@{k}"])}</td>' for k in range(1, 5))
                   + f'<td class="num muted">[{pct(lo)}, {pct(hi)}]</td>'
                   f'<td class="num muted">{"rescored" if a in ("B1", "B1p") else per}</td></tr>')
    return "\n".join(out)


def lang_rows() -> str:
    cols = ["B0", "v1", "B1", "B1c", "full"]
    out = []
    for lg in ("Go", "JavaScript", "Python"):
        vals = [pk[a]["per_language"][lg]["pass@1"] if "per_language" in pk[a] and lg in pk[a]["per_language"]
                else S(a)["per_language_pass@1"][lg] for a in cols]
        best = max(vals)
        n = sum(1 for v in pk["full"]["per_cve_language"].values() if v == lg)
        out.append(f"<tr><td>{lg} <span class='muted'>({n})</span></td>" +
                   "".join(f'<td class="num{" strong" if v == best else ""}">{pct(v)}</td>' for v in vals) + "</tr>")
    return "\n".join(out)


def class_bars() -> str:
    cl = sorted(summary["R2"]["class_files_read"].items(), key=lambda kv: -kv[1])
    m = max(v for _, v in cl)
    return "".join(f'<div class="cbar"><span class="mono">{esc(k)}</span><span class="track"><span style="width:{100 * v / m:.0f}%"></span></span>'
                   f'<span class="num">{v}</span></div>' for k, v in cl)


def cost_rows() -> str:
    def m(v):
        return f"{v / 1e6:,.0f} M" if v >= 1e8 else f"{v / 1e6:,.1f} M"
    groups = [
        ("Tokens (all 184 episodes)", [
            ("Input tokens", lambda c: m(c["in_total"])),
            ("  of which served from the prefix cache", lambda c: f'{m(c["cached_total"])} ({c["cache_rate"] * 100:.0f}%)'),
            ("  uncached input", lambda c: m(c["uncached_total"])),
            ("Output tokens", lambda c: m(c["out_total"])),
            ("  of which reasoning", lambda c: m(c["reason_total"])),
            ("Model requests (turns)", lambda c: f'{c["requests_total"]:,}')]),
        ("Per episode", [
            ("Input tokens", lambda c: f'{c["in_ep"] / 1e6:.2f} M'),
            ("Uncached input tokens", lambda c: f'{c["uncached_ep"] / 1e6:.2f} M'),
            ("Output tokens (reasoning)", lambda c: f'{c["out_ep"] / 1e3:.1f} k ({c["reason_ep"] / 1e3:.1f} k)'),
            ("Model requests (turns)", lambda c: f'{c["requests_ep"]:.1f}'),
            ("Tool calls", lambda c: f'{c["tools_ep"]:.1f}'),
            ("Agent minutes, mean / median", lambda c: f'{c["agent_min_mean"]:.1f} / {c["agent_min_median"]:.1f}')]),
        ("Per solved episode", [
            ("Solved episodes (of 184)", lambda c: c["solved"]),
            ("Tokens (input + output)", lambda c: f'{c["tok_solved"] / 1e6:.2f} M'),
            ("Cost, list price", lambda c: f'${c["usd_solved_list"]:.3f}'),
            ("Cost, with cache discount", lambda c: f'${c["usd_solved_cache"]:.3f}')]),
        ("Run totals (4 samples)", [
            ("Cost per episode, list / with cache discount", lambda c: f'${c["usd_ep_list"]:.3f} / ${c["usd_ep_cache"]:.3f}'),
            ("Total cost, list / with cache discount", lambda c: f'${c["usd_list"]:.2f} / ${c["usd_cache"]:.2f}'),
            ("Agent hours (sum over episodes)", lambda c: f'{c["agent_hours"]:.1f} h'),
            ("Wall-clock hours (4 samples, 48 parallel containers)", lambda c: f'{c["wall_hours"]:.2f} h'),
            ("H200 GPU-hours (8 GPUs × wall-clock)", lambda c: f'{c["gpu_hours"]:.1f}')]),
    ]
    out = []
    for title, rows in groups:
        out.append(f'<tr class="grp"><td colspan="{len(GENERATED) + 2}">{esc(title)}</td></tr>')
        for name, f in rows:
            vals = [f(cost[a]) for a in GENERATED]
            base = cost["B1c"]
            out.append(f'<tr><td>{"&nbsp;&nbsp;&nbsp;" if name.startswith("  ") else ""}{esc(name.strip())}</td>'
                       + "".join(f'<td class="num">{esc(str(v))}</td>' for v in vals) + "</tr>")
    return "\n".join(out)


def input_share(c: dict) -> float:
    return c["in_ep"] * PRICE_IN / (c["in_ep"] * PRICE_IN + c["out_ep"] * PRICE_OUT)


def cost_delta() -> dict:
    a, b = cost["full"], cost["B1c"]
    return {k: a[k] / b[k] - 1 for k in ("in_ep", "uncached_ep", "out_ep", "requests_ep", "agent_min_mean", "usd_ep_list",
                                         "usd_ep_cache", "usd_solved_list", "gpu_hours", "wall_hours")}


def cost_chart() -> str:
    w, h, l, r, t, b = 640, 300, 56, 24, 16, 44
    xs = [cost[a]["usd_ep_list"] for a in GENERATED]
    x0, x1 = 0.20, max(xs) * 1.12
    y0, y1 = 0.50, 0.90
    X = lambda v: l + (v - x0) / (x1 - x0) * (w - l - r)  # noqa: E731
    Y = lambda v: t + (y1 - v) / (y1 - y0) * (h - t - b)  # noqa: E731
    s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="cost per episode vs pass@1">']
    for g in range(50, 95, 10):
        s.append(f'<line x1="{l}" x2="{w - r}" y1="{Y(g / 100):.1f}" y2="{Y(g / 100):.1f}" class="grid"/>'
                 f'<text x="{l - 8}" y="{Y(g / 100) + 4:.1f}" class="tick" text-anchor="end">{g}%</text>')
    v = 0.20
    while v <= x1:
        s.append(f'<line x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{t}" y2="{h - b}" class="grid"/>'
                 f'<text x="{X(v):.1f}" y="{h - b + 16}" class="tick" text-anchor="middle">${v:.2f}</text>')
        v = round(v + 0.05, 2)
    s.append(f'<text x="{(l + w - r) / 2:.1f}" y="{h - 6}" class="tick" text-anchor="middle">cost per episode, '
             f'list price (USD; ${PRICE_IN}/M in, ${PRICE_OUT}/M out)</text>')
    for a in GENERATED:
        c = cost[a]
        lo, hi = pk[a]["uncertainty"]["pass@1"]["ci95"]
        x, y = X(c["usd_ep_list"]), Y(pk[a]["pass@1"])
        s.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{Y(hi):.1f}" y2="{Y(lo):.1f}" stroke="{COLORS[a]}" stroke-width="2" opacity=".55"/>'
                 f'<circle cx="{X(c["usd_ep_cache"]):.1f}" cy="{y:.1f}" r="4" fill="var(--panel)" stroke="{COLORS[a]}" stroke-width="1.5">'
                 f'<title>{esc(NAMES[a])}: ${c["usd_ep_cache"]:.3f}/episode with cache discount</title></circle>'
                 f'<line x1="{X(c["usd_ep_cache"]) + 4:.1f}" x2="{x - 6:.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{COLORS[a]}" stroke-dasharray="2 3"/>'
                 f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{COLORS[a]}"><title>{esc(NAMES[a])}: ${c["usd_ep_list"]:.3f}/episode, '
                 f'pass@1 {pct(pk[a]["pass@1"])}%</title></circle>'
                 f'<text x="{x + 10:.1f}" y="{y - 8:.1f}" class="lbl" fill="{COLORS[a]}">{esc(SHORT[a])}</text>')
    s.append("</svg>")
    return "".join(s)


def nexus_section() -> str:
    """Section 9: the held-out check on nexus iid100 (empty when cyber-train's results.json is absent)."""
    if not NX:
        return ""
    A, ref, run1 = NX["arms"], NX["reference_arms"], NX["r2_run1"]
    b, r = A["baseline"], A["R2"]
    bb, rb = b["behavior"], r["behavior"]
    rows = [("Plain OpenCode (baseline rerun)", "2026-10-07", b["pass_at_k"]["pass@1"], b["pass_at_k"]["pass@4"],
             bb["cap_hit_rate"], bb["median_run_min"], f'${bb["cost_usd_list"]["per_rollout"]:.2f}', True),
            ("Full harness (R2, run 2)", "2026-10-07", r["pass_at_k"]["pass@1"], r["pass_at_k"]["pass@4"],
             rb["cap_hit_rate"], rb["median_run_min"], f'${rb["cost_usd_list"]["per_rollout"]:.2f}', True),
            ("Full harness (R2, run 1; raw data lost)", "2026-10-06", run1["summary"]["mean"],
             run1["summary"]["pass_at_4"], run1["summary"]["cap_hit_rate"], run1["behavior"]["median_run_min"], "–", False),
            ("Plain OpenCode (earlier box)", "2026-10-01", ref["old_baseline"]["pass@1"], ref["old_baseline"]["pass@4"],
             None, None, "–", False),
            ("cyber v2: LSP + Semgrep hints (earlier box)", "2026-10-01", ref["cyber_v2"]["pass@1"],
             ref["cyber_v2"]["pass@4"], None, None, "–", False)]
    arm_rows = "\n".join(
        f'<tr{" class=\"strong\"" if main else ""}><td>{esc(n)}</td><td class="mono muted">{d}</td>'
        f'<td class="num">{pct(p1)}</td><td class="num">{pct(p4)}</td>'
        f'<td class="num">{"–" if cap is None else pct(cap, 2) + "%"}</td><td class="num">{"–" if med is None else med}</td>'
        f'<td class="num">{usd}</td></tr>' for n, d, p1, p4, cap, med, usd, main in rows)
    cmp_rows = []
    for key, what in (("R2 - baseline", "Full harness − plain OpenCode (same day, same server)"),
                      ("baseline - old_baseline", "Plain rerun − plain on the earlier box (reproducibility)"),
                      ("R2 - old_baseline", "Full harness − plain on the earlier box"),
                      ("R2 - cyber_v2", "Full harness − cyber v2 (LSP + Semgrep)")):
        g = NX["comparisons"][key]["groups"]["overall"]
        d1, d4 = g["pass@1"], g["pass@4"]
        cmp_rows.append(f'<tr><td>{esc(what)}</td><td class="num{" sig" if d1["p_permutation"] < 0.05 else ""}">'
                        f'{d1["diff"] * 100:+.1f}</td><td class="num muted">{ci(d1)}</td>'
                        f'<td class="num">{d1["p_permutation"]:.2f}</td><td class="num">{d4["diff"] * 100:+.1f}</td>'
                        f'<td class="num">{d4["p_permutation"]:.2f}</td><td class="num">{d1["better"]}/{d1["worse"]}</td></tr>')
    lang = NX["comparisons"]["R2 - baseline"]["groups"]
    lang_cells = " · ".join(f'{l} {lang[l]["pass@1"]["diff"] * 100:+.1f} ({lang[l]["n_cves"]})'
                            for l in sorted((k for k in lang if k != "overall"), key=lambda k: -lang[k]["n_cves"]))

    def beh(name, f, fmt):
        return f'<tr><td>{name}</td><td class="num">{fmt(f(bb))}</td><td class="num">{fmt(f(rb))}</td></tr>'
    beh_rows = "\n".join([
        beh("Rollouts hitting the 40-min cap", lambda x: x["cap_hit_rate"], lambda v: f"{v * 100:.2f}%"),
        beh("Mean reward: capped / uncapped rollouts", lambda x: (x["capped_mean_reward"], x["uncapped_mean_reward"]),
            lambda v: f"{v[0]:.3f} / {v[1]:.3f}"),
        beh("Run time: median / mean (min)", lambda x: (x["median_run_min"], x["mean_run_min"]), lambda v: f"{v[0]} / {v[1]}"),
        beh("Steps / tool calls", lambda x: (x["mean_steps"], x["mean_tool_calls"]), lambda v: f"{v[0]} / {v[1]}"),
        beh("OpenCode auto-compactions per rollout (solved / unsolved)",
            lambda x: (x["compactions"]["mean"], x["compactions"]["mean_solved"], x["compactions"]["mean_unsolved"]),
            lambda v: f"{v[0]} ({v[1]} / {v[2]})"),
        beh("Input / output tokens per rollout", lambda x: (x["tokens"]["input_per_rollout"], x["tokens"]["output_per_rollout"]),
            lambda v: f"{v[0] / 1e6:.2f} M / {v[1] / 1e3:.0f} k"),
        beh("Cost per rollout / per solved (list price)",
            lambda x: (x["cost_usd_list"]["per_rollout"], x["cost_usd_list"]["per_solved"]), lambda v: f"${v[0]:.2f} / ${v[1]:.2f}"),
        beh("Skill loaded / family file read", lambda x: (x["skill_loaded_rate"], x["class_files_read_rate"]),
            lambda v: "–" if not v[0] else f"{v[0] * 100:.2f}% / {v[1] * 100:.1f}%"),
        beh("Episodes with a length step / continued by the wrapper",
            lambda x: (x["episodes_with_length_step"], x["continued_episodes"]), lambda v: f"{v[0]} / {v[1]}"),
    ])
    return f"""
<h2>9. Held-out check: nexus iid100</h2>
<p>This is the full harness on 100 held-out nexus cyber-training tasks (the cyber-train iid100 set, 4 samples each),
against plain OpenCode run back to back on the same SGLang server and task images. Both arms use nexus's own prompt
and OpenCode settings (context 30k, output 12k, 40-minute agent cap), and both get the ripgrep fallback. They differ only
in the secpatch2 profile and the continuation wrapper. <b>The val46 gain did not transfer:</b> the full harness is at
{pp(NXD)} (95% CI {ci(NXD)}, p = {NXD['p_permutation']:.2f}) and costs
{(rb['cost_usd_list']['per_rollout'] / bb['cost_usd_list']['per_rollout'] - 1) * 100:+.0f}% per rollout.</p>
<div class="panel scroll"><table>
<thead><tr><th>nexus iid100 (100 × 4)</th><th>Date</th><th class="num">pass@1</th><th class="num">pass@4</th>
<th class="num">Cap hits</th><th class="num">Median min</th><th class="num">$ / rollout</th></tr></thead>
<tbody>
{arm_rows}
</tbody></table></div>
<div class="panel scroll"><table>
<thead><tr><th>Paired over the same 100 tasks</th><th class="num">Δ pass@1</th><th class="num">95% CI</th><th class="num">p</th>
<th class="num">Δ pass@4</th><th class="num">p</th><th class="num">Tasks better / worse</th></tr></thead>
<tbody>
{"".join(cmp_rows)}
</tbody></table>
<p class="muted" style="font-size:12.5px">Same method as section 5: per-task unbiased pass@k, normal CI, two-sided
sign-flip permutation test. Full harness − plain by language, pp (tasks): {esc(lang_cells)}. None is significant.</p></div>
<div class="two">
<div class="panel scroll"><h3>Behavior and cost on nexus</h3><table>
<thead><tr><th></th><th class="num">Plain</th><th class="num">Full harness</th></tr></thead>
<tbody>
{beh_rows}
</tbody></table></div>
<div class="panel"><h3>Reading it</h3><ul>
<li><b>Rollouts that finish do about as well.</b> Within capped and uncapped rollouts, the full harness is level with or
slightly above plain OpenCode. But its longer method (path inventory, variant matrix, checks, review) pushes 15 percentage points
more rollouts into the 40-minute cap. The val46 report flagged the same timeout failure mode.</li>
<li><b>Continuation is inert on nexus.</b> nexus reserves 12,288 output tokens in a 30,000-token context, so OpenCode
compacts a session once input plus output passes about 17.7k tokens. A response that runs into the output limit always
crosses that line. OpenCode then summarizes the session and resumes it with its own "Continue if you have next steps…"
message, so the wrapper never sees a session end on a length stop.</li>
<li><b>Compaction is constant in both arms:</b> 399 of 400 rollouts, {bb['compactions']['mean']} vs
{rb['compactions']['mean']} times per rollout. Unsolved rollouts compact more often. nexus's context and output settings
may matter more than the profile; that A/B was not run.</li>
<li><b>Run-to-run noise is about 2 pp.</b> The two full-harness runs scored {pct(run1['summary']['mean'])}% and
{pct(r['pass_at_k']['pass@1'])}%.</li>
</ul></div>
</div>
<p class="muted" style="font-size:12.5px">Details, per-rollout records and the reproduction scripts are in
<code>cyber-train/docs/arthur/qwen38-27b-secpatch2-iid100/</code> (README, <code>results.json</code>) and
<code>cyber-train/scripts/nexus/arthur/secpatch2/</code>. Run 1's raw data was lost when the instance stopped on
2026-10-06; only its summary survives.</p>
"""


nexus_card = "" if not NX else (
    f'<div class="card warn"><div class="k">Held-out: nexus iid100, full harness vs plain</div>'
    f'<div class="v">{pp(NXD)}</div><div class="d">{pct(NX["arms"]["R2"]["pass_at_k"]["pass@1"])}% vs '
    f'{pct(NX["arms"]["baseline"]["pass_at_k"]["pass@1"])}% · 95% CI {ci(NXD)} · p = {NXD["p_permutation"]:.2f} · '
    f'{(NX["arms"]["R2"]["behavior"]["cost_usd_list"]["per_rollout"] / NX["arms"]["baseline"]["behavior"]["cost_usd_list"]["per_rollout"] - 1) * 100:+.0f}% cost</div></div>')


# ---------------------------------------------------------------- page
Y_, N_, P_ ='<span class="yes">●</span>', '<span class="no">○</span>', '<span class="part">◐</span>'
features = [
    ("env", "Vendored ripgrep: OpenCode's own <code>grep</code>/<code>glob</code>/<code>skill</code> tools work offline",
     [N_, N_, Y_, Y_, Y_]),
    ("env", "Project Python venv kept, so Python agents can run the project's tests", [N_, N_, Y_, Y_, Y_]),
    ("env", "No CVE id leaked through the session id or <code>.pyc</code> files", [N_, N_, Y_, Y_, Y_]),
    ("harness", "Continue-on-length: re-prompt the same session (≤ 2×) after an output-limit stop",
     [N_, N_, N_, Y_, Y_]),
    ("harness", "Security rules in the global <code>AGENTS.md</code>", [N_, Y_ + " 2.6 kB", N_, N_, Y_ + " 1.3 kB"]),
    ("harness", "<code>security-patch</code> skill",
     [N_, P_ + " tool failed (no ripgrep); read by hand", N_, N_, Y_ + " loads with the tool"]),
    ("harness", "Per-weakness guidance", [N_, "1 file, 15 kB + review checklist", N_, N_, "15 family files, 1.2–2 kB"]),
    ("same", "Prompt, model, server, 40-min timeout, offline network", ["same"] * 5),
]
TYPE = {"env": '<span class="tag env">environment fix</span>', "harness": '<span class="tag har">harness</span>',
        "same": '<span class="tag">fixed</span>'}
feature_rows = "\n".join(f'<tr class="{k}"><td>{TYPE[k]}</td><td>{n}</td>' + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
                         for k, n, cells in features)

claims = [
    ("ripgrep in agent containers", "Restores OpenCode's built-in search and skill tools. They download <code>rg</code> on "
     "first use, which broke when PatchEval's agent containers went offline on 2026-10-02. Online-era runs had working "
     "grep.", "Evaluation-environment fix (a regression from the offline change)",
     "No. Every baseline needs it, and nexus task images already ship <code>rg</code>."),
    ("Project Python venv kept", "PatchEval's runner deleted <code>/workspace/PoC_env/&lt;CVE&gt;</code> along with the "
     "hidden test payload. That directory is the only place Python CVEs have their dependencies installed, so agents could not run project tests.",
     "Benchmark-runner bug fix", "No. It applies to all harnesses."),
    ("No CVE-id leak", "The session id carried the container name, and <code>.pyc</code> files still held the CVE "
     "id after the move.", "Benchmark-integrity fix", "No."),
    ("Continue-on-length", "OpenCode 1.18.31 ends the session when one response uses the whole output budget without a "
     "tool call. The wrapper re-prompts the <i>same</i> session up to twice with a fixed, task-neutral nudge, inside the "
     "same time budget.", "Agent-loop / harness robustness",
     "<b>Yes</b>, as harness engineering. It is generic rather than security-specific, and it works around an "
     "OpenCode exit behavior."),
    ("secpatch2 profile", "Security-patching method and guidance: safe-by-default priority, a path inventory and "
     "variant matrix, regression triage, review after the diff exists, and 15 weakness-family files.",
     "Cyber-specific harness content", "<b>Yes</b>. Its isolated effect is not significant on 46 CVEs."),
]
claim_rows = "\n".join(f'<tr><td><b>{n}</b></td><td>{w}</td><td>{c}</td><td>{v}</td></tr>' for n, w, c, v in claims)

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenCode Harness Variants</title>
<meta name="description" content="OpenCode security harness variants on PatchEval val46: which gains are environment fixes and which are claimable harness improvements, measured against a fair fixed-environment baseline.">
<style>
:root {{
  --bg: #fbfaf7; --panel: #ffffff; --ink: #1d1d1f; --muted: #6b6b70; --muted-strong: #8e8e93; --line: #e4e2dc;
  --grid: #ecebe6; --accent: #0b6e4f; --envbg: #f3f1ec;
  --c-b0: #a3a3a8; --c-v1: #c8812a; --c-b1: #5b6b80; --c-cont: #3b7dd8; --c-prof: #7ab89a; --c-full: #0b6e4f;
  --ok: #2e8b62; --bad: #c8553d; --warn: #e0a83a;
  --h0: #f1efea; --h1: #cfe6da; --h2: #9fd0b6; --h3: #5fae86; --h4: #2a7f57;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #141416; --panel: #1c1c1f; --ink: #ececee; --muted: #9a9aa1; --muted-strong: #77777e; --line: #2c2c31;
    --grid: #26262b; --accent: #4cc38a; --envbg: #202024;
    --c-b0: #8e8e96; --c-v1: #e3a04f; --c-b1: #8fa0b8; --c-cont: #6aa5f0; --c-prof: #8fd1b0; --c-full: #4cc38a;
    --ok: #46a87a; --bad: #d9705a; --warn: #d9a441;
    --h0: #232327; --h1: #1f4434; --h2: #2b6649; --h3: #3c8f66; --h4: #5fc48f;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #141416; --panel: #1c1c1f; --ink: #ececee; --muted: #9a9aa1; --muted-strong: #77777e; --line: #2c2c31;
  --grid: #26262b; --accent: #4cc38a; --envbg: #202024;
  --c-b0: #8e8e96; --c-v1: #e3a04f; --c-b1: #8fa0b8; --c-cont: #6aa5f0; --c-prof: #8fd1b0; --c-full: #4cc38a;
  --ok: #46a87a; --bad: #d9705a; --warn: #d9a441;
  --h0: #232327; --h1: #1f4434; --h2: #2b6649; --h3: #3c8f66; --h4: #5fc48f;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif; }}
main {{ max-width: 1000px; margin: 0 auto; padding: 32px 16px 64px; }}
h1 {{ font-size: 28px; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.01em; }}
h2 {{ font-size: 19px; margin: 40px 0 10px; }}
h3 {{ font-size: 15px; margin: 18px 0 6px; }}
p {{ margin: 8px 0; }}
.sub {{ color: var(--muted); margin-bottom: 22px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin: 18px 0 8px; }}
.card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }}
.card.main {{ border-color: var(--c-full); border-width: 2px; }}
.card.env {{ background: var(--envbg); }}
.card.warn {{ border-color: var(--bad); border-width: 2px; }}
.card .k {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
.card .v {{ font-size: 26px; font-weight: 650; font-variant-numeric: tabular-nums; margin-top: 2px; }}
.card .d {{ color: var(--muted); font-size: 13px; }}
.panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin: 12px 0; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
@media (max-width: 760px) {{ .two {{ grid-template-columns: 1fr; }} }}
.scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
th, td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
th {{ font-weight: 600; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
tr.env td {{ background: var(--envbg); }}
tr.grp td {{ font-weight: 650; font-size: 12.5px; color: var(--muted); background: var(--envbg); text-transform: uppercase; letter-spacing: .03em; }}
.tag {{ display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }}
.tag.env {{ color: var(--c-b1); border-color: var(--c-b1); }}
.tag.har {{ color: var(--c-full); border-color: var(--c-full); }}
.muted {{ color: var(--muted); }}
.strong, tr.strong td {{ font-weight: 650; }}
.sig {{ color: var(--accent); font-weight: 650; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }}
code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; background: var(--grid); padding: 1px 4px; border-radius: 4px; }}
.sw {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 7px; }}
.yes {{ color: var(--accent); }} .no {{ color: var(--muted-strong); }} .part {{ color: var(--c-v1); }}
svg {{ width: 100%; height: auto; display: block; }}
svg text {{ fill: var(--ink); }}
svg .grid {{ stroke: var(--grid); stroke-width: 1; }}
svg .sep {{ stroke: var(--line); stroke-width: 1; }}
svg .conn {{ stroke: var(--muted-strong); stroke-dasharray: 3 3; }}
svg .tick {{ font-size: 11px; fill: var(--muted); }}
svg .lbl {{ font-size: 12px; font-weight: 600; }}
svg .num {{ font-size: 12px; font-variant-numeric: tabular-nums; }}
svg .inbar {{ font-size: 11px; fill: #fff; font-weight: 600; }}
svg .mono {{ font-family: ui-monospace, Menlo, monospace; font-size: 10.5px; fill: var(--muted); }}
svg .hnum {{ font-size: 10px; fill: var(--ink); }} svg .hdark {{ fill: #fff; }}
.h0 {{ fill: var(--h0); }} .h1 {{ fill: var(--h1); }} .h2 {{ fill: var(--h2); }} .h3 {{ fill: var(--h3); }} .h4 {{ fill: var(--h4); }}
.legend {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px; color: var(--muted); margin-top: 8px; }}
.legend i {{ display: inline-block; width: 11px; height: 11px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }}
.cbar {{ display: grid; grid-template-columns: 130px 1fr 32px; gap: 8px; align-items: center; margin: 3px 0; font-size: 13px; }}
.cbar .track {{ background: var(--grid); border-radius: 3px; height: 9px; overflow: hidden; }}
.cbar .track span {{ display: block; height: 100%; background: var(--c-full); }}
.quote {{ border-left: 3px solid var(--c-full); padding: 10px 14px; background: var(--panel); border-radius: 0 8px 8px 0; margin: 12px 0; }}
.do, .dont {{ border-radius: 10px; padding: 12px 16px; border: 1px solid var(--line); background: var(--panel); }}
.do h3 {{ color: var(--c-full); margin-top: 0; }} .dont h3 {{ color: var(--bad); margin-top: 0; }}
ul {{ padding-left: 20px; }} li {{ margin: 4px 0; }}
.names td:first-child {{ white-space: nowrap; font-weight: 650; }}
footer {{ margin-top: 48px; color: var(--muted); font-size: 12.5px; }}
</style>
</head>
<body>
<main>
<h1>OpenCode security harness: environment fixes vs claimable improvements</h1>
<div class="sub">PatchEval validation split (46 CVEs: Go 17, JavaScript 15, Python 14) × 4 samples · Qwen3.8-27B on vLLM 0.29.0
(MTP 4, 16k output cap) · OpenCode 1.18.31 · offline containers · 40-minute timeout · identical task prompt</div>

<div class="cards">
  <div class="card main"><div class="k">Full harness vs fair baseline B1</div><div class="v">{pp(FULL_B1)}</div>
    <div class="d">{pct(pk['B1']['pass@1'])}% → {pct(pk['full']['pass@1'])}% pass@1 · 95% CI {ci(FULL_B1)} · p = {FULL_B1['p_permutation']:.2f}</div></div>
  <div class="card"><div class="k">Continue-on-length (claimable)</div><div class="v">{pp(CONT)}</div>
    <div class="d">p = {CONT['p_permutation']:.3f}; rescued 9 episodes, harmed none</div></div>
  <div class="card"><div class="k">secpatch2 profile (claimable)</div><div class="v">{PROF_C['diff'] * 100:+.1f} to {PROF_NC['diff'] * 100:+.1f} pp</div>
    <div class="d">not significant (p = {PROF_C['p_permutation']:.2f} / {PROF_NC['p_permutation']:.2f})</div></div>
  <div class="card env"><div class="k">Environment fixes (not claimable)</div><div class="v">{pp(ENV)}</div>
    <div class="d">B0 → B1: ripgrep, project venv, id-leak fix · p = {ENV['p_permutation']:.2f}</div></div>
  {nexus_card}
</div>

<h2>1. Names used in this report</h2>
<p>Earlier write-ups compared everything with the old <i>baseline</i>, which ran in the original, broken environment. Here
the reference is <b>B1</b>: plain OpenCode with the environment fixed. Run ids from the
<a href="../../patcheval/exp_agent/agent_runs/analysis_reports/20261006_051507-opencode-secpatch2-val46/README.md">val46
analysis</a> are given for traceability.</p>
<div class="panel scroll"><table class="names">
<thead><tr><th>Name</th><th>Configuration</th><th>Run id</th><th>Role</th></tr></thead>
<tbody>
<tr><td>B0</td><td>Plain OpenCode, original environment</td><td class="mono">baseline</td><td>Historical reference</td></tr>
<tr><td>v1</td><td>secpatch v1 profile, original environment</td><td class="mono">v1</td><td>First profile attempt; compare with B0 only</td></tr>
<tr><td>B1</td><td>Plain OpenCode, fixed environment</td><td class="mono">R1 w/o continuation</td><td><b>Fair baseline</b> for all claims</td></tr>
<tr><td>B1 + cont.</td><td>B1 + continue-on-length</td><td class="mono">R1</td><td>Continuation effect</td></tr>
<tr><td>B1 + secpatch2</td><td>B1 + secpatch2 profile</td><td class="mono">R2 w/o continuation</td><td>Profile effect</td></tr>
<tr><td>Full harness</td><td>B1 + continue-on-length + secpatch2</td><td class="mono">R2</td><td>Claimable total</td></tr>
</tbody></table>
<p class="muted" style="font-size:12.5px">B1 and B1 + secpatch2 are rescorings, not separate runs. Each takes the episodes of
R1 or R2 and scores every continued episode with the patch it held at its first output-limit stop. That is exactly what
plain OpenCode submits there, because it exits at a length stop. No other episode changes.</p></div>

<h2>2. What you can claim</h2>
<div class="panel scroll"><table>
<thead><tr><th>Change</th><th>What it does</th><th>Category</th><th>Claim as your harness work?</th></tr></thead>
<tbody>
{claim_rows}
</tbody></table></div>
<div class="two">
<div class="do"><h3>Claim</h3><ul>
<li>The <b>full harness</b> (continue-on-length + secpatch2) against <b>B1</b>: {pp(FULL_B1)} pass@1
({pct(pk['B1']['pass@1'])}% → {pct(pk['full']['pass@1'])}%), 95% CI {ci(FULL_B1)}, p = {FULL_B1['p_permutation']:.2f}.
This is suggestive but not significant at 0.05, and it holds <b>on PatchEval val46 only</b>: on held-out nexus tasks the
harness showed no gain (section 9).</li>
<li><b>Continue-on-length</b>: {pp(CONT)} (p = {CONT['p_permutation']:.3f}), the one significant component. Present it as
generic agent-loop engineering.</li>
<li><b>secpatch2</b>: {PROF_C['diff'] * 100:+.1f} to {PROF_NC['diff'] * 100:+.1f} pp, not significant. Behavioral effects are
clearer: the fewest wrong patches (38 against 48), the skill loaded in 184/184 episodes, and 80% of episodes read a
weakness-family file.</li>
<li><b>Its cost:</b> the full harness uses {cost_delta()['in_ep'] * 100:+.0f}% input tokens, {cost_delta()['requests_ep'] * 100:+.0f}% turns and
{cost_delta()['agent_min_mean'] * 100:+.0f}% agent time per episode over B1 + cont.; per solved episode it is
{cost_delta()['usd_solved_list'] * 100:+.0f}% at list price (${cost['B1c']['usd_solved_list']:.2f} → ${cost['full']['usd_solved_list']:.2f}).</li>
</ul></div>
<div class="dont"><h3>Don't claim</h3><ul>
<li>The <b>{pp(FULL_B0)}</b> of Full − B0 (p = {FULL_B0['p_permutation']:.3f}) as a harness gain. It includes the environment
fixes.</li>
<li>ripgrep, the Python venv, or the id-leak fix. These repair the benchmark environment and belong in every baseline.</li>
<li>Full − v1 ({pp(FULL_V1)}) as "v2 beats v1 by design". v1 never ran in the fixed environment, and its skill tool could
not load.</li>
<li>Any val46 number as held-out evidence. The secpatch2 method was derived from failures on these 46 CVEs.</li>
{"" if not NX else f"<li><b>That the harness generalizes.</b> On 100 held-out nexus cyber-training tasks the full harness scored {pp(NXD)} against plain OpenCode (95% CI {ci(NXD)}, p = {NXD['p_permutation']:.2f}), at higher cost (section 9).</li>"}
</ul></div>
</div>
<div class="quote"><b>Suggested wording.</b> "On PatchEval's 46-CVE validation split (Qwen3.8-27B, 4 samples), our OpenCode
harness, with continue-on-length plus a security-patching profile, raises pass@1 from {pct(pk['B1']['pass@1'])}% for
plain OpenCode in the same environment to {pct(pk['full']['pass@1'])}% ({pp(FULL_B1)}, 95% CI {ci(FULL_B1)}, paired
permutation p = {FULL_B1['p_permutation']:.2f}). Continue-on-length alone contributes {pp(CONT)}
(p = {CONT['p_permutation']:.3f}); the security profile adds {PROF_C['diff'] * 100:+.1f} to
{PROF_NC['diff'] * 100:+.1f} pp (not significant) at {cost_delta()['usd_ep_list'] * 100:+.0f}% cost per episode. {"Held-out confirmation on nexus cyber-training tasks is in progress." if not NX else f"On 100 held-out nexus cyber-training tasks the same harness did not help ({pct(NX['arms']['R2']['pass_at_k']['pass@1'])}% vs {pct(NX['arms']['baseline']['pass_at_k']['pass@1'])}%, {pp(NXD)}, 95% CI {ci(NXD)}), mainly because its longer workflow hit nexus's 40-minute agent cap more often."}"</div>

<h2>3. What differs between the configurations</h2>
<div class="panel scroll"><table>
<thead><tr><th>Type</th><th></th><th>B0</th><th>v1</th><th>B1</th><th>B1 + cont.</th><th>Full harness</th></tr></thead>
<tbody>
{feature_rows}
</tbody></table></div>
<div class="two">
<div class="panel"><h3>v1 profile: adapted from codex-security / deepsec</h3>
<ul>
<li>Priorities: close the boundary, but also <i>preserve legitimate behavior and compatibility</i> and <i>keep checks passing</i>.</li>
<li>A long 7-step workflow with a separate investigation pass, and a review checklist that is read <i>before</i> editing.</li>
<li>One large per-CWE reference file (15 kB).</li>
<li>Ran only in the original environment, where its <code>skill</code> tool could not load.</li>
</ul></div>
<div class="panel"><h3>secpatch2: rewritten after v1's failure analysis</h3>
<ul>
<li><b>Safe-by-default first:</b> vulnerable behavior is not "legitimate", even when a default or a test asserts it.</li>
<li><b>Short, tool-driven reasoning:</b> plans live in <code>todowrite</code>; an edit is applied as soon as it is known; no recalling upstream fixes.</li>
<li><b>Path inventory and variant matrix</b> before fixing, then a table-driven check of the described impact.</li>
<li><b>Regression triage:</b> narrow the fix, never edit tests. Review only once a diff exists.</li>
<li><b>Benchmark-agnostic:</b> 15 small weakness-family files sized for nexus's 30k context.</li>
</ul></div>
</div>

<h2>4. Solve rates</h2>
<div class="two">
<div class="panel"><h3>pass@k</h3>{passk_chart()}
<p class="muted" style="font-size:12.5px">Dashed lines ran in the original environment. B1 and B1 + cont. coincide from
pass@3 on: continuation rescued only CVEs that other samples already solved.</p></div>
<div class="panel"><h3>pass@1 with 95% CI over CVEs</h3>{ci_chart()}
<p class="muted" style="font-size:12.5px">Hollow points are rescorings without continuation (see section 1).</p></div>
</div>
<div class="panel scroll"><table>
<thead><tr><th>Configuration</th><th>Run id</th><th class="num">pass@1</th><th class="num">pass@2</th><th class="num">pass@3</th>
<th class="num">pass@4</th><th class="num">pass@1 95% CI</th><th class="num">Solved per sample</th></tr></thead>
<tbody>
{passk_rows()}
</tbody></table></div>

<h2>5. Where the gain comes from</h2>
<p>This builds up from B0 to the full harness one step at a time. Each step is a paired comparison over the same 46 CVEs
(two-sided sign-flip permutation test, 20,000 draws). The hatched step is environment repair; only the steps after B1 are
yours.</p>
<div class="panel">{waterfall()}</div>
<div class="panel scroll"><table>
<thead><tr><th>Effect</th><th>Comparison</th><th class="num">Δ pass@1</th><th class="num">95% CI</th><th class="num">p</th>
<th class="num">Δ pass@4</th><th class="num">95% CI</th><th class="num">p</th><th class="num">CVEs better / worse</th></tr></thead>
<tbody>
{comparison_rows()}
</tbody></table>
<p class="muted" style="font-size:12.5px">Δ is in percentage points; green means p &lt; 0.05. "Better / worse" counts CVEs
whose per-CVE pass@1 moved. At pass@4 no difference is significant, because most CVEs are solved at least once by every
configuration.</p></div>

<h2>6. How the behavior changed</h2>
<p>Episode-level metrics exist for the four generated runs. B1 shares B1 + cont.'s episodes, which differ only in the
continued ones.</p>
<div class="two">
<div class="panel"><h3>Episode outcomes (184 per run)</h3>{outcomes_chart()}</div>
<div class="panel"><h3>pass@1 by language (%)</h3>
<table><thead><tr><th></th><th class="num">B0</th><th class="num">v1</th><th class="num">B1</th><th class="num">B1+cont</th><th class="num">Full</th></tr></thead><tbody>{lang_rows()}</tbody></table>
<h3 style="margin-top:18px">Full harness: weakness-family files read (episodes)</h3>{class_bars()}</div>
</div>
<div class="panel scroll"><table>
<thead><tr><th></th>{''.join(f'<th class="num">{esc(SHORT[a])}</th>' for a in GENERATED)}</tr></thead>
<tbody>
{behavior_rows()}
</tbody></table></div>
<ul>
<li><b>v1 did not help</b> ({(pk['v1']['pass@1'] - pk['B0']['pass@1']) * 100:+.1f} pp vs B0, not significant). Its
<code>skill</code> tool failed in every episode because ripgrep was missing. Its longer guidance produced more output-limit
stops (15 against 8), and each of those ends the session with an empty patch.</li>
<li><b>Continuation removed every empty patch.</b> It rescued 9 episodes with the plain profile and 8 with secpatch2, and
harmed none.</li>
<li><b>secpatch2 produced the fewest wrong patches</b> (38, against 46–55). It works longer: 64 tool calls on average, 13.4
median minutes, and +33% input tokens over B1 + cont.</li>
<li><b>New failure mode: timeouts.</b> The full harness had 9, against 5 for B1 + cont. With a working Python environment,
agents sometimes ran whole test suites or servers.</li>
</ul>

<h2>7. Cost</h2>
<p>All four generated runs used the same server (vLLM 0.29.0, 8 × H200, MTP 4, identical argv) and 48 parallel agent
containers. Dollar figures apply OpenRouter list prices for Qwen3.8-27B (${PRICE_IN} per 1M input, ${PRICE_CACHED} per 1M
cached input, ${PRICE_OUT} per 1M output), the same basis as the earlier cost report. They are a common yardstick, not the
self-hosted cost; GPU-hours are the self-hosted cost. B1 has no cost of its own because it is a rescoring of B1 + cont.;
without continuation, the 13 continued episodes would have stopped early, so B1 costs slightly less.</p>
<div class="cards">
  <div class="card"><div class="k">Full vs B1 + cont.: input tokens / episode</div><div class="v">{cost_delta()['in_ep'] * 100:+.0f}%</div>
    <div class="d">{cost['B1c']['in_ep'] / 1e6:.2f} M → {cost['full']['in_ep'] / 1e6:.2f} M; uncached {cost_delta()['uncached_ep'] * 100:+.0f}%</div></div>
  <div class="card"><div class="k">Output tokens / episode</div><div class="v">{cost_delta()['out_ep'] * 100:+.0f}%</div>
    <div class="d">{cost['B1c']['out_ep'] / 1e3:.1f} k → {cost['full']['out_ep'] / 1e3:.1f} k</div></div>
  <div class="card"><div class="k">Turns / agent time per episode</div><div class="v">{cost_delta()['requests_ep'] * 100:+.0f}% / {cost_delta()['agent_min_mean'] * 100:+.0f}%</div>
    <div class="d">{cost['B1c']['requests_ep']:.1f} → {cost['full']['requests_ep']:.1f} requests; {cost['B1c']['agent_min_mean']:.1f} → {cost['full']['agent_min_mean']:.1f} min</div></div>
  <div class="card"><div class="k">$ per solved episode (list)</div><div class="v">{cost_delta()['usd_solved_list'] * 100:+.0f}%</div>
    <div class="d">${cost['B1c']['usd_solved_list']:.3f} → ${cost['full']['usd_solved_list']:.3f}; GPU-hours {cost_delta()['gpu_hours'] * 100:+.0f}%</div></div>
</div>
<div class="two">
<div class="panel"><h3>Cost per episode vs pass@1</h3>{cost_chart()}
<p class="muted" style="font-size:12.5px">Filled points use list price; hollow points apply the cache discount to the
measured prefix-cache hits. Vertical bars are pass@1 95% CIs.</p></div>
<div class="panel"><h3>Reading it</h3><ul>
<li><b>Continuation is nearly free.</b> B1 + cont. costs no more per episode than B0
(${cost['B1c']['usd_ep_list']:.3f} vs ${cost['B0']['usd_ep_list']:.3f}) and is cheaper per solved episode
(${cost['B1c']['usd_solved_list']:.3f} vs ${cost['B0']['usd_solved_list']:.3f}). It continued only 13 of 184 episodes.</li>
<li><b>secpatch2 costs more per episode.</b> Input is {cost_delta()['in_ep'] * 100:+.0f}%, because longer sessions resend the
growing context each turn. Output is only {cost_delta()['out_ep'] * 100:+.0f}%. Per solved episode the increase is
{cost_delta()['usd_solved_list'] * 100:+.0f}%.</li>
<li><b>Input dominates list-price cost:</b> {input_share(cost['full']) * 100:.0f}% for the full harness. Output is about
1% of tokens but is priced 47× higher. The prefix cache serves {cost['full']['cache_rate'] * 100:.0f}% of input, which
roughly halves the cost when discounted.</li>
<li><b>Wall-clock and GPU-hours</b> rise less ({cost_delta()['wall_hours'] * 100:+.0f}%) than per-episode time
({cost_delta()['agent_min_mean'] * 100:+.0f}%). With 48 parallel containers, a sample ends when its slowest episode
does, and samples that include a timed-out episode take 40–47 minutes.</li>
</ul></div>
</div>
<div class="panel scroll"><table>
<thead><tr><th></th>{''.join(f'<th class="num">{esc(SHORT[a])}</th>' for a in GENERATED)}</tr></thead>
<tbody>
{cost_rows()}
</tbody></table></div>

<h2>8. Per-CVE solves (out of 4 samples)</h2>
<div class="panel scroll" style="max-width:780px">{heatmap()}
<div class="legend"><span><i style="background:var(--h0)"></i>0</span><span><i style="background:var(--h1)"></i>1</span>
<span><i style="background:var(--h2)"></i>2</span><span><i style="background:var(--h3)"></i>3</span><span><i style="background:var(--h4)"></i>4</span></div></div>

{nexus_section()}
<h2>{10 if NX else 9}. Caveats</h2>
<ul>
<li><b>Optimistic numbers.</b> The secpatch2 working method was derived from B0 and v1 failures on these same 46 CVEs.
{"A held-out check on nexus is in progress." if not NX else f"The held-out check on 100 nexus tasks (section 9) found no gain ({pp(NXD)}, p = {NXD['p_permutation']:.2f}). There, both arms got the same ripgrep fallback, consistent with treating ripgrep as an environment fix."}</li>
<li><b>Small sample.</b> Per-configuration 95% CIs are about ±10–11 pp. Against B1, only continuation is significant; the full
harness is at p = {FULL_B1['p_permutation']:.2f}.</li>
<li><b>Two kinds of contrast.</b> Comparisons between a run and its own rescoring (B1 vs B1 + cont., B1 + secpatch2 vs Full)
isolate continuation within the same trajectories. Comparisons between runs (Full vs B1, B1 + secpatch2 vs B1) also carry
run-to-run sampling noise.</li>
<li><b>Bundled profile changes.</b> secpatch2 changes the rules, the skill and the family files together, so this does not
attribute its effect to any one of them.</li>
<li><b>Continuation is generic.</b> It would help any OpenCode setup that hits output-limit stops, so present it as harness
engineering rather than security expertise.</li>
</ul>

<footer>
Generated by <code>docs/opencode_harness_v1_r1_r2/build_report.py</code> from saved runs; no generation or evaluation was run.
Data: <code>patcheval/exp_agent/agent_runs/analysis_reports/20261006_051507-opencode-secpatch2-val46/</code> (episodes,
comparisons including <code>comparisons/R2_vs_R1nocont</code>, the Full − B1 test) and <code>…/20261006_001319-opencode-secpatch-val46/</code> (v1).
Profiles: <code>harness_profiles/opencode/secpatch/</code> (v1) and <code>harness_profiles/opencode/secpatch2/</code>.
Continuation wrapper: <code>patcheval/exp_agent/container/opencode_continue.sh</code>. Held-out nexus data: <code>cyber-train/docs/arthur/qwen38-27b-secpatch2-iid100/</code>.
</footer>
</main>
</body>
</html>
"""
OUT.write_text(page)
print(f"wrote {OUT.relative_to(REPO)} ({len(page) // 1024} KiB)")
