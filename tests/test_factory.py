"""Tests for the job state machine, the gate, the installer and the agent files.

`gh` is replaced by a small script that answers from a JSON file, so no network is used.
"""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / "kit"
INSTALL = KIT.parent / "install.sh"

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
args = sys.argv[1:]
open(os.environ["FAKE_GH_LOG"], "a").write(" ".join(args) + "\n")
if args[:2] == ["issue", "list"]:
    print(json.dumps(state["issues"]))
elif args[:2] == ["issue", "view"]:
    n = int(args[2]); i = next(x for x in state["issues"] if x["number"] == n)
    print(json.dumps({**i, "url": f"https://github.com/o/r/issues/{n}", "author": {"login": "someone"}}))
elif args[:2] == ["pr", "view"]:
    n = args[2]; pr = state.get("prs", {}).get(n)
    if pr is None:
        import subprocess
        branches = subprocess.run(["git", "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/factory/"],
                                  capture_output=True, text=True).stdout.split("\n")
        want = state.get("pr_branch", {}).get(n)
        name, oid = next((b.split() for b in branches if b and (want is None or b.split()[0] == want)), ("factory/none", "0"))
        pr = {"number": int(n), "state": "OPEN", "headRefName": name, "headRefOid": oid, "isCrossRepository": False}
    print(json.dumps(pr))
elif args[:2] == ["pr", "merge"]:
    if state.get("merge_fails"): sys.stderr.write("not mergeable\n"); sys.exit(1)
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    run = lambda *a: subprocess.run(a, cwd=r, check=True, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (r / "README.md").write_text("x\n")
    subprocess.run([str(INSTALL), str(r)], check=True, capture_output=True)
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "init")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "gh.json"
    state.write_text(json.dumps({"issues": [{"number": n, "title": t, "updatedAt": "2026-11-21T00:00:00Z"}
                                             for n, t in ((1, "Dark mode"), (2, "Export notes as CSV"), (3, "Fix login redirect"),
                                                          (4, "Change display name"))]}))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("FACTORY_ROOT", str(r))
    return r


def job(repo, *args, ok=True):
    p = subprocess.run([str(repo / "factory" / "bin" / "job"), *args], cwd=repo, capture_output=True, text=True)
    if ok:
        assert p.returncode == 0, p.stderr
    return p


def release_as_person(repo, jid, stage, answer):
    """Run `job release` attached to a pseudo-terminal, typing `answer` at the prompt."""
    import pty
    master, slave = pty.openpty()
    p = subprocess.Popen([str(repo / "factory" / "bin" / "job"), "release", jid, stage], cwd=repo,
                         stdin=slave, stdout=slave, stderr=subprocess.PIPE, text=True)
    os.close(slave)
    os.write(master, (answer + "\n").encode())
    p.wait(timeout=20)
    os.close(master)
    return p


def state(repo, jid):
    return json.loads(job(repo, "show", jid).stdout)


def nxt(repo, jid):
    return json.loads(job(repo, "next", jid).stdout)


def to_review(repo, jid, risk="low", surfaces="", checks="exit 0"):
    job(repo, "intake", jid, "accept", "--risk", risk, *(["--surfaces", surfaces] if surfaces else []))
    (repo / "factory" / "jobs" / jid / "spec.md").write_text("# spec\n")
    job(repo, "advance", jid, "build")
    job(repo, "worktree", jid)
    (repo / "factory" / "checks.sh").write_text(f"#!/usr/bin/env bash\n{checks}\n")
    job(repo, "advance", jid, "gate")
    return job(repo, "gate", jid, ok=False)


def test_queue_respects_the_parallel_limit(repo):
    q = json.loads(job(repo, "queue", "--json").stdout)
    assert [i["number"] for i in q["new_issues"]] == [1, 2, 3, 4]
    assert q["free_slots"] == 3 and q["start_now"] == [1, 2, 3]
    for n in q["start_now"]:
        job(repo, "create", str(n))
    q = json.loads(job(repo, "queue", "--json").stdout)
    assert q["free_slots"] == 0 and q["start_now"] == [] and [i["number"] for i in q["new_issues"]] == [4]


