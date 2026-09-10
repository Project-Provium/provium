# Test-Driven Development and Code Review

Implement each task in small, manageable increments. For every increment:

1. Write tests that define the expected behavior.
2. Run the tests and confirm they fail for the expected reason.
3. Implement the minimum code required to make them pass.
4. Review the implementation for bugs, regressions, missed edge cases, and unnecessary complexity.
5. When issues are found, add tests that reproduce them, fix or refactor the code, and repeat the review.
6. If no issues are found, go ahead and run `ruff` and `pyright` checks for linting and formatting issues.
7. If formatting or linting issues were found and fixed, perform another code review round.
8. Repeat code review rounds until no remaining issues are found.

Consider an increment complete only when all relevant tests pass and the review identifies no remaining issues.

After completing the task, summarize:

* The original task.
* The changes made.
* The tests added or updated.
* The number of code-review rounds completed.
* Any known limitations or unresolved concerns.
