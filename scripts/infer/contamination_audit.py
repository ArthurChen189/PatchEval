#!/usr/bin/env python3
"""Flag generation episodes that fetched information about their own fix (stdlib only).

An episode (one CVE in one sample run) is contaminated when, during generation, it
*successfully*:
  (a) fetched the target project's upstream content: a URL naming the dataset repo
      (owner/name) on a code host or CDN, a registry file of the target package, or a
      git clone/fetch/pull/ls-remote that reached a remote;
  (b) fetched a vulnerability advisory or CVE page (NVD, MITRE, cve.org, GHSA,
      huntr, Snyk, OSV, ...), or ran a web search;
  (c) installed or downloaded the target package itself (pip, npm, yarn, pnpm, go,
      gem), which can place the fixed version on disk.

Shell commands (Codex `command_execution`, OpenCode `bash`) and OpenCode `webfetch`
calls are read from each archived task: the OpenCode session database when present
(it also holds subagent calls), otherwise the harness stdout stream. A fetch counts
only when it exited 0 without network errors or not-found responses; commands whose
output was redirected cannot be checked and count as successful. Every flagged event
is listed with its evidence for manual review; `--review` drops false positives.

usage:
  python -m scripts.infer.contamination_audit --run [LABEL=]GENERATION_DIR ... --out DIR
      [--dataset patcheval/datasets/patcheval_verified.json] [--review review.json]
"""

import argparse
import csv
import json
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK_DIR = re.compile(r"\d+-patcheval_(?P<cve>.+)$")
URL = re.compile(r"(?:https?://|git@)[^\s'\"|)<>\\`;,]+", re.I)
# Commands that retrieve remote content; URLs that only appear in strings or greps are ignored.
FETCHER = re.compile(r"\b(curl|wget|httpie|http\s+(GET|HEAD|POST)|urlopen|urllib|requests\.(get|post|head)"
                     r"|http\.client|https?\.get\(|fetch\(|aiohttp|git\s+(clone|fetch|pull|ls-remote|archive)"
                     r"|svn\s+(co|export)|go\s+(get|mod\s+download|install)|pip3?\s+(download|install)"
                     r"|npm\s+(pack|install|i|ci))\b", re.I)
GIT_REMOTE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(clone|fetch|pull|ls-remote)\b")
INSTALL = re.compile(r"\b(?:pip3?|python3?\s+-m\s+pip|uv\s+pip)\s+(?:install|download)\b(?P<pip>[^;&|\n]*)"
                     r"|\b(?:npm\s+(?:install|i|ci|add|pack)|yarn\s+add|pnpm\s+(?:add|install))\b(?P<npm>[^;&|\n]*)"
                     r"|\bgo\s+(?:get|install|mod\s+download)\b(?P<go>[^;&|\n]*)"
                     r"|\bgem\s+install\b(?P<gem>[^;&|\n]*)", re.I)
CODE_HOSTS = ("github.com", "raw.githubusercontent.com", "api.github.com", "patch-diff.githubusercontent.com",
              "codeload.github.com", "objects.githubusercontent.com", "gist.github.com", "gitlab.com",
              "bitbucket.org", "cdn.jsdelivr.net", "unpkg.com", "registry.npmjs.org", "www.npmjs.com",
              "npmjs.com", "pypi.org", "files.pythonhosted.org", "proxy.golang.org", "pkg.go.dev",
              "sum.golang.org", "rubygems.org", "deps.dev", "sourcegraph.com", "googlesource.com")
ADVISORY = re.compile(r"(nvd\.nist\.gov|cve\.mitre\.org|(www\.)?cve\.org|osv\.dev|huntr\.(dev|com)|snyk\.io"
                      r"|github\.com/advisories|/security/advisories|api\.github\.com/advisories|GHSA-"
                      r"|vuldb\.com|cvedetails\.com|exploit-db\.com|security-tracker\.debian\.org"
                      r"|access\.redhat\.com/security|ubuntu\.com/security|npmjs\.com/advisories"
                      r"|vuln\.go\.dev|pkg\.go\.dev/vuln|rustsec\.org|security\.netapp\.com"
                      r"|seclists\.org|openwall\.com|cve\.circl\.lu|vulners\.com)", re.I)
# Specific failure phrases only: fetched source code often mentions SSL, certificates, or timeouts.
NET_ERROR = re.compile(r"(Could not resolve host|Temporary failure in name resolution|Name or service not known"
                       r"|Network is unreachable|Connection refused|Connection timed out|Operation timed out"
                       r"|Read timed out|ENOTFOUND|EAI_AGAIN|ENETUNREACH|ECONNREFUSED|getaddrinfo .*failed"
                       r"|dial tcp .*(i/o timeout|refused|unreachable)|no such host"
                       r"|Failed to establish a new connection|unable to access '|Could not read from remote"
                       r"|curl: \(\d+\)|wget: unable to resolve|Failed to connect to|SSL certificate problem"
                       r"|certificate verify failed|Max retries exceeded)", re.I)