def test_create_names_the_job_and_refuses_duplicates(repo):
    assert job(repo, "create", "1").stdout.strip() == "001-dark-mode"
    assert state(repo, "001-dark-mode")["branch"] == "factory/001-dark-mode"
    assert job(repo, "create", "1", ok=False).returncode != 0


def test_happy_path_ends_in_a_merge(repo):
    jid = job(repo, "create", "1").stdout.strip()
    assert nxt(repo, jid)["agent"] == "factory-intake"
    assert to_review(repo, jid).returncode == 0
    step = nxt(repo, jid)
    assert step["action"] == "run-agents-in-parallel" and [a["reviewer"] for a in step["agents"]] == ["code"]
    job(repo, "review", jid, "code", "approve")
    assert nxt(repo, jid)["command"].endswith(f"settle {jid}")
    assert job(repo, "settle", jid).stdout.strip() == "approve"
    assert nxt(repo, jid)["agent"] == "factory-approver"
    job(repo, "set", jid, "pr=6")
    assert job(repo, "approve", jid, "approve").stdout.strip() == "merge"
    job(repo, "merge", jid)
    s = state(repo, jid)
    assert s["stage"] == "done" and not (repo / s["worktree"]).exists()
    reviewed = s["gates"]["1"]["head"]
    assert f"pr merge 6 --squash --delete-branch --match-head-commit {reviewed}" in Path(os.environ["FAKE_GH_LOG"]).read_text()
    assert nxt(repo, jid)["action"] == "none"


def test_intake_can_park_or_reject(repo):
    a, b = job(repo, "create", "1").stdout.strip(), job(repo, "create", "2").stdout.strip()
    job(repo, "intake", a, "needs-info", "--note", "which pages?")
    assert state(repo, a)["stage"] == "needs-info" and nxt(repo, a)["action"] == "wait-for-human"
    job(repo, "advance", a, "intake")
    assert state(repo, a)["stage"] == "intake"
    job(repo, "intake", b, "reject", "--note", "duplicate")
    assert state(repo, b)["stage"] == "rejected"
    assert job(repo, "intake", a, "accept", ok=False).returncode != 0          # risk is required
    assert job(repo, "intake", a, "accept", "--risk", "low", "--surfaces", "ui,nonsense", ok=False).returncode != 0


@pytest.mark.parametrize("risk,surfaces,expected", [
    ("low", "", ["code"]),
    ("medium", "ui", ["code", "ux", "ui"]),
    ("medium", "data", ["code", "security"]),
    ("high", "", ["code", "security"]),
    ("high", "ui,security", ["code", "ux", "ui", "security"]),
])
def test_reviews_are_routed_by_risk_and_surface(repo, risk, surfaces, expected):
    jid = job(repo, "create", "1").stdout.strip()
    to_review(repo, jid, risk, surfaces)
    agents = nxt(repo, jid)["agents"]
    assert [a["reviewer"] for a in agents] == expected
    assert agents[0]["model"] == ("sonnet" if risk == "high" else "haiku")


def test_a_review_that_is_not_required_is_refused(repo):
    jid = job(repo, "create", "1").stdout.strip()
    to_review(repo, jid, "low")
    assert job(repo, "review", jid, "security", "approve", ok=False).returncode != 0
    assert job(repo, "settle", jid, ok=False).returncode != 0                  # code review still missing


def test_a_failed_gate_sends_the_log_back_to_the_builder(repo):
    jid = job(repo, "create", "1").stdout.strip()
    p = to_review(repo, jid, checks="echo boom; exit 1")
    assert p.returncode == 1
    s = state(repo, jid)
    assert s["stage"] == "build" and s["round"] == 2 and s["gates"]["1"]["passed"] is False
    step = nxt(repo, jid)
    assert step["agent"] == "factory-builder" and step["round"] == 2
    assert step["feedback"] == [f"factory/jobs/{jid}/round-1-gate.log"]
    assert "boom" in (repo / step["feedback"][0]).read_text()


