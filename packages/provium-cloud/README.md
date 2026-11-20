# Provium Cloud

Cloud execution configuration for Provium procedures and pipelines, starting
with AWS Batch. Deployment validation and scheduling-rule resolution are
implemented, along with a deployment-validation CLI. Submission, execution,
and storage backends are not implemented yet.

Deployment configurations have no name or registration step and refer to
existing infrastructure. Provium Cloud is independent of `provium-dispatch`.
