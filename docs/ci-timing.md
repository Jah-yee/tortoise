---
title: "CI Timing Measurement Artifact"
type: engineering
domain: capability
doc_status: live
created: 2026-08-18
subjects.team: epistemic-team
---

# CI Timing Measurement Artifact

> Measurement-only artifact (#1477). Refreshed weekly by the `ci-timing.yml`
> workflow, sampling the latest completed push-to-main Python CI run. Never a gate.

## Sampled run

- run_id: `36607451420`
- head_sha: `9c432c6a45794c4a030b1ae17f951bceb862aeac`
- created_at: `2026-09-29T17:47:38Z`
- conclusion: `success`
- sample_time: `2026-09-30T04:00:32Z`
- selection: latest completed `event=push&branch=main` run, `exclude_pull_requests=true`, cancelled skipped
- schema_version: `1`

## Step timings (Jobs API — real run)

Second-granularity timestamps: sub-10s steps read 0s — do not alarm on those.

| Job | Step | Duration (s) |
|---|---|---|
| manifest-integrity | Set up job | 1.0 |
| manifest-integrity | Run actions/checkout@v4 | 5.0 |
| manifest-integrity | Run actions/setup-python@v5 | 0.0 |
| manifest-integrity | Install pyyaml | 3.0 |
| manifest-integrity | Manifest integrity (drift gate, #1262) | 1.0 |
| manifest-integrity | Mergify protection invariants (config guard, #5215) | 2.0 |
| manifest-integrity | Post Run actions/setup-python@v5 | 0.0 |
| manifest-integrity | Post Run actions/checkout@v4 | 0.0 |
| manifest-integrity | Complete job | 0.0 |
| surface-guard | Set up job | 1.0 |
| surface-guard | Run actions/checkout@v4 | 5.0 |
| surface-guard | Run actions/setup-python@v5 | 0.0 |
| surface-guard | Install | 24.0 |
| surface-guard | Pin the gate's tooling to the base ref | 1.0 |
| surface-guard | The surface cannot expand without an explicit human decision | 2.0 |
| surface-guard | The ordering lint (AC13) | 11.0 |
| surface-guard | The list is generated from the manifest and cannot drift from it | 1.0 |
| surface-guard | Post Run actions/setup-python@v5 | 0.0 |
| surface-guard | Post Run actions/checkout@v4 | 0.0 |
| surface-guard | Complete job | 0.0 |
| changes | Set up job | 1.0 |
| changes | Run actions/checkout@v4 | 9.0 |
| changes | Tiered selection | 1.0 |
| changes | Emit push matrix (#1472) | 0.0 |
| changes | Tier-2 duration split (#1473) | 0.0 |
| changes | Secrets scan (evidence artifacts, | 0.0 |
| changes | Post Run actions/checkout@v4 | 0.0 |
| changes | Complete job | 0.0 |
| test-track-b | Set up job | 1.0 |
| test-track-b | Initialize containers | 12.0 |
| test-track-b | Run actions/checkout@v4 | 3.0 |
| test-track-b | Run actions/setup-python@v5 | 4.0 |
| test-track-b | Install | 25.0 |
| test-track-b | Generate coverage manifest (track-b, epic | 2.0 |
| test-track-b | Run Track B tests (explicit marker) | 5.0 |
| test-track-b | Skip-fail guard (track-b, epic | 0.0 |
| test-track-b | Upload track-b log (measurement artifact, | 1.0 |
| test-track-b | Post Run actions/setup-python@v5 | 0.0 |
| test-track-b | Post Run actions/checkout@v4 | 0.0 |
| test-track-b | Stop containers | 1.0 |
| test-track-b | Complete job | 0.0 |
| test-carve-out | Set up job | 1.0 |
| test-carve-out | Run actions/checkout@v4 | 4.0 |
| test-carve-out | Run actions/setup-python@v5 | 1.0 |
| test-carve-out | Install package + test extras | 99.0 |
| test-carve-out | Clean stale redislite orphans (issue | 0.0 |
| test-carve-out | Generate coverage manifest (carve-out — expected nodeids, epic | 14.0 |
| test-carve-out | Run carve-out suite (embedded, URI unset — E2E-4) | 892.0 |
| test-carve-out | Skip-fail guard (carve-out, epic | 0.0 |
| test-carve-out | Assert no redislite orphans (issue | 0.0 |
| test-carve-out | Upload carve-out log (measurement artifact, | 1.0 |
| test-carve-out | Post Run actions/setup-python@v5 | 0.0 |
| test-carve-out | Post Run actions/checkout@v4 | 0.0 |
| test-carve-out | Complete job | 0.0 |
| test (b) | Set up job | 1.0 |
| test (b) | Initialize containers | 14.0 |
| test (b) | Run actions/checkout@v4 | 2.0 |
| test (b) | Run actions/setup-python@v5 | 0.0 |
| test (b) | Run actions/setup-node@v4 | 1.0 |
| test (b) | Install package + test extras | 104.0 |
| test (b) | Clean stale redislite orphans (issue | 0.0 |
| test (b) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 6.0 |
| test (b) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 7.0 |
| test (b) | Compute docker URI (epic | 0.0 |
| test (b) | Generate coverage manifest (expected nodeids, epic | 95.0 |
| test (b) | Canary producer (epic | 0.0 |
| test (b) | Run fast test suite (slow files run in the test-slow job) | 2004.0 |
| test (b) | Upload pytest log (measurement artifact, | 2.0 |
| test (b) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (b) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (b) | Assert no redislite orphans (issue | 0.0 |
| test (b) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (b) | Post Run actions/setup-node@v4 | 0.0 |
| test (b) | Post Run actions/setup-python@v5 | 0.0 |
| test (b) | Post Run actions/checkout@v4 | 0.0 |
| test (b) | Stop containers | 1.0 |
| test (b) | Complete job | 0.0 |
| test-slow (a) | Set up job | 1.0 |
| test-slow (a) | Initialize containers | 21.0 |
| test-slow (a) | Run actions/checkout@v4 | 3.0 |
| test-slow (a) | Run actions/setup-python@v5 | 0.0 |
| test-slow (a) | Install package + test extras | 114.0 |
| test-slow (a) | Clean stale redislite orphans (issue | 0.0 |
| test-slow (a) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 3.0 |
| test-slow (a) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 6.0 |
| test-slow (a) | Drift guard — legs cover slow_files minus carve-out (#1471) | 1.0 |
| test-slow (a) | Compute docker URI (epic | 0.0 |
| test-slow (a) | Generate coverage manifest (slow legs — expected nodeids, epic | 7.0 |
| test-slow (a) | Run slow test suite — leg a (known-slow files, | 156.0 |
| test-slow (a) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (a) | Assert no redislite orphans (issue | 0.0 |
| test-slow (a) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (a) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (a) | Post Run actions/setup-python@v5 | 0.0 |
| test-slow (a) | Post Run actions/checkout@v4 | 1.0 |
| test-slow (a) | Stop containers | 0.0 |
| test-slow (a) | Complete job | 0.0 |
| packs-compile | Set up job | 1.0 |
| packs-compile | Run actions/checkout@v4 | 5.0 |
| packs-compile | Run actions/setup-python@v5 | 0.0 |
| packs-compile | Install package | 26.0 |
| packs-compile | Whole-registry compile (all packs, zero validation errors) | 0.0 |
| packs-compile | Post Run actions/setup-python@v5 | 1.0 |
| packs-compile | Post Run actions/checkout@v4 | 0.0 |
| packs-compile | Complete job | 0.0 |
| test (a) | Set up job | 1.0 |
| test (a) | Initialize containers | 13.0 |
| test (a) | Run actions/checkout@v4 | 3.0 |
| test (a) | Run actions/setup-python@v5 | 0.0 |
| test (a) | Run actions/setup-node@v4 | 0.0 |
| test (a) | Install package + test extras | 103.0 |
| test (a) | Clean stale redislite orphans (issue | 0.0 |
| test (a) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test (a) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 7.0 |
| test (a) | Compute docker URI (epic | 0.0 |
| test (a) | Generate coverage manifest (expected nodeids, epic | 83.0 |
| test (a) | Canary producer (epic | 0.0 |
| test (a) | Run fast test suite (slow files run in the test-slow job) | 1989.0 |
| test (a) | Upload pytest log (measurement artifact, | 1.0 |
| test (a) | Skip-fail guard: live-FalkorDB tests must run, never skip (#1436) | 0.0 |
| test (a) | Dump redislite-hygiene end-sweep decision (issue | 0.0 |
| test (a) | Assert no redislite orphans (issue | 0.0 |
| test (a) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test (a) | Post Run actions/setup-node@v4 | 1.0 |
| test (a) | Post Run actions/setup-python@v5 | 0.0 |
| test (a) | Post Run actions/checkout@v4 | 0.0 |
| test (a) | Stop containers | 1.0 |
| test (a) | Complete job | 0.0 |
| test-concurrency-falkor | Set up job | 1.0 |
| test-concurrency-falkor | Initialize containers | 10.0 |
| test-concurrency-falkor | Run actions/checkout@v4 | 2.0 |
| test-concurrency-falkor | Run actions/setup-python@v5 | 0.0 |
| test-concurrency-falkor | Install package + test extras | 99.0 |
| test-concurrency-falkor | Clean stale redislite orphans (issue | 0.0 |
| test-concurrency-falkor | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-concurrency-falkor | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 7.0 |
| test-concurrency-falkor | Run live concurrency tests (real FalkorDB sidecar) | 27.0 |
| test-concurrency-falkor | Upload pytest log (measurement artifact, | 1.0 |
| test-concurrency-falkor | Validate docker-compose.yml (ports + header edits) | 0.0 |
| test-concurrency-falkor | Docs-consistency guard (embedded must never be "default" again) | 0.0 |
| test-concurrency-falkor | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-concurrency-falkor | Post Run actions/setup-python@v5 | 0.0 |
| test-concurrency-falkor | Post Run actions/checkout@v4 | 0.0 |
| test-concurrency-falkor | Stop containers | 1.0 |
| test-concurrency-falkor | Complete job | 0.0 |
| test-d14-hosted-api | Set up job | 1.0 |
| test-d14-hosted-api | Run actions/checkout@v4 | 5.0 |
| test-d14-hosted-api | Run actions/setup-python@v5 | 0.0 |
| test-d14-hosted-api | Install package + test extras | 98.0 |
| test-d14-hosted-api | Run the embedded_only marker selection (URI unset — | 113.0 |
| test-d14-hosted-api | Upload D14 log (measurement artifact, | 1.0 |
| test-d14-hosted-api | Post Run actions/setup-python@v5 | 0.0 |
| test-d14-hosted-api | Post Run actions/checkout@v4 | 0.0 |
| test-d14-hosted-api | Complete job | 0.0 |
| test-slow (b) | Set up job | 1.0 |
| test-slow (b) | Initialize containers | 19.0 |
| test-slow (b) | Run actions/checkout@v4 | 5.0 |
| test-slow (b) | Run actions/setup-python@v5 | 0.0 |
| test-slow (b) | Install package + test extras | 128.0 |
| test-slow (b) | Clean stale redislite orphans (issue | 0.0 |
| test-slow (b) | Cache HF embedding model (BAAI/bge-small-en-v1.5) | 1.0 |
| test-slow (b) | Embedding model REQUIRED (BAAI/bge-small-en-v1.5) — dense retrieval leg | 8.0 |
| test-slow (b) | Drift guard — legs cover slow_files minus carve-out (#1471) | 0.0 |
| test-slow (b) | Compute docker URI (epic | 0.0 |
| test-slow (b) | Generate coverage manifest (slow legs — expected nodeids, epic | 7.0 |
| test-slow (b) | Run slow test suite — leg b (known-slow files, | 175.0 |
| test-slow (b) | Skip-fail guard (slow legs, epic | 0.0 |
| test-slow (b) | Assert no redislite orphans (issue | 0.0 |
| test-slow (b) | Upload pytest log (measurement artifact, | 1.0 |
| test-slow (b) | Post Cache HF embedding model (BAAI/bge-small-en-v1.5) | 0.0 |
| test-slow (b) | Post Run actions/setup-python@v5 | 1.0 |
| test-slow (b) | Post Run actions/checkout@v4 | 0.0 |
| test-slow (b) | Stop containers | 6.0 |
| test-slow (b) | Complete job | 0.0 |
| python-ci-gate | Set up job | 0.0 |
| python-ci-gate | Aggregate matrix result | 0.0 |
| python-ci-gate | Complete job | 0.0 |
| canary-streak | Set up job | 2.0 |
| canary-streak | Run actions/checkout@v4 | 5.0 |
| canary-streak | Run actions/setup-python@v5 | 0.0 |
| canary-streak | Download half-b artifacts (the P1-7 canary contract) | 1.0 |
| canary-streak | Download previous streak artifact (chain continuity) | 5.0 |
| canary-streak | Classify run + update the canary streak (epic | 1.0 |
| canary-streak | Upload canary streak artifact | 1.0 |
| canary-streak | Post canary-streak summary | 0.0 |
| canary-streak | Post Run actions/setup-python@v5 | 0.0 |
| canary-streak | Post Run actions/checkout@v4 | 0.0 |
| canary-streak | Complete job | 0.0 |

## Per-file durations (aggregated from --durations=15, slowest tests only)

> Derived, not measured: top-15 slowest tests per job grouped by file. Files whose
> tests are all below the top-15 cutoff are invisible; red runs are truncated by
> `--maxfail=20`. Use for relative regression detection, not absolute budgets.

| File | Tests measured | Total (s) | Max (s) |
|---|---|---|---|
| test_write_path_benchmark.py | 3 | 223.0 | 148.8 |
| test_write_path_runner.py | 3 | 110.6 | 37.0 |
| test_why_suite_benchmark.py | 1 | 26.7 | 26.7 |
| test_session_verify.py | 4 | 69.5 | 20.4 |
| test_4314_inert_hooks.py | 1 | 20.1 | 20.1 |
| test_ci_selection.py | 1 | 17.8 | 17.8 |
| test_markers.py | 1 | 16.8 | 16.8 |
| test_onboarding_state_split.py | 1 | 16.1 | 16.1 |
| test_run.py | 3 | 208.4 | 159.6 |
| test_selfhost_rest.py | 1 | 60.4 | 60.4 |
| test_battery_setup.py | 3 | 63.6 | 29.1 |
| test_github_indexer.py | 1 | 27.8 | 27.8 |
| test_graph_diagnostics.py | 1 | 18.3 | 18.3 |
| test_ep_selector.py | 1 | 16.9 | 16.9 |
| test_client_surface.py | 1 | 16.6 | 16.6 |
| test_ingest_v2_consolidation.py | 1 | 16.2 | 16.2 |
| test_surface_manifest.py | 3 | 44.0 | 15.8 |
| test_embedded_concurrency.py | 7 | 128.6 | 46.6 |
| test_smoke_embedded.py | 1 | 39.2 | 39.2 |
| test_longmem_runner.py | 1 | 16.7 | 16.7 |
| test_reaper.py | 3 | 31.7 | 11.8 |
| test_embedded_lifecycle.py | 6 | 64.5 | 11.6 |
| test_event_store.py | 2 | 8.5 | 6.4 |
| test_hosted_api.py | 9 | 11.8 | 2.3 |
| test_index_directory.py | 3 | 4.2 | 1.8 |
| test_sdk_group3.py | 1 | 1.1 | 1.1 |
| test_billing.py | 1 | 1.0 | 1.0 |
| test_control_plane.py | 1 | 1.0 | 1.0 |

## Outcome (suite totals across jobs)

- passed: **20525** · failed: **0** · error: **0** · skipped: **119** · xfailed: 4 · xpassed: 0
- watchdog-killed mid-suite: no

## Flake signal

> Proxy: failed-test lists from consecutive weekly samples. A test that failed in one
> sample and is absent from the next sample's failed list is a *candidate flake*.
> Per-test rerun-based flake rate becomes exact once the retry protocol lands (#1477).

_No candidate flakes in the sampled window yet (needs ≥ 2 consecutive samples)._

## History (bounded to last 52 samples)

| Sample | Run | Conclusion | Passed | Failed | Error | Skipped | Steps max job (s) |
|---|---|---|---|---|---|---|---|
| 2026-09-30T04:00:32Z | 36607451420 | success | 20525 | 0 | 0 | 119 | 2237 |