NOT_FOUND = re.compile(r"(404: Not Found|\"message\":\s*\"(Not Found|No commit found)|HTTP/[\d.]+ [45]\d\d"
                       r"|\"status\":\s*\"[45]\d\d\"|ERROR 404|404 Not Found|No matching distribution"
                       r"|npm (ERR|error)!? (code )?E404|ERROR: Could not find a version|fatal: |Repository not found"
                       r"|unknown revision|no matching versions)", re.I)
# Installer progress lines (case-sensitive: pytest prints "collecting ...").
INSTALL_DOWNLOADED = re.compile(r"(?m)^\s*(Successfully installed|Downloading |Collecting |Saved |Successfully downloaded"
                                r"|go: downloading |Fetching |added \d+ packages?|changed \d+ packages?|\+ \S+@)")
INSTALL_LINE = re.compile(r"(?m)^\s*(?:Successfully installed|Downloading|Collecting|Saved|Successfully downloaded"
                          r"|go: downloading|Fetching|\+)\s+(?P<what>.+)$")


def target_of(sample):
    """Lower-case (owner, name, package-name variants) of a dataset case's repository."""
    repo = str(sample.get("repo") or "").rstrip("/")
    repo = repo[:-4] if repo.endswith(".git") else repo
    parts = [p for p in re.split(r"[/:]", repo) if p]
    owner, name = (parts[-2].lower(), parts[-1].lower()) if len(parts) >= 2 else ("", parts[-1].lower() if parts else "")
    variants = {name, name.replace("_", "-"), name.replace("-", "_")}
    for prefix in ("node-", "python-", "py-", "go-"):
        if name.startswith(prefix) and len(name) > len(prefix) + 2:
            variants.add(name[len(prefix):])
    for suffix in (".js", "-js", ".py", "-py", "-go"):
        if name.endswith(suffix) and len(name) > len(suffix) + 2:
            variants.add(name[: -len(suffix)])
    return {"owner": owner, "name": name, "packages": {v for v in variants if v}, "repo": repo}


def _host(url):
    match = re.match(r"(?:https?://|git@)([^/:]+)", url, re.I)
    return match.group(1).lower() if match else ""


def _fetched_ok(exit_code, output):
    """A retrieval that plausibly returned content: exit 0, no network error, no not-found body."""
    if str(exit_code) not in ("0", "None", "") and exit_code is not None:
        return False
    return not NET_ERROR.search(output or "") and not NOT_FOUND.search(output or "")


def _install_targets(command, target):
    """Target package names that an install/download command names explicitly."""
    hits = set()
    for match in INSTALL.finditer(command):
        args = " ".join(v for v in match.groupdict().values() if v)
        for token in re.split(r"[\s,]+", args):
            token = token.strip("'\"").lower()
            if not token or token.startswith("-"):
                continue
            # github.com/owner/name (go), name==1.2, name@1.2, name[extra], @scope/name
            path = token.split("@")[0] if not token.startswith("@") else "@" + token[1:].split("@")[0]
            base = re.split(r"[=<>!~\[;]", path)[0]
            full = f"{target['owner']}/{target['name']}"
            scoped = base.startswith("@") and base.split("/")[-1] in target["packages"]  # @scope/name
            if base in target["packages"] or base == full or base.endswith("/" + full) or scoped:
                hits.add(base)
    return hits


