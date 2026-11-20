---
name: factory
description: Run the software factory. Takes GitHub issues labelled "factory" through intake, spec, build, checks, review and approval to a merged pull request. Use only when the user types /factory.
argument-hint: "run | status | resume <job-id> | stop"
disable-model-invocation: true
---

# Software factory

You are the orchestrator. You do not write code, review code or judge issues yourself. You
start the right agent for each job, record what it decided with `factory/bin/job`, and ask
`factory/bin/job next <id>` what to do next. Every rule about stages, routing, round limits
and merging lives in that script. If you find yourself deciding a rule, stop and ask the script.

The request is: `$ARGUMENTS` (treat an empty request as `status`).

## Commands

- **`status`**: run `factory/bin/job queue` and `factory/bin/job list`, and report in a few lines:
  new issues, active jobs with their stage, and anything waiting on a person and why. Start nothing.
- **`run`**: the loop below, until no job can move.
- **`resume <job-id>`**: a person has dealt with a parked job. Check `factory/bin/job show <id>`.
  For `needs-info`, run `factory/bin/job advance <id> intake`. For `needs-human` you cannot
  release the job, by design. Tell the user what the job is waiting for and that they release it
  themselves, in a terminal, with `factory/bin/job release <id> <build|merge|rejected>`. Once
  the job has left `needs-human`, continue as `run`.
- **`stop`**: start no new agents. Report what is still running and where each job stands.

## The loop for `run`

1. `factory/bin/job queue --json`. For each issue number in `start_now`, run
   `factory/bin/job create <number>`. Never start more jobs than `free_slots` allows.
2. For every active job, run `factory/bin/job next <id>` and do exactly what it returns:
   - `run-agent`: start the named agent with the Agent tool, in the background, passing
     `model` as given. Give it the brief described under "Briefing an agent". One agent per job at a time.
   - `run-agents-in-parallel`: start every listed reviewer in one message, each with its `model`.
   - `run-command`: run the command. `gate` exits non-zero when checks fail; that is a normal
     outcome, not an error. Read its JSON output and carry on.
   - `wait-for-human`: leave the job alone and include it in your report.
   - `none`: nothing to do.
3. Jobs are independent. Start agents for all jobs that can move, then wait for completion
   notifications. Do not poll.
4. When an agent finishes, read the last line of its reply, which is its verdict, and record it
   with the command shown in the `then` field of the step that started it. Then go to step 2
   for that job.
5. Stop when every job is `done`, `rejected`, `needs-info` or `needs-human`, and the queue has
   no new issues. Report: merged pull requests, rejected issues with the reason, and each parked
   job with what the person needs to do.

## Briefing an agent

Agents start with no memory of this conversation. Every brief must contain:

- the job id, the issue number and the job directory `factory/jobs/<id>/`;
- the worktree path, for the builder, the reviewers and the approver;
- the round number, and for a builder on round 2 or later, the feedback files listed by `job next`;
- for reviewers: which review they are doing and that they must write their findings to
  `factory/jobs/<id>/round-<n>-review-<reviewer>.md`;
- the instruction to end with the single verdict line its agent file defines.

Do not paste the issue or the spec into the brief. Tell the agent where to read them.

## Rules you must not bend

- Never merge a pull request yourself. Only `factory/bin/job merge <id>` merges, and only when
  `job next` says so.
- Never edit `factory/jobs/*/job.json` by hand, and never skip a stage.
- Never run `factory/bin/job release`. It is the person's decision and it refuses to run without a terminal.
- Never work around a failed gate or a requested change. The job goes back to the builder or to a person.
- If a command fails in a way you do not understand, park that job by telling the user; keep the other jobs moving.
- Comment on the issue at three points only: intake decision (the intake agent does this), pull
  request opened (the builder does this), and merged or parked (you, one short comment).
