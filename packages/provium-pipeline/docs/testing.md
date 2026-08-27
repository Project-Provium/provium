# Testing architecture

The package is developed in small test-driven increments and enforces complete statement and branch coverage. Tests are organized by the contract they prove, not merely by file correspondence.

## Running the suite

From `packages/provium-pipeline`:

```bash
PYTHONPATH=. ../../.venv/bin/pytest -q
../../.venv/bin/ruff check .
../../.venv/bin/pyright
```

`PYTHONPATH=.` avoids a collision with Python’s standard-library `test` namespace in this repository layout.

The full suite includes the mandatory Testcontainers scenario when Docker is available. To mirror CI’s split:

```bash
PYTHONPATH=. ../../.venv/bin/pytest -m "not testcontainers" -q
PYTHONPATH=. ../../.venv/bin/pytest -m testcontainers -q --no-cov
```

The second command must not silently skip in the CI integration job.

## TDD increment

For each behavior:

1. add the narrowest test that expresses the expected contract;
2. run it and confirm failure for the intended missing behavior;
3. implement the smallest complete behavior;
4. run focused tests;
5. review for races, edge cases, wrong boundaries, and unnecessary complexity;
6. add regression tests for review findings;
7. run Ruff and Pyright;
8. repeat review until clean;
9. run the full suite and coverage gate.

A green test that does not exercise the real boundary is not sufficient evidence. For example, an empty pipeline would not prove task invocation or output publication.

## Unit tests

Unit tests cover deterministic domain rules:

- validated identifiers and canonical JSON;
- definition/reference models and builder ownership;
- compiler diagnostics, topology, cardinality, and configuration;
- run/task/dispatch planning and state transitions;
- retry/backoff and lease tokens;
- computation identity/cache single-flight behavior;
- query ordering and export redaction;
- retention and GC races;
- codec round trips and unknown-schema rejection.

Small in-memory stores/callables isolate one service, but tests assert complete semantic values rather than internal call counts alone.

## Real local adapter tests

SQLite and filesystem adapters use real temporary databases/directories. They prove:

- reopen/persistence behavior;
- schema coexistence and idempotent initialization;
- transactions and compare-and-set conflicts;
- real file publication/materialization/deletion;
- corruption and locator errors;
- multiprocess claims and transitions;
- atomic output-set behavior.

Mocks do not replace these tests.

## Conformance suites

Artifact store/index contracts are reusable so a new provider adapter can run the same behavioral expectations. A conformance suite should be parameterized by a real adapter factory and cleanup fixture.

Conformance focuses on observable guarantees: idempotency, conflicts, complete reads, verification, stable identity, active locations, and error categories.

## Execution integration tests

Integration tests compose:

- compiled task/run snapshots;
- SQLite stores;
- filesystem artifact store;
- core artifact/procedure definitions and concrete implementations;
- actual materialization and `ProcedureExecutor` calls;
- output import and downstream resolution;
- serial/multiprocess control where relevant.

Important failure cases include interruption, worker replacement, retry, cancellation, output publication conflicts, optional/repeated fields, and setup reuse/isolation.

## Installed-wheel Testcontainers acceptance

`test/integration/test_installed_wheel_cli.py` is the clean-environment acceptance test. It:

1. builds wheels for Provium core, `provium-pipeline`, and the example definition/procedure package;
2. starts a clean `python:3.12-slim` container with Testcontainers;
3. installs only those wheels;
4. verifies external CLI entry-point discovery;
5. creates a real finalized artifact;
6. prepares a one-node pipeline and managed index;
7. creates an immutable input set through subprocess CLI;
8. creates and executes a durable run;
9. verifies task count, terminal state, produced outputs, artifact binding, and active location.

This scenario catches source-tree import leakage, missing package data, entry-point mistakes, filesystem permissions, subprocess state loss, and optional dependency assumptions.

## CLI tests

The command matrix test parses every declared action. Separate tests cover:

- help descriptions;
- versioned JSON success and error envelopes;
- human output;
- exit codes and diagnostics;
- invalid identifiers and missing durable state;
- resolver convenience commands;
- selection/dependency flags;
- dispatch wait/cancel/retry;
- run artifact/location output;
- environment backend wiring.

Parser coverage alone does not prove backend support, so each behavior also has backend or subprocess coverage.

## Codec tests

Durable/cross-process documents are versioned and canonical. Tests should prove:

- complete round trip;
- deterministic bytes/order;
- timezone normalization;
- optional/absent values;
- enum/identifier reconstruction;
- rejection of malformed, missing, extra, or unknown-schema values.

A codec test is part of the storage contract because a reopen path depends on it.

## Concurrency and race tests

Use real threads/processes where ownership matters:

- only one cache reservation owner;
- only one SQLite lease/transition claimant;
- expired lease recovery;
- worker crash/replacement;
- cancellation token fencing;
- GC retention race;
- publication race/conflict;
- concurrent state compare-and-set.

Deterministic clocks, token factories, and barriers are preferable to long sleeps.

## Static verification

Pyright runs in strict mode across `src` and `test`. The package includes `py.typed`, so public typing is a distribution contract.

Ruff checks `E`, `F`, `I`, `TRY`, and `UP` rules. Changed files should also be Ruff-formatted. Phase 14 will reconcile the remaining historical package-wide formatter baseline before enabling a global formatting gate.

## Coverage

Pytest configuration requires 100% statement and branch coverage for `provium_pipeline`. Coverage is a floor, not a substitute for semantic assertions. Every branch must be intentional, but tests must still prove the required real integration boundaries.

Documentation has its own coverage test: required guides must exist and be linked from the README/index, and every runtime Python module must appear in the module reference.

## Review checklist

For each increment ask:

- Does the model distinguish semantic from operational state?
- Is ordering canonical where identity or output depends on it?
- Is the operation idempotent, and are conflicting repeats rejected?
- What happens if the process dies before/after each external effect?
- Are state transitions fenced by expected state or token?
- Can mutable source/config/provider state affect an existing run?
- Are produced and absent optional outputs distinct?
- Are temporary files/materializations always owned and cleaned?
- Does the test use the real boundary it claims to verify?
- Does the public error explain which layer failed?

Record review rounds and the regressions they found in the task summary.

## CI layout

The repository test workflow runs:

- lint/type checks for core and pipeline packages;
- package tests on Python 3.12 and 3.13, excluding the container marker;
- a separate mandatory Python 3.12 Testcontainers clean-wheel job;
- example package tests;
- coverage upload for core.

A container runtime failure fails the mandatory integration job rather than converting acceptance into a skip.
