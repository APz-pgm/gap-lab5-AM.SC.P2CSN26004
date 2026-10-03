#!/usr/bin/env python3
"""
harness.py: Lab 5 marking script.

    python harness.py path/to/student_repo
    python harness.py --all path/to/all_submissions --out results/

Runs 19 checks worth 5.0 marks. No network. No reading anyone's code.

It works on a temporary copy of the repo and overwrites the copy's
client.py and stub_client.py with its own, so a student who edited the
stub gains nothing. The student's own samples/*.json and observability.py
are kept: the samples define their schema, and observability.py is theirs
to extend.

Shipped to students unchanged, under the name check.py. Without the
reference_files/ folder beside it, it prints "self-check mode" and takes
their supplied files as they are.

Requires the marker's Python to have: pydantic, python-dotenv, openai.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "reference_files"     # holds client.py and stub_client.py
SELF_CHECK = not REFERENCE.exists()      # student copy: no reference dir shipped


def ref(name: str, repo: Path) -> Path:
    """The authoritative copy of a supplied file, or the repo's own in self-check."""
    return (repo / name) if SELF_CHECK else (REFERENCE / name)


TIMEOUT = 60          # one run of main.py, including any backoff sleeps

REQUIRED_FILES = [
    "main.py",
    "client.py",
    "stub_client.py",
    "observability.py",
    "check.py",
    "SPEC.md",
    "DECISIONS.md",
    "QUESTION.md",
    "REPORT.md",
    ".gitignore",
    "requirements.txt",
    ".env.example",
    "samples/valid_response.json",
    "samples/invalid_response.json",
    "samples/run_log_sample.jsonl",
]

SPEC_HEADINGS = [
    "# What it does",
    "# Inputs",
    "# Outputs",
    "# Failure cases",
    "# Acceptance checks",
    "# Out of scope",
]

REPORT_HEADINGS = ["# Cost", "# Speed", "# Method"]

LOG_FIELDS = ["timestamp", "run_id", "model", "status", "prompt_tokens",
              "completion_tokens", "latency_ms", "cost_usd", "tokens_estimated"]

OUTCOMES = {"ok", "invalid_output", "refused", "error"}

# mode -> (expected last stdout line, expected exit code)
BEHAVIOUR = {
    "ok":        ("ok", 0),
    "fenced":    ("ok", 0),
    "preamble":  ("ok", 0),
    "malformed": ("invalid_output", 1),
    "badshape":  ("invalid_output", 1),
    "empty":     ("invalid_output", 1),
    "refused":   ("refused", 1),
    "error":     ("error", 1),
    "flaky":     ("ok", 0),
    "ratelimit": ("error", 1),
}

ORIGINAL_MODES = ["ok", "fenced", "preamble", "malformed",
                  "badshape", "empty", "refused", "error"]

TOPIC = "photosynthesis"

SECRET_KEYS = {"api_key", "apikey", "llm_api_key", "authorization", "key",
               "token", "secret"}
SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_\-]{12,}|Bearer\s+[A-Za-z0-9._\-]{12,})")

MIN_SAMPLE_LINES = 20


# --------------------------------------------------------------------------

@dataclass
class Check:
    group: str
    name: str
    marks: float
    passed: bool = False
    note: str = ""


@dataclass
class Result:
    repo: str
    checks: list[Check] = field(default_factory=list)

    @property
    def awarded(self) -> float:
        return round(sum(c.marks for c in self.checks if c.passed), 2)

    @property
    def total(self) -> float:
        return round(sum(c.marks for c in self.checks), 2)


@dataclass
class Run:
    mode: str
    last: str
    code: int
    err: str
    records: list[dict] = field(default_factory=list)   # parsed run_log.jsonl
    bad_lines: int = 0                                  # lines that did not parse
    calls: int = 0                                      # real calls, from the stub
    log_written: bool = False


def git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=20,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_log(path: Path) -> tuple[list[dict], int]:
    """Parse run_log.jsonl. Returns (records, number of unparseable lines)."""
    if not path.exists():
        return ([], 0)
    records, bad = [], 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            bad += 1
    return (records, bad)