def test_requested_changes_go_back_and_then_escalate_after_three_rounds(repo):
    jid = job(repo, "create", "1").stdout.strip()
    to_review(repo, jid, "medium", "ui")
    for rnd in (1, 2, 3):
        for who, verdict in (("code", "approve"), ("ux", "changes"), ("ui", "approve")):
            job(repo, "review", jid, who, verdict)
        job(repo, "settle", jid)
        s = state(repo, jid)
        if rnd < 3:
            assert s["stage"] == "build" and s["round"] == rnd + 1
            job(repo, "advance", jid, "gate")
            job(repo, "gate", jid)
            assert [a["reviewer"] for a in nxt(repo, jid)["agents"]] == ["code", "ux", "ui"]   # every review is redone
    assert s["stage"] == "needs-human" and "ux" in s["blocked_on"]
    assert nxt(repo, jid)["action"] == "wait-for-human"


def test_high_risk_is_never_merged_without_a_person(repo):
    jid = job(repo, "create", "3").stdout.strip()
    to_review(repo, jid, "high", "security")
    job(repo, "review", jid, "code", "approve")
    job(repo, "review", jid, "security", "approve")
    job(repo, "settle", jid)
    assert job(repo, "approve", jid, "approve").stdout.strip() == "needs-human"
    assert "person to merge" in state(repo, jid)["blocked_on"]
    assert job(repo, "merge", jid, ok=False).returncode != 0
    job(repo, "set", jid, "pr=9")
    assert job(repo, "advance", jid, "merge", ok=False).returncode != 0        # an agent cannot release it
    assert job(repo, "release", jid, "merge", ok=False).returncode == 4        # nor can anything without a terminal
    assert state(repo, jid)["stage"] == "needs-human"
    assert release_as_person(repo, jid, "merge", "wrong").returncode == 4      # the person must type the issue number
    assert state(repo, jid)["stage"] == "needs-human"
    assert release_as_person(repo, jid, "merge", "3").returncode == 0
    job(repo, "merge", jid)
    s = state(repo, jid)
    assert s["stage"] == "done" and any(h["event"] == "released-by-person:merge" for h in s["history"])


def test_a_job_that_was_never_approved_cannot_be_released_to_merge(repo):
    jid = job(repo, "create", "1").stdout.strip()
    to_review(repo, jid, "medium", "ui")
    for _ in range(3):
        for who in ("code", "ux", "ui"):
            job(repo, "review", jid, who, "changes")
        job(repo, "settle", jid)
        if state(repo, jid)["stage"] == "build":
            job(repo, "advance", jid, "gate")
            job(repo, "gate", jid)
    assert state(repo, jid)["stage"] == "needs-human"
    assert release_as_person(repo, jid, "merge", "1").returncode != 0
    assert release_as_person(repo, jid, "build", "1").returncode == 0
    s = state(repo, jid)
    assert s["stage"] == "build" and s["round"] == 4 and s["budget_start"] == 4   # rounds never repeat
    job(repo, "advance", jid, "gate")
    job(repo, "gate", jid)
    step = nxt(repo, jid)                                                         # nothing recorded earlier counts now
    assert step["action"] == "run-agents-in-parallel" and [a["reviewer"] for a in step["agents"]] == ["code", "ux", "ui"]
    assert job(repo, "settle", jid, ok=False).returncode != 0
    for _ in range(2):                                                            # and the person gave it a fresh budget
        for who in ("code", "ux", "ui"):
            job(repo, "review", jid, who, "changes")
        job(repo, "settle", jid)
        assert state(repo, jid)["stage"] == "build"
        job(repo, "advance", jid, "gate")
        job(repo, "gate", jid)


