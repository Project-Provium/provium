# Provium Pipeline tutorial notebooks

This course assumes you already understand Provium artifacts, procedures, catalogs, and procedure execution. It focuses on the orchestration layer: why it exists, how its durable objects fit together, and how to operate it safely.

Run the notebooks in order. Each notebook is intentionally small and includes a **What to notice** section. Code cells are executable against the repository environment; CLI examples that would create durable local state are shown as shell blocks for you to run in a disposable working directory.

1. [Orientation and mental model](00-orientation.ipynb)
2. [Definitions and compilation](01-definitions-and-compilation.ipynb)
3. [Inputs and runs](02-inputs-and-runs.ipynb)
4. [Dispatch and execution](03-dispatch-and-execution.ipynb)
5. [Artifacts, cache, retention, and garbage collection](04-artifacts-cache-and-gc.ipynb)
6. [Storage, recovery, and concurrency](05-storage-recovery-and-concurrency.ipynb)
7. [CLI workflows](06-cli-workflows.ipynb)
8. [Extension points and testing](07-extension-points-and-testing.ipynb)

## Running the course

From `packages/provium-pipeline`, install the repository development environment and start Jupyter with the repository virtual environment. The notebooks themselves do not mutate `.provium`; operational commands are opt-in shell examples.

```bash
../../.venv/bin/python -m jupyter lab notebooks
```

If Jupyter is not installed in that environment, install it in a separate development environment while keeping `provium` and `provium-pipeline` editable. The automated package test executes every Python cell without requiring Jupyter.

## Suggested learning loop

For each notebook:

1. Read the mental model first.
2. Run the Python cells and inspect the real library objects.
3. Copy CLI blocks into a temporary directory when you want durable state.
4. Use `provium run status`, `provium run tasks`, and `provium run outputs` after each state-changing command.
5. Return to the linked reference guide when you want exact contracts or edge cases.