def run_app(workdir: Path, topic: str, mode: str) -> Run:
    """Run main.py once against one stub mode, with a clean log."""
    env = dict(os.environ)
    env["LLM_API_KEY"] = "not-needed"
    env["LLM_BASE_URL"] = "local://stub"
    env["LLM_MODEL"] = f"stub:{mode}"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("LLM_PROVIDER", None)
    env.pop("RUN_LOG_PATH", None)

    call_log = workdir / ".stub_calls.log"
    run_log = workdir / "run_log.jsonl"
    call_log.unlink(missing_ok=True)
    run_log.unlink(missing_ok=True)

    try:
        proc = subprocess.run(
            [sys.executable, "main.py", topic],
            cwd=str(workdir), env=env, capture_output=True,
            text=True, timeout=TIMEOUT,
        )
        last_code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        last_code, out, err = -1, "", f"no output within {TIMEOUT}s"

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    last = lines[-1] if lines else "<no output>"
    records, bad = read_log(run_log)
    calls = 0
    if call_log.exists():
        calls = len([ln for ln in call_log.read_text().splitlines() if ln.strip()])

    return Run(mode=mode, last=last, code=last_code, err=err.strip()[-300:],
               records=records, bad_lines=bad, calls=calls,
               log_written=run_log.exists())


# ---------------------------------------------------------------- log rules

def field_problems(rec: dict) -> list[str]:
    """Everything wrong with one log line. Empty list means it is fine."""
    bad = []
    missing = [f for f in LOG_FIELDS if f not in rec]
    if missing:
        bad.append("missing " + ",".join(missing))
        return bad

    for f in ("timestamp", "run_id", "model"):
        if not isinstance(rec[f], str) or not rec[f].strip():
            bad.append(f"{f} is not a non-empty string")

    if rec["status"] not in OUTCOMES:
        bad.append(f"status {rec['status']!r} is not one of the four words")

    if isinstance(rec["latency_ms"], bool) or not isinstance(rec["latency_ms"], int):
        bad.append("latency_ms is not an int")

    for f in ("prompt_tokens", "completion_tokens"):
        v = rec[f]
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int):
            bad.append(f"{f} is neither an int nor null")

    v = rec["cost_usd"]
    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
        bad.append("cost_usd is neither a number nor null")

    if not isinstance(rec["tokens_estimated"], bool):
        bad.append("tokens_estimated is not true or false")

    # An error is not billed, so its cost is an absence, not a zero.
    if rec.get("status") == "error" and rec["cost_usd"] is not None:
        bad.append("cost_usd is not null on an error line")

    # null is for an absence. A reply that arrived has counts.
    if rec.get("status") in {"ok", "invalid_output", "refused"}:
        nulls = [f for f in ("prompt_tokens", "completion_tokens", "cost_usd")
                 if rec[f] is None]
        if nulls:
            bad.append("null on a call that returned a reply: " + ",".join(nulls))

    return bad


def secret_problems(rec: dict) -> list[str]:
    bad = []
    for k, v in rec.items():
        if k.lower() in SECRET_KEYS:
            bad.append(f"field named {k!r}")
        if isinstance(v, str) and SECRET_VALUE.search(v):
            bad.append(f"key-like value in {k!r}")
    return bad


# --------------------------------------------------------------------------

