"""Local compute adapter exercising the actual cloud worker protocol."""

import pytest

from provium_cloud.execution.worker import execute_job


class LocalBatch:
    def __init__(self):
        self.jobs = {}
        self.submissions = 0

    def submit(self, key, generation, deployment):
        self.submissions += 1
        job = f"job-{self.submissions}"
        self.jobs[job] = [key, generation, deployment, "RUNNABLE"]
        return job

    def find(self, generation):
        return [
            job
            for job, (_, g, _, _) in self.jobs.items()
            if g.attempt == generation.attempt
        ]

    def describe(self, jobs):
        return {job: self.jobs[job][3] for job in jobs if job in self.jobs}

    def cancel(self, job, status, reason):
        self.jobs[job][3] = "FAILED"

    def drain(self):
        for job, (key, generation, deployment, status) in list(self.jobs.items()):
            if status == "RUNNABLE":
                self.jobs[job][3] = "RUNNING"
                execute_job(
                    deployment, key, generation.number, generation.attempt, job_id=job
                )
                self.jobs[job][3] = "SUCCEEDED"


def graph():
    return {
        "inputs": {"document": "provium_text_pipeline_example.DocumentV1"},
        "nodes": {
            "tokens": {
                "procedure": "provium_text_pipeline_example.TokenizeV1",
                "inputs": {"source": "$inputs.document"},
            },
            "count": {
                "procedure": "provium_text_pipeline_example.CountWordsV1",
                "inputs": {"tokens": "$nodes.tokens.destination"},
            },
        },
        "targets": {"count": "$nodes.count.word_count"},
    }


def finish(client, controller, executor, run):
    for _ in range(8):
        controller.tick()
        executor.drain()
        if client.get_run(run.id).status not in ("queued", "running"):
            return client.get_run(run.id)
    pytest.fail("run did not finish")