def test_an_approval_covers_one_commit_only(repo):
    jid = job(repo, "create", "3").stdout.strip()
    to_review(repo, jid, "high", "security")
    for who in ("code", "security"):
        job(repo, "review", jid, who, "approve")
    job(repo, "settle", jid)
    job(repo, "set", jid, "pr=9")
    assert job(repo, "approve", jid, "approve").stdout.strip() == "needs-human"
    approved = state(repo, jid)["approved_head"]
    assert approved == state(repo, jid)["gates"]["1"]["head"]
    # the person sends it back for another build; the old approval must not survive
    assert release_as_person(repo, jid, "build", "3").returncode == 0
    assert "approved_head" not in state(repo, jid)
    wt = repo / state(repo, jid)["worktree"]
    (wt / "more.txt").write_text("new work\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "more"], cwd=wt, check=True)
    job(repo, "advance", jid, "gate")
    job(repo, "gate", jid)
    for who in ("code", "security"):
        job(repo, "review", jid, who, "changes")
    job(repo, "settle", jid)
    job(repo, "advance", jid, "gate"), job(repo, "gate", jid)
    for who in ("code", "security"):
        job(repo, "review", jid, who, "changes")
    job(repo, "settle", jid)
    job(repo, "advance", jid, "gate"), job(repo, "gate", jid)
    for who in ("code", "security"):
        job(repo, "review", jid, who, "changes")
    job(repo, "settle", jid)
    s = state(repo, jid)
    assert s["stage"] == "needs-human" and "approved_head" not in s
    # an approval was logged once, long ago, for a different commit: release to merge is refused
    assert any(h["event"] == "approver:approve" for h in s["history"])
    assert release_as_person(repo, jid, "merge", "3").returncode != 0
    assert state(repo, jid)["stage"] == "needs-human"


def test_only_the_jobs_own_pull_request_can_be_recorded_or_merged(repo):
    a, b = job(repo, "create", "1").stdout.strip(), job(repo, "create", "2").stdout.strip()
    for jid in (a, b):
        to_review(repo, jid, "low")
        job(repo, "review", jid, "code", "approve")
        job(repo, "settle", jid)
    gh_state = Path(os.environ["FAKE_GH_STATE"])
    st = json.loads(gh_state.read_text())
    st["pr_branch"] = {"6": f"factory/{a}", "7": f"factory/{b}"}
    gh_state.write_text(json.dumps(st))
    assert job(repo, "set", a, "pr=7", ok=False).returncode == 5               # another job's pull request
    assert job(repo, "set", a, "branch=main", ok=False).returncode != 0
    assert job(repo, "set", a, "worktree=..", ok=False).returncode != 0
    job(repo, "set", a, "pr=6")
    job(repo, "approve", a, "approve")
    # the branch moves after it was checked and reviewed
    wt = repo / state(repo, a)["worktree"]
    (wt / "late.txt").write_text("unreviewed\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "late"], cwd=wt, check=True)
    assert job(repo, "merge", a, ok=False).returncode == 5
    s = state(repo, a)
    assert s["stage"] == "needs-human" and "changed after" in s["blocked_on"]
    assert "pr merge" not in Path(os.environ["FAKE_GH_LOG"]).read_text()


def test_auto_merge_can_be_switched_off(repo):
    cfg = repo / "factory" / "config.json"
    c = json.loads(cfg.read_text())
    c["auto_merge"] = False
    cfg.write_text(json.dumps(c))
    jid = job(repo, "create", "1").stdout.strip()
    to_review(repo, jid, "low")
    job(repo, "review", jid, "code", "approve")
    job(repo, "settle", jid)
    assert job(repo, "approve", jid, "approve").stdout.strip() == "needs-human"


def test_the_approver_can_escalate_or_ask_for_changes(repo):
    a, b = job(repo, "create", "1").stdout.strip(), job(repo, "create", "2").stdout.strip()
    for jid in (a, b):
        to_review(repo, jid, "low")
        job(repo, "review", jid, "code", "approve")
        job(repo, "settle", jid)
    assert job(repo, "approve", a, "escalate", "--note", "product decision").stdout.strip() == "needs-human"
    assert job(repo, "approve", b, "changes").stdout.strip() == "build" and state(repo, b)["round"] == 2


def test_stages_cannot_be_skipped(repo):
    jid = job(repo, "create", "1").stdout.strip()
    for stage in ("build", "review", "merge", "done"):
        assert job(repo, "advance", jid, stage, ok=False).returncode != 0
    job(repo, "intake", jid, "accept", "--risk", "low")
    assert job(repo, "advance", jid, "build", ok=False).returncode != 0        # no spec.md yet
    assert job(repo, "gate", jid, ok=False).returncode != 0
    assert job(repo, "merge", jid, ok=False).returncode != 0
    assert job(repo, "set", jid, "stage=done", ok=False).returncode != 0


def test_a_failed_merge_leaves_the_job_at_merge(repo):
    jid = job(repo, "create", "1").stdout.strip()
    to_review(repo, jid, "low")
    job(repo, "review", jid, "code", "approve")
    job(repo, "settle", jid)
    job(repo, "set", jid, "pr=6")
    job(repo, "approve", jid, "approve")
    s = json.loads(Path(os.environ["FAKE_GH_STATE"]).read_text())
    s["merge_fails"] = True
    Path(os.environ["FAKE_GH_STATE"]).write_text(json.dumps(s))
    assert job(repo, "merge", jid, ok=False).returncode != 0
    assert state(repo, jid)["stage"] == "merge"


def test_the_gate_uses_the_main_checkout_script_not_the_builders_copy(repo):
    jid = job(repo, "create", "1").stdout.strip()
    job(repo, "intake", jid, "accept", "--risk", "low")
    (repo / "factory" / "jobs" / jid / "spec.md").write_text("s")
    job(repo, "advance", jid, "build")
    job(repo, "worktree", jid)
    (repo / "factory" / "checks.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
    (repo / state(repo, jid)["worktree"] / "factory" / "checks.sh").write_text("#!/usr/bin/env bash\nexit 0\n")  # a builder weakening the gate
    job(repo, "advance", jid, "gate")
    assert job(repo, "gate", jid, ok=False).returncode == 1


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "", "a b", "x;rm"])
def test_unsafe_job_ids_are_refused(repo, bad):
    assert job(repo, "show", bad, ok=False).returncode != 0


def test_jobs_get_separate_worktrees_and_branches(repo):
    a, b = job(repo, "create", "1").stdout.strip(), job(repo, "create", "2").stdout.strip()
    for jid in (a, b):
        job(repo, "intake", jid, "accept", "--risk", "low")
        (repo / "factory" / "jobs" / jid / "spec.md").write_text("s")
        job(repo, "advance", jid, "build")
        job(repo, "worktree", jid)
    wa, wb = repo / state(repo, a)["worktree"], repo / state(repo, b)["worktree"]
    assert wa != wb and wa.is_dir() and wb.is_dir()
    head = lambda w: subprocess.run(["git", "branch", "--show-current"], cwd=w, capture_output=True, text=True).stdout.strip()  # noqa: E731
    assert head(wa) == f"factory/{a}" and head(wb) == f"factory/{b}" and head(repo) == "main"


def test_push_sends_only_the_jobs_own_branch(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    git = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)  # noqa: E731
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    jid = job(repo, "create", "1").stdout.strip()
    assert job(repo, "push", jid, ok=False).returncode != 0                    # not at build yet
    job(repo, "intake", jid, "accept", "--risk", "low")
    (repo / "factory" / "jobs" / jid / "spec.md").write_text("s")
    job(repo, "advance", jid, "build")
    job(repo, "worktree", jid)
    wt = repo / state(repo, jid)["worktree"]
    (wt / "feature.txt").write_text("work\n")
    git(wt, "add", "-A"), git(wt, "commit", "-q", "-m", "work")
    main_before = git(remote, "rev-parse", "main").stdout
    assert job(repo, "push", jid).stdout.strip() == f"factory/{jid}"
    assert git(remote, "rev-parse", f"factory/{jid}").stdout == git(wt, "rev-parse", "HEAD").stdout
    assert git(remote, "rev-parse", "main").stdout == main_before
    git(wt, "checkout", "-q", "-B", "main")                                    # a builder that wandered onto main
    assert job(repo, "push", jid, ok=False).returncode == 5
    assert git(remote, "rev-parse", "main").stdout == main_before
    settings = json.loads((KIT / "factory" / "settings.example.json").read_text())["permissions"]
    assert "Bash(git push:*)" in settings["deny"] and not any(r.startswith("Bash(git push") for r in settings["allow"])


def test_installer_never_overwrites(repo):
    marker = repo / "factory" / "FACTORY.md"
    marker.write_text("mine\n")
    out = subprocess.run([str(INSTALL), str(repo)], capture_output=True, text=True, check=True).stdout
    assert marker.read_text() == "mine\n" and "0 file(s) added" in out
    assert ".factory-worktrees/" in (repo / ".gitignore").read_text().splitlines()
    assert (repo / ".gitignore").read_text().count(".factory-worktrees/") == 1


def test_default_checks_fail_an_empty_branch_and_pass_a_real_change(repo):
    checks = KIT / "factory" / "checks.sh"
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)  # noqa: E731
    run("git", "checkout", "-q", "-b", "factory/x")
    assert run("bash", str(checks)).returncode == 1
    (repo / "notes.txt").write_text("hello\n")
    run("git", "add", "-A"), run("git", "commit", "-q", "-m", "change")
    assert run("bash", str(checks)).returncode == 0


