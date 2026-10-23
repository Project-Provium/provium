# Changelog

## [2.2.0](https://github.com/Project-Provium/provium/compare/provium-pipeline-v2.1.0...provium-pipeline-v2.2.0) (2026-10-23)


### Features

* Input sets ([e680d14](https://github.com/Project-Provium/provium/commit/e680d14874193a759d88e10be0604f7a740201cd))
* Input sets ([aebcdf7](https://github.com/Project-Provium/provium/commit/aebcdf74a6bcaa7dbc87abf7de73d21a42814e75))

## [2.1.0](https://github.com/Project-Provium/provium/compare/provium-pipeline-v2.0.0...provium-pipeline-v2.1.0) (2026-10-21)


### Features

* add layered application configuration and CLI overrides ([7cce376](https://github.com/Project-Provium/provium/commit/7cce37689c529e31349651d5756489f60a3cc810))
* add named pipeline queues and configurable defaults ([f39cd22](https://github.com/Project-Provium/provium/commit/f39cd2266172f63d05200ef4ada3183a8386b63a))

## [2.0.0](https://github.com/Project-Provium/provium/compare/provium-pipeline-v1.0.0...provium-pipeline-v2.0.0) (2026-10-16)


### ⚠ BREAKING CHANGES

* **provium-pipeline:** PipelineConfiguration requires an inputs mapping from names to artifact identifiers. Existing YAML and persisted configurations must be updated explicitly.

### Features

* **provium-pipeline:** add unregister, registry listing, and queue summaries ([ad714c9](https://github.com/Project-Provium/provium/commit/ad714c9f4b721a031c4f9624beffba7617e27fa4))
* **provium-pipeline:** add unregister, registry listing, and queue summaries ([69e2753](https://github.com/Project-Provium/provium/commit/69e27535a3c603b4bc9acaffb1baa0b96f7769fd))
* **provium-pipeline:** load YAML documents from S3 ([4787230](https://github.com/Project-Provium/provium/commit/478723034de41150d18f2299c748abe2ca1a8f85))
* **provium-pipeline:** require explicit typed pipeline inputs ([23bc5cf](https://github.com/Project-Provium/provium/commit/23bc5cfe3e4dc460771ab9e53e1bafb702b2a93f))
* support S3 paths and explicit pipeline input declarations ([43bf0f6](https://github.com/Project-Provium/provium/commit/43bf0f677b852adfd0524ccd134bea2f721c8517))

## [1.0.0](https://github.com/Project-Provium/provium/compare/provium-pipeline-v0.1.0...provium-pipeline-v1.0.0) (2026-10-16)


### ⚠ BREAKING CHANGES

* **provium-pipeline:** Replace the previous pipeline executor API and configuration with pipeline definitions, YAML procedure graphs, compute profiles, and ExecutionContext. Existing pipeline integrations must migrate to the new API.

### Features

* **provium-pipeline:** implement idempotent execution and PostgreSQL queues ([1126724](https://github.com/Project-Provium/provium/commit/11267240f4d25c534c2885346a2ef17b4d3992b8))


### Bug Fixes

* **provium-pipeline:** keep core dependency compatible with automated releases ([b774114](https://github.com/Project-Provium/provium/commit/b7741143383ffda1e3b23e943154e3e3b36b5258))
* **provium-pipeline:** keep core dependency compatible with automated releases ([098dc85](https://github.com/Project-Provium/provium/commit/098dc85c4877d930faa77640c662e53f149b2ce5))


### Documentation

* exclude internal pipeline planning from published documentation ([5135075](https://github.com/Project-Provium/provium/commit/5135075e5c7ccf39e068f3b1488de769fd2b85fc))

## 0.1.0 (2026-09-10)


### Features

* Create provium-pipeline ([6e6257c](https://github.com/Project-Provium/provium/commit/6e6257cc318eb5fb36146fecd63cb23c42b126b1))
* **pipeline:** add artifact index reference ([c025fda](https://github.com/Project-Provium/provium/commit/c025fdabaa31facd38f647b364e261fbb45c849a))
* **pipeline:** add artifact store conformance suite ([26d21e6](https://github.com/Project-Provium/provium/commit/26d21e6fdad5b3b85389bf17b5c3f6d197cb6ca2))
* **pipeline:** add compiler catalog collections ([8a57d48](https://github.com/Project-Provium/provium/commit/8a57d48a311ad0627acef26432e7b83c794c5b97))
* **pipeline:** add configuration document codecs ([430b2cf](https://github.com/Project-Provium/provium/commit/430b2cf93b53ce08d9af26be95f45a73e39b3731))
* **pipeline:** add definition document codecs ([dedfe1f](https://github.com/Project-Provium/provium/commit/dedfe1fadfdd7c0986206c35e717161e121f5b55))
* **pipeline:** add definition primitives ([84c6d59](https://github.com/Project-Provium/provium/commit/84c6d5944db86c4cb32f7a28aeac030d82f8792c))
* **pipeline:** add identifiers and canonical values ([93731c4](https://github.com/Project-Provium/provium/commit/93731c4d14de104c311a25572539b612542fccf5))
* **pipeline:** add immutable input records ([aa63e7c](https://github.com/Project-Provium/provium/commit/aa63e7cec0fb498bc9035aa83819133a6b794b8e))
* **pipeline:** add lazy pipeline catalog ([539d439](https://github.com/Project-Provium/provium/commit/539d439d5f7594e4943119f5dfa59d5669ee29f5))
* **pipeline:** add typed definition builder ([5bbc364](https://github.com/Project-Provium/provium/commit/5bbc36492626960048b74dd415714a5b17940b5f))
* **pipeline:** clean filesystem artifacts ([6d79050](https://github.com/Project-Provium/provium/commit/6d79050cc295d664a1e6a5cfad9970601df97371))
* **pipeline:** compile deterministic pipeline plans ([ebd76fc](https://github.com/Project-Provium/provium/commit/ebd76fc9dbf7377fe2b01fb4b8dc618e58dccba6))
* **pipeline:** define artifact store contracts ([c02cd11](https://github.com/Project-Provium/provium/commit/c02cd11039941bba41ab91272bbd797459389476))
* **pipeline:** describe finalized artifacts ([c118255](https://github.com/Project-Provium/provium/commit/c118255440e98d93f80353d0e950f15c37f88221))
* **pipeline:** discover artifact adapter plugins ([2d500cf](https://github.com/Project-Provium/provium/commit/2d500cf7cf75c1d6603dea2cff2d8b9323f0ff10))
* **pipeline:** discover installed pipeline catalogs ([766bdfa](https://github.com/Project-Provium/provium/commit/766bdfaca9c400cd2b63d277bd1dc98bdeeb9adc))
* **pipeline:** import managed artifacts ([c6fa3bd](https://github.com/Project-Provium/provium/commit/c6fa3bdf747fef3aa994ac4dc9b40d301b7d555c))
* **pipeline:** materialize filesystem artifacts ([da2c7ec](https://github.com/Project-Provium/provium/commit/da2c7ecb1c60c756b8e74f00351e125f3edc81c7))
* **pipeline:** persist artifact index in sqlite ([caccae1](https://github.com/Project-Provium/provium/commit/caccae1549a913e4310ccfb4c133d8c1fc76f98c))
* **pipeline:** publish filesystem artifacts ([c687972](https://github.com/Project-Provium/provium/commit/c68797216d859a54c64d86d3f5498448a9cab141))
* **pipeline:** report compiler diagnostics ([cee57c2](https://github.com/Project-Provium/provium/commit/cee57c25740509aa3b39d62f187e0c78e14ce3dd))
* **pipeline:** resolve layered configuration ([b93f0ab](https://github.com/Project-Provium/provium/commit/b93f0abb6c7a14b50e0d065655f1ba7653191370))
* **pipeline:** scaffold distribution ([261079a](https://github.com/Project-Provium/provium/commit/261079a1f8897b5ab5b8fe7b26f8d612011d0827))
* **pipeline:** validate compiled pipeline graphs ([832a1e4](https://github.com/Project-Provium/provium/commit/832a1e41b2882ab7000b2342276921b030e41359))


### Bug Fixes

* **pipeline:** report concrete cycle paths ([abb6b3d](https://github.com/Project-Provium/provium/commit/abb6b3dc50d8a5e6ee0c0cbf8a31e9c94b8300fd))