def classify(event, target):
    """Contamination rules this event meets (empty when clean), plus whether it used the network at all."""
    text, output, exit_code = event.get("command") or "", event.get("output") or "", event.get("exit_code")
    rules, network = [], False
    if event["source"] == "web_search":
        done = event.get("status") == "completed"
        return (["b:web-search"], True) if done else ([], False)
    if event["source"] == "webfetch":
        url = (event.get("url") or "").lower()
        if event.get("status") != "completed":
            return [], False
        urls, ok = [url], True
    else:
        urls = [u.rstrip(".") for u in URL.findall(text)] if FETCHER.search(text) else []
        urls = [u for u in urls if _host(u) and not _host(u).startswith(("172.", "127.", "localhost", "0.0.0.0"))
                and _host(u) not in ("example.com", "evil.com", "attacker.com")]
        ok = _fetched_ok(exit_code, output)
    owner_name = f"{target['owner']}/{target['name']}"
    for url in urls:
        low, host = url.lower(), _host(url)
        network = network or ok
        if not ok:
            continue
        if ADVISORY.search(low):
            rules.append("b:advisory")
        elif host in CODE_HOSTS or host.endswith((".github.com", ".githubusercontent.com")):
            path = low.split(host, 1)[-1]
            if owner_name and owner_name in path:
                rules.append("a:target-upstream")
            elif host in ("cdn.jsdelivr.net", "unpkg.com", "registry.npmjs.org", "www.npmjs.com", "npmjs.com",
                          "pypi.org", "files.pythonhosted.org", "rubygems.org") and any(
                    re.search(rf"(^|[/@])({re.escape(p)})([/@-]|$)", path) for p in target["packages"]):
                rules.append("a:target-package-file")
    if event["source"] == "shell":
        # A URL-less fetch/pull uses the repository's configured remote (the target's
        # upstream); count it only when git reports contacting a remote host. Local
        # clones and test git daemons on 127.0.0.1 are not remote access.
        remote = re.search(r"(?m)^From (?:https?://|git@|ssh://|git://)(?!127\.|localhost)\S+", output)
        if GIT_REMOTE.search(text) and remote and _fetched_ok(exit_code, output):
            rules.append("a:git-remote")
            network = True
        targets = _install_targets(text, target)
        if INSTALL.search(text) and _fetched_ok(exit_code, output):
            network = network or bool(INSTALL_DOWNLOADED.search(output))
            if targets and _target_downloaded(output, target):
                rules.append("c:target-package-install")
    return sorted(set(rules)), network


def _norm(name):
    return re.sub(r"[-_.]+", "-", name.lower())


def _target_downloaded(output, target):
    """Did an install fetch the target package? Unknown (no installer progress shown) counts as yes.

    pip prints "Requirement already satisfied" for packages it leaves alone, so a run
    that downloaded only other packages is not counted; npm's "added N packages" does
    not name packages and counts.
    """
    names = {_norm(p) for p in target["packages"]}
    lines = [m.group("what") for m in INSTALL_LINE.finditer(output)]
    if any(_norm(word).startswith(tuple(f"{n}-" for n in names)) or _norm(word) in names
           or f"{target['owner']}/{target['name']}" in word.lower()
           for line in lines for word in re.split(r"[\s(),]+", line) if word):
        return True
    if re.search(r"(?m)^\s*(added|changed) \d+ packages?", output):
        return True
    return not INSTALL_DOWNLOADED.search(output) and not re.search(r"Requirement already satisfied", output)


def _opencode_db_events(native):
    db = native / "opencode.db"
    if not db.is_file():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        for suffix in ("", "-wal", "-shm"):
            if (native / f"opencode.db{suffix}").is_file():
                shutil.copy2(native / f"opencode.db{suffix}", Path(tmp) / f"opencode.db{suffix}")
        con = sqlite3.connect(Path(tmp) / "opencode.db")
        try:
            rows = con.execute("SELECT data FROM part").fetchall()
        except sqlite3.DatabaseError:
            return None
        finally:
            con.close()
    events = []
    for (data,) in rows:
        try:
            part = json.loads(data)
        except (TypeError, ValueError):
            continue
        if part.get("type") == "tool":
            events.append(_opencode_event(part))
    return [e for e in events if e]


def _opencode_event(part):
    tool, state = part.get("tool"), part.get("state") or {}
    meta, inp = state.get("metadata") or {}, state.get("input") or {}
    output = str(state.get("output") or meta.get("output") or state.get("error") or "")
    if tool == "bash":
        return {"source": "shell", "command": inp.get("command", ""), "exit_code": meta.get("exit"), "output": output}
    if tool in ("webfetch", "websearch", "codesearch"):
        return {"source": "webfetch" if tool == "webfetch" else "web_search", "url": inp.get("url", ""),
                "command": json.dumps(inp), "status": state.get("status"), "output": output}
    return None


def task_events(task):
    """Shell, webfetch, and web-search events of one archived task."""
    native = _opencode_db_events(task / "native") if (task / "native").is_dir() else None
    if native is not None:
        return native, "opencode-db"
    events = []
    stdout = task / "stdout.jsonl"
    for line in stdout.read_text(errors="replace").splitlines() if stdout.is_file() else []:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "command_execution":
            events.append({"source": "shell", "command": item.get("command", ""), "exit_code": item.get("exit_code"),
                           "output": item.get("aggregated_output", "")})
        elif event.get("type") == "item.completed" and item.get("type") == "web_search":
            events.append({"source": "web_search", "command": json.dumps(item), "status": "completed", "output": ""})
        part = event.get("part") or {}
        if event.get("type") == "tool_use" and part.get("tool"):
            converted = _opencode_event(part)
            if converted:
                events.append(converted)
    return events, "stdout"