def test_default_checks_run_a_node_project_that_has_no_dependencies(repo):
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    checks = KIT / "factory" / "checks.sh"
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)  # noqa: E731
    run("git", "checkout", "-q", "-b", "factory/y")
    (repo / "package.json").write_text('{"name":"x","type":"module","scripts":{"test":"node --test t.test.js"}}')
    (repo / "t.test.js").write_text('import {test} from "node:test"; import assert from "node:assert"; test("ok", () => assert.equal(1, 1));')
    run("git", "add", "-A"), run("git", "commit", "-q", "-m", "node")
    p = run("bash", str(checks))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "npm test: ok" in p.stdout and "install" not in p.stdout
    (repo / "t.test.js").write_text('import {test} from "node:test"; import assert from "node:assert"; test("bad", () => assert.equal(1, 2));')
    assert run("bash", str(checks)).returncode == 1


def test_agent_and_skill_files_are_well_formed():
    agents = sorted((KIT / ".claude" / "agents").glob("*.md"))
    assert len(agents) == 8
    names = set()
    for path in agents:
        text = path.read_text()
        assert text.startswith("---\n")
        front = dict(line.split(": ", 1) for line in text.split("---\n")[1].strip().splitlines())
        assert front["name"] == path.stem and front["model"] in {"fable", "opus", "sonnet", "haiku"}
        assert ": " not in front["description"], f"{path.name}: a colon-space in the description breaks the YAML"
        assert "VERDICT:" in text
        if "reviewer" in path.stem:
            assert "Edit" not in front["tools"]
        names.add(front["name"])
    script = (KIT / "factory" / "bin" / "job").read_text()
    for name in names:
        assert f'"{name}"' in script, f"{name} is never started by the job script"
    skill = (KIT / ".claude" / "skills" / "factory" / "SKILL.md").read_text()
    assert "disable-model-invocation: true" in skill and "$ARGUMENTS" in skill


def test_dashboard_serves_jobs(repo):
    import socket
    import time
    import urllib.error
    import urllib.request
    job(repo, "create", "1")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([str(repo / "factory" / "bin" / "dashboard"), "--port", str(port)], cwd=repo, stdout=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                data = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs", timeout=1).read())
                break
            except OSError:
                time.sleep(0.1)
        assert [j["id"] for j in data["jobs"]] == ["001-dark-mode"]
        assert b"Software factory" in urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/jobs", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=1)
        assert err.value.code == 403
    finally:
        p.terminate()
