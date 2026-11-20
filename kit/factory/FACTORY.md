# How this project's factory works

Every agent in the factory reads this file. Put here what a new engineer would need to be
told on their first day. Keep it short; agents read all of it, every time.

## The project

<!-- Two or three sentences: what this product is and who uses it. -->

## How to run it

<!-- The commands to install, start the product locally, and run the tests.
     The UX and UI reviewers use these to look at a change in the running product. -->

## Conventions

<!-- The handful of rules that matter here and that a reader would not guess from the code:
     where things live, naming, what must always have a test, what must never be touched. -->

## What the factory must not do here

<!-- Files, directories or kinds of change that always need a person.
     Intake rejects or marks as high risk anything that touches these. -->

- Never change `factory/`, `.claude/` or CI configuration as part of a job.