def discover(run):
    """Archived tasks of a generation invocation, run, or sample directory (replaced reruns excluded)."""
    tasks = []
    for stdout in sorted(Path(run).glob("**/trajectories/*/stdout.jsonl")):
        if "startup_reruns" in stdout.parts:
            continue
        task = stdout.parent
        match = TASK_DIR.match(task.name)
        if not match:
            continue
        sample = next((p for p in reversed(task.parts) if re.fullmatch(r"sample_\d+", p)), "sample_0")
        tasks.append((sample, match.group("cve"), task))
    return tasks


def audit(runs, dataset, review=None):
    """runs: {label: path}. Returns (episodes, evidence, per_run summary)."""
    cases = {d["cve_id"]: d for d in json.loads(Path(dataset).read_text())}
    dropped = set((review or {}).get("false_positive", []))
    episodes, evidence, summary = {}, [], {}
    for label, path in runs.items():
        counts = Counter()
        seen = set()
        for sample, cve, task in discover(path):
            if (sample, cve) in seen:
                raise ValueError(f"{label}: duplicate archived task for {sample} {cve}")
            seen.add((sample, cve))
            counts["episodes"] += 1
            target = target_of(cases.get(cve, {}))
            events, origin = task_events(task)
            counts[f"read_from_{origin}"] += 1
            used, hit_rules = False, set()
            for event in events:
                rules, network = classify(event, target)
                used = used or network
                if rules:
                    hit_rules.update(rules)
                    evidence.append({"run": label, "sample": sample, "cve": cve, "rules": rules,
                                     "source": event["source"], "exit_code": event.get("exit_code"),
                                     "command": (event.get("url") or event.get("command") or "")[:600],
                                     "output_head": (event.get("output") or "")[:400], "task": str(task)})
            counts["network_used"] += used
            key = f"{label}|{sample}|{cve}"
            if hit_rules and key not in dropped:
                episodes[key] = {"run": label, "sample": sample, "cve": cve, "rules": sorted(hit_rules),
                                 "task": str(task)}
                counts["contaminated"] += 1
            elif hit_rules:
                counts["dropped_by_review"] += 1
        summary[label] = {"path": str(path), **counts,
                          "contaminated_cves": sorted({e["cve"] for e in episodes.values() if e["run"] == label})}
    return episodes, evidence, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="append", required=True, metavar="[LABEL=]DIR",
                        help="Generation invocation, run, or sample directory; repeatable")
    parser.add_argument("--dataset", default=str(ROOT / "patcheval/datasets/patcheval_verified.json"))
    parser.add_argument("--review", help='JSON {"false_positive": ["LABEL|sample_i|CVE", ...], "notes": {...}}')
    parser.add_argument("--out", required=True, help="Directory for episodes.csv, evidence.jsonl, summary.json, "
                                                     "excluded_cves.json")
    args = parser.parse_args(argv)
    runs = {}
    for item in args.run:
        label, sep, path = item.partition("=")
        label, path = (label, path) if sep else (Path(item).name, item)
        if label in runs:
            parser.error(f"duplicate run label {label}")
        runs[label] = Path(path).resolve()
    review = json.loads(Path(args.review).read_text()) if args.review else None
    episodes, evidence, summary = audit(runs, args.dataset, review)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "episodes.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["run", "sample", "cve", "rules", "task"])
        writer.writeheader()
        for row in sorted(episodes.values(), key=lambda r: (r["run"], r["sample"], r["cve"])):
            writer.writerow({**row, "rules": ";".join(row["rules"])})
    (out / "evidence.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evidence), encoding="utf-8")
    by_cve = defaultdict(list)
    for e in episodes.values():
        by_cve[e["cve"]].append(f"{e['run']}|{e['sample']}")
    excluded = {"rule": "a: target upstream content or git remote; b: advisory/CVE page or web search; "
                        "c: install/download of the target package (successful only)",
                "excluded_cves": sorted(by_cve), "episodes_by_cve": {c: sorted(v) for c, v in sorted(by_cve.items())},
                "runs": {label: str(path) for label, path in runs.items()},
                "review": review}
    (out / "excluded_cves.json").write_text(json.dumps(excluded, indent=1) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    for label, info in summary.items():
        print(f"{label}: {info.get('episodes', 0)} episodes, {info.get('network_used', 0)} used the network, "
              f"{info.get('contaminated', 0)} contaminated ({len(info['contaminated_cves'])} CVEs)")
    print(f"{len(by_cve)} CVEs flagged in any run: {out / 'excluded_cves.json'}")


if __name__ == "__main__":
    main()