def check_repo(repo: Path) -> Result:
    res = Result(repo=repo.name)
    add = res.checks.append

    # ---- Group A: repo and contract (1.2) --------------------------------
    missing = [f for f in REQUIRED_FILES if not (repo / f).exists()]
    add(Check("A", "Required files present", 0.3, not missing,
              "" if not missing else "missing: " + ", ".join(missing)))

    student_client = repo / "client.py"
    same = student_client.exists() and (
        SELF_CHECK or sha256(student_client) == sha256(ref("client.py", repo))
    )
    add(Check("A", "client.py unmodified", 0.2, same,
              "" if same else "client.py differs from the supplied file"))

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8", errors="ignore") \
        if (repo / ".gitignore").exists() else ""
    ignored_env = ".env" in gitignore
    ignored_log = "run_log.jsonl" in gitignore
    history = git(repo, "log", "--all", "--name-only", "--pretty=format:")
    tracked = {ln.strip() for ln in history.splitlines()}
    committed_env = ".env" in tracked
    committed_log = "run_log.jsonl" in tracked
    gate = ignored_env and ignored_log and not committed_env and not committed_log
    add(Check("A", ".env and run_log.jsonl ignored, neither ever committed",
              0.3, gate,
              "" if gate else "; ".join(filter(None, [
                  "" if ignored_env else "'.env' not in .gitignore",
                  "" if ignored_log else "'run_log.jsonl' not in .gitignore",
                  ".env appears in git history" if committed_env else "",
                  "run_log.jsonl appears in git history" if committed_log else "",
              ]))))

    spec_lines = [ln.strip() for ln in
                  (repo / "SPEC.md").read_text(encoding="utf-8", errors="ignore").splitlines()] \
        if (repo / "SPEC.md").exists() else []
    report_lines = [ln.strip() for ln in
                    (repo / "REPORT.md").read_text(encoding="utf-8", errors="ignore").splitlines()] \
        if (repo / "REPORT.md").exists() else []
    absent = [h for h in SPEC_HEADINGS if h not in spec_lines] + \
             [h for h in REPORT_HEADINGS if h not in report_lines]
    add(Check("A", "SPEC.md has its six headings, REPORT.md its three",
              0.2, not absent,
              "" if not absent else "missing headings: " + ", ".join(absent)))

    commits = git(repo, "rev-list", "--count", "HEAD")
    n_commits = int(commits) if commits.isdigit() else 0
    tagged = "lab-5-submission" in git(repo, "tag", "-l", "lab-5-submission")
    git_ok = n_commits >= 5 and tagged
    add(Check("A", "Git: >=5 commits and the lab-5-submission tag", 0.2, git_ok,
              "" if git_ok else
              f"commits={n_commits}, tag={'yes' if tagged else 'no'}"))

    # ---- Behavioural checks on a temp copy -------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(repo, work, ignore=shutil.ignore_patterns(".git"))
        if not SELF_CHECK:
            shutil.copy2(REFERENCE / "stub_client.py", work / "stub_client.py")
            shutil.copy2(REFERENCE / "client.py", work / "client.py")
        (work / ".env").unlink(missing_ok=True)

        runs = {m: run_app(work, TOPIC, m) for m in BEHAVIOUR}

        # ---- Group B: the four outcomes still hold (1.0) -----------------
        per = round(1.0 / len(ORIGINAL_MODES), 3)
        for mode in ORIGINAL_MODES:
            want_line, want_code = BEHAVIOUR[mode]
            r = runs[mode]
            ok = (r.last == want_line) and (r.code == want_code)
            add(Check("B", f"stub:{mode} -> {want_line} (exit {want_code})",
                      per, ok,
                      "" if ok else f"got {r.last!r} exit {r.code}"
                      + (f"; stderr: {r.err.splitlines()[-1]}" if r.err else "")))

        # ---- Group C: the log itself (1.3) -------------------------------
        r_ok = runs["ok"]
        c1 = r_ok.log_written and r_ok.bad_lines == 0 and len(r_ok.records) >= 1
        add(Check("C", "run_log.jsonl written, one parseable JSON object per line",
                  0.3, c1,
                  "" if c1 else
                  ("no run_log.jsonl after a stub:ok run" if not r_ok.log_written
                   else f"{r_ok.bad_lines} line(s) did not parse, "
                        f"{len(r_ok.records)} did")))

        all_recs = [(m, rec) for m, r in runs.items() for rec in r.records]
        field_bad = [(m, field_problems(rec)) for m, rec in all_recs]
        field_bad = [(m, p) for m, p in field_bad if p]
        add(Check("C", "Every line carries the nine fields, correctly typed",
                  0.4, not field_bad and bool(all_recs),
                  "" if all_recs and not field_bad else
                  ("no log lines at all" if not all_recs else
                   f"{len(field_bad)} bad line(s), e.g. stub:{field_bad[0][0]}: "
                   + "; ".join(field_bad[0][1])[:120])))

        mismatched = []
        for m, r in runs.items():
            if not r.records:
                mismatched.append(f"stub:{m} wrote no line")
                continue
            final = r.records[-1].get("status")
            if r.last in OUTCOMES and final != r.last:
                mismatched.append(f"stub:{m} printed {r.last} but last line says {final}")
        add(Check("C", "The last line's status matches the word printed",
                  0.3, not mismatched,
                  "" if not mismatched else "; ".join(mismatched[:3])))

        multi = [f"stub:{m} wrote {len({rec.get('run_id') for rec in r.records})} run_ids"
                 for m, r in runs.items()
                 if len({rec.get("run_id") for rec in r.records}) > 1]
        add(Check("C", "One run_id per run of main.py", 0.3,
                  not multi and bool(all_recs),
                  "" if all_recs and not multi else
                  ("no log lines at all" if not all_recs else "; ".join(multi[:3]))))

        # ---- Group D: granularity and retries (1.0) ----------------------
        r = runs["flaky"]
        st = [rec.get("status") for rec in r.records]
        d1 = (r.last == "ok" and r.code == 0 and len(st) >= 2
              and st[0] == "error" and st[-1] == "ok"
              and len(st) == r.calls)
        add(Check("D", "stub:flaky -> retried, ended ok, every attempt logged",
                  0.4, d1,
                  "" if d1 else
                  f"got {r.last!r} exit {r.code}; log statuses {st}; "
                  f"{r.calls} real call(s)"))

        r = runs["ratelimit"]
        st = [rec.get("status") for rec in r.records]
        d2 = (r.last == "error" and r.code == 1 and 2 <= len(st) <= 3
              and all(s == "error" for s in st) and len(st) == r.calls)
        add(Check("D", "stub:ratelimit -> capped at 2-3 attempts, each logged, "
                       "ends error", 0.3, d2,
                  "" if d2 else
                  f"got {r.last!r} exit {r.code}; log statuses {st}; "
                  f"{r.calls} real call(s)"))

        r = runs["malformed"]
        st = [rec.get("status") for rec in r.records]
        d3 = (r.last == "invalid_output" and len(st) == 2
              and all(s == "invalid_output" for s in st) and r.calls == 2)
        add(Check("D", "stub:malformed -> exactly one repair retry, both lines "
                       "invalid_output", 0.3, d3,
                  "" if d3 else
                  f"got {r.last!r}; log statuses {st}; {r.calls} real call(s)"))

        # ---- Group E: the input gate (0.3) -------------------------------
        r = run_app(work, "", "ok")
        gate_ok = (r.code == 2) and (r.calls == 0) and not r.records
        add(Check("E", "empty topic -> exit 2, no model call, no log line",
                  0.3, gate_ok,
                  "" if gate_ok else
                  f"exit {r.code}; {r.calls} call(s); {len(r.records)} log line(s)"))

    # ---- Group F: the committed sample log (0.2) -------------------------
    sample = repo / "samples" / "run_log_sample.jsonl"
    recs, bad = read_log(sample)
    problems = []
    if not sample.exists():
        problems.append("samples/run_log_sample.jsonl missing")
    else:
        if bad:
            problems.append(f"{bad} line(s) do not parse")
        if len(recs) < MIN_SAMPLE_LINES:
            problems.append(f"only {len(recs)} lines, {MIN_SAMPLE_LINES} needed")
        shape = [p for rec in recs for p in field_problems(rec)]
        if shape:
            problems.append("fields: " + shape[0])
        leaks = [p for rec in recs for p in secret_problems(rec)]
        if leaks:
            problems.append("possible secret: " + leaks[0])
    add(Check("F", f"samples/run_log_sample.jsonl: >={MIN_SAMPLE_LINES} good "
                   f"lines, nothing secret", 0.2, not problems,
              "; ".join(problems[:2])))

    return res


# --------------------------------------------------------------------------

def render(res: Result) -> str:
    out = [f"# Lab 5: {res.repo}", "",
           f"**Harness: {res.awarded} / {res.total}**", "",
           "| | Check | Marks | Result | Note |",
           "|---|---|--:|:-:|---|"]
    for c in res.checks:
        out.append(f"| {c.group} | {c.name} | {c.marks} | "
                   f"{'PASS' if c.passed else 'FAIL'} | {c.note} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--all", action="store_true",
                    help="treat path as a directory of student repos")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    repos = sorted(p for p in root.iterdir() if p.is_dir()) if args.all else [root]

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    summary = []

    if SELF_CHECK:
        print("[self-check mode] client.py and stub_client.py are taken as-is.\n"
              "The marking run uses the supplied originals.\n")

    for repo in repos:
        res = check_repo(repo)
        print(render(res))
        print()
        (outdir / f"{res.repo}.md").write_text(render(res), encoding="utf-8")
        summary.append({"repo": res.repo, "awarded": res.awarded,
                        "total": res.total,
                        "checks": [c.__dict__ for c in res.checks]})

    (outdir / "summary.json").write_text(json.dumps(summary, indent=2),
                                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
