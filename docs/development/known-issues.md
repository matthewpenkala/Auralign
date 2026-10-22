<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Known issues, unfinished capabilities and verification gaps

Recorded October 22, 2026 against source `93a20ee6f99115743367d945a84c566c0566fea7`.
This register captures all unresolved work known from the field investigation,
implementation and cleanup in that thread. It is not a new whole-repository
audit or a promise that every source TODO has been examined. The three field
repair commits are already on `main`; see [delivered repairs](2026-10-22-field-repairs.md).
An item here is not implemented or resolved unless its entry explicitly says so.

`KI-*` identifiers are stable. `AUR-*` references retain the investigation's
finding IDs without publishing private chats, recordings, transcript excerpts,
operator paths or raw logs. Public source/test links and bounded acceptance
criteria make the remaining work actionable. Historical observations, current
source limitations, test failures and new proposals are distinguished below.
Update an entry with the fixing commit and actual verification when closing it;
do not silently delete it or treat a proposed test as a passing test.

## Priorities and evidence

First restore trustworthy acceptance evidence: investigate the expired security
exception register, platform/security fixtures, bridge/API discrepancies and
missing native/real-model validation. These affect what can safely be claimed.
Recognition omissions and concealed takes have high user impact, but do not yet
have an evidence-backed automatic repair. Next consider source-selection and
review usability. Context packets, challenger integration and schema expansion
remain exploratory until their contracts and validation are established.

The baseline comparison covered all 1,831 original case identifiers and found
no passing baseline case becoming a failure/error after the field repairs.
That is regression evidence, not a green suite. Retained Windows results were:

| Check | Observed result | Meaning |
|---|---|---|
| Non-GUI Python tests | 1,658 passed, 105 failed, 9 skipped | Existing failures still need triage |
| GUI Python contracts | 89 passed, 8 failed, 1 skipped, 2 error events | Setup and teardown errors concern one oversized-parameter case |
| Full mypy | 22 errors in 6 files | Diagnostics byte-identical to untouched baseline |
| Final focused repairs | 76 passed | Changed paths, including synthetic decoding and receipt binding |
| Later cleanup focused checks | 51 passed | Fresh changed-path check, not a full-suite rerun |
| Ruff, compliance, REUSE and history/privacy checks | Passed | Narrow checks do not replace runtime/native acceptance |

The [sanitized validation inventory](2026-10-22-validation-inventory.json)
enumerates every retained failure/error event, skipped case and mypy diagnostic.
Its classifications are triage leads, not independently proven root causes.
It contains source identifiers and receipt digests, not private execution logs.

## Recognition and output interpretation

### KI-001 — Recognition can omit valid speech

**Open model limitation; historical outcome evidence.** Full-source and export
recognition sometimes omitted unique content. Forced alignment cannot restore
unrecognized words; complete timing, raw/final lexical conservation and
agreement between recognizers do not prove source completeness. No automatic
repair was delivered. A model upgrade is not assumed to solve this.
Next: evaluate permitted, acoustically adjudicated omission cases alongside
clean controls; record model/revision, selected audio and full context. A fix
must improve omission recall without inserting script text or hallucinations.
See [accuracy](../accuracy.md). Findings: AUR-017, 026, 027, 040, 050.

### KI-002 — Concealed restarts, intentional repetition and disputed speech

**Open mixed model/editorial limitation.** Fluent ASR can merge competing takes
or conceal a failed start. Long/displaced-word warnings mitigate navigation,
but omissions may produce no warning. Frequency counts can also flag legitimate
repetition; disabled diarization naturally produces unknown speakers. Some
historical disputed syllables were never conclusively adjudicated.
Next: retain complete alternative takes and review surrounding source audio;
test failed-start and intentional-repeat controls before proposing classifiers.
Acceptance requires demonstrated decisions against acoustic evidence, not word
frequency, high alignment scores or join-only review. Findings: AUR-018,
020–024, 047, 050; [QA](../../src/local_transcript_max/qa/heuristics.py).

### KI-003 — Confidence meanings remain format-dependent

**Partially mitigated; new schema proposal deferred.** Review spans now label
raw ASR probability and forced CTC alignment score separately. The existing
transcript `confidence` field retains its compatibility semantics; neither
measurement is a calibrated probability of correct wording. Extending explicit
score provenance to every output format remains undone. Next: inventory
consumers and propose additive fields/migration rules; require format roundtrip
and backwards-compatibility tests. Do not fabricate calibration.
Findings: AUR-019; [alignment](../alignment.md#report).

### KI-004 — Timing heuristics need contextual evaluation

**Implemented mitigation with unvalidated heuristic accuracy.** One-second
endpoint displacement and two-second word duration thresholds are transparent
review choices, not measured phonetic limits. Reports cap review spans at 100
and expose total/truncation counts. A legitimate sustained word may trigger;
nonfinite raw endpoint pairs are excluded from displacement spans and published
invalid intervals are handled separately by QA. Next: evaluate false positives
and missed anomalies on permitted labeled controls before changing thresholds
or adding automatic action. Test index/provenance conservation and bounded
reporting. Findings: AUR-018, 020, 052;
[diagnostics](../../src/local_transcript_max/diagnostics.py),
[timing tests](../../tests/test_timing_review.py).

## Ingestion, configuration and runtime capabilities

### KI-005 — Stream selection has no native audition or per-channel selector

**Known unimplemented product capability.** CLI/YAML absolute stream selection
is implemented, but the native field is hidden until a selection/audition flow
exists. Selecting a stream does not select a microphone channel inside it;
mono settings can downmix its channels. Next: design stream/channel identity,
preview and export provenance without silently changing defaults. Validate
synthetic channels with distinct samples and native UI persistence/accessibility.
Findings: AUR-012; [media ingestion](../media-ingestion.md),
[field catalog](../../macos/Auralign/Resources/FieldCatalog.json).

### KI-006 — Coordinate conversion and latency cause remain unresolved

**Known boundary plus uncertain historical observation.** Probe timebase/start
metadata is preserved, but transcript seconds are relative to decoded canonical
audio. Container duration may differ from decoded duration. Excerpt origin,
camera timecode and NLE presentation offsets require an external mapping;
there is no general converter. A reported sub-frame waveform offset had no
controlled causal diagnosis. A specific integration's presentation offset was
resolved there and must not become a universal audio shift.
Next: use synthetic impulses/uncompressed source/export calibration and an
explicit rational-time mapping contract, with nonzero starts and edit boundaries.
Reject unexplained global corrections. Findings: AUR-013–015, 030, 048, 049;
[probe](../../src/local_transcript_max/audio/inspect.py).

### KI-007 — Better Windows native-library diagnosis needs a real fixture

**Environment mitigation exists; diagnostic proposal deferred.** Shared-FFmpeg
compatibility and retained DLL-directory handles are documented. The original
failing loader stack was unavailable, and current synthetic decoding did not
exercise the deployed TorchCodec/shared-library inference closure. No arbitrary
DLL scanner/reloader was added. Next: capture an exact supported failing stack,
then test safe error classification with injected failures and a supported
native runtime. Preserve offline use, reviewed search paths, handle lifetimes
and untouched shared installations. Findings: AUR-003, 004;
[troubleshooting](../troubleshooting.md),
[doctor](../../src/local_transcript_max/doctor.py).

### KI-008 — Configuration preview does not establish readiness

**Deliberate interface boundary; integration verification outstanding.** Preview
resolves/validates settings without probing media, verifying models, loading
devices or creating a job. Doctor, exact model verification and bounded real
inference remain distinct checks. Child-process environment isolation belongs
to callers; YAML does not override inherited environment variables. Next:
validate the intended invocation through all those checks when adopting repairs.
Do not describe a safe preview as successful transcription or disable supported
environment overrides globally. Findings: AUR-005–008;
[configuration preview](../configuration-preview.md).

### KI-009 — Experimental vocal separation is explicitly unavailable

**Confirmed current unimplemented capability, not a newly observed regression.**
`audio.separate_vocals=experimental` fails closed rather than silently claiming
separated audio. No backend was added by this work. Implementation needs an
authorized/licensed local backend, exact provenance/runtime/resource contracts
and synthetic plus permitted real separation evaluation. Preserve explicit
refusal until those exist. Source:
[normalization](../../src/local_transcript_max/audio/normalize.py),
[audio tests](../../tests/test_audio.py).

## Deferred reusable enhancements

### KI-010 — Local contextual review packets

**New exploratory proposal; unimplemented.** Build optional packets from review
spans containing media identity, coordinate origin and raw/published evidence.
Benefit: less manual navigation. Dependencies: permitted local media, exact
sample mapping, bounded storage and preservation of whole competing takes.
Smallest experiment: synthetic span extraction plus one adjudicated case.
Reject a design that hides necessary context or implies acceptance from a cue.
Findings: AUR-020, 023, 030; existing review spans are the input contract.

### KI-011 — Independent-recognizer evidence contract

**New exploratory proposal; unimplemented.** Architectural diversity exposed
omissions but other recognizers also omitted speech, misrecognized names and
hallucinated. Adjacent audio-language models had uncertain stop/cap evidence;
normal EOS alone did not establish good judgments. A generic interface should
retain overlapping alternatives, source identity, model/runtime provenance and
termination evidence without automatically merging text or choosing takes.
Dependencies include rights, isolated runtime/storage and local-only processing.
Smallest experiment: adjudicated omission/retake, clean and intentional-repeat
controls. Reject mandatory consensus or unverified token-to-edit timing.
Findings: AUR-021–023, 027, 039–042, 048.

### KI-012 — General resumable inference and recovery contract

**Capability not established; proposal deferred.** Fresh-output guards, atomic
publication, job locking and metadata-bound completion already exist. Outer
orchestrator stage receipts do not establish resumability inside model inference.
This investigation did not demonstrate changed-code cancellation/restart
behavior. Next: specify resumable stages and exact input/config/model identities;
exercise cancellation, crash, incomplete output, retry and idempotency before
adding or advertising resume. Preserve failed evidence and refuse stale output.
Findings: AUR-032–034; [pipeline](../../src/local_transcript_max/pipeline.py),
[job store](../../src/local_transcript_max/server/jobs.py).

### KI-013 — Performance observations are not controlled benchmarks

**Measurement gap; no demonstrated optimization target.** Historical runtimes
used different excerpts, caches, devices and configurations. They cannot rank
models, prove a leak, or justify a rewrite. Next: a bounded permitted benchmark
with exact versions, warm/cold state, resource peaks, stage timings and quality
controls. Reject an optimization that changes recognition/timing invariants or
merely relocates repeated work. Findings: AUR-002, 035, 036;
[benchmarking](../benchmarking.md).

## Validation and portability work

Each item below records an observed failure or missing check, not proof of an
exploitable vulnerability or a deployed production failure. The sanitized
inventory assigns every failure/error to one of these entries. Preserve
fail-closed security semantics when repairing platform coverage.

### KI-014 — POSIX API and static-typing portability

**Confirmed Windows acceptance failures; root-cause repair outstanding.** Forty
non-GUI failures reference unavailable POSIX APIs. Full mypy reports 20 POSIX
attribute diagnostics plus two optional-import/ignore diagnostics (KI-020).
Affected source includes `platform_security.py`, `model_pack.py`, `models.py`,
`model_bundle.py` and `gui_bridge.py`. Next: distinguish intentionally Mac-only
paths from portable engine contracts, use supported platform abstractions and
faithful platform fixtures, then run positive and refusal behavior on Windows
and POSIX. No broad ignore/skip or removal of ownership/locking checks.
See [platform security](../../src/local_transcript_max/platform_security.py).

### KI-015 — Model-bundle fixtures stop at POSIX mode checks

**Confirmed validation obstruction; not evidence that tamper checks passed.**
Twenty-one model-bundle tests stop at unsafe source-root mode on Windows;
three surface as regex mismatches because the earlier check preempts their
intended negative case. A separate payload-mode test also fails. The same 29
NLTK-dependent cases were checked on the untouched base with matching small
locked dependencies: eight passed and 21 failed identically.
Next: use a supported POSIX host for native bundle ownership/modes and make
portable test preconditions explicit. Require each corruption case to reach
its intended guard; do not weaken installed-bundle permissions.
See [bundle tests](../../tests/test_model_bundle.py).

### KI-016 — Symlink privilege and Windows ACL fixtures

**Confirmed environment/fixture blockers with residual uncertainty.** Twenty-two
non-GUI tests fail because Windows symlink privilege is unavailable. Two
filesystem-permission tests and a separate QA swap-at-open assertion also fail.
The latter did not produce its expected parse classification; the observation
alone does not prove target content was exposed. Next: run guards on a supported
privileged fixture host and investigate ACL/swap failures independently. Verify
that outside targets stay untouched, linked data stays unread and errors remain
truthful. Do not alter user privileges or fake successful symlink coverage.
See [CLI tests](../../tests/test_cli_smoke.py).

### KI-017 — Python GUI bridge contracts are not green on Windows

**Confirmed eight failed assertions and two error events in one case.** An
absolute-path shared envelope fixture fails Windows validation; six expected
success exit codes fail; a visible-link case receives `InternalEngineError`
instead of `ResultContractError`. A very large parametrized QA value exceeds
Windows' 32,767-character environment-variable limit in setup and teardown.
These match the untouched baseline; the nullable stream manifest test passes.
Next: inspect individual traces, fix portable fixtures/error attribution and
avoid transporting oversized fixture payloads in environment values. Preserve
artifact bounds, regular-file checks and private previews. Re-run on Windows
and supported Mac, including invalid/oversized QA and cancellation.
See [bridge tests](../../tests/test_gui_bridge.py).

### KI-018 — API error-redaction test does not exercise its expected failure

**Confirmed test failure; product diagnosis uncertain.**
`test_server_error_redaction` expects an `HTTPException` from model acquisition
but none is raised in the isolated environment. That does not itself prove
redaction failure. Next: inject a deterministic permitted acquisition failure,
check actual API behavior and assert safe error details for the intended error
path. Do not require network access, credentials or model downloads in this
unit test. See [API test](../../tests/test_api.py).

### KI-019 — Shell/bootstrap and executable assumptions

**Confirmed Windows validation failures.** One POSIX-shell invocation, three
missing-option bootstrap exit assertions and one missing executable/file case
fail. Discovering `bash` on Windows does not establish a usable POSIX runtime.
Next: distinguish the supported shell/host contract from a launcher bug, run
option-rejection tests on a real supported host and make executable requirements
explicit. Preserve argument rejection; do not call installed Adobe apps or
install global tools for validation. See
[native validation tests](../../tests/test_native_release_validation.py).

### KI-020 — Optional dependency and mypy coverage

**Confirmed isolated-environment gap.** Two runtime tests lack optional
`transformers`; full mypy has a missing-import diagnostic and a dependent unused
ignore in forced alignment. NLTK availability was separately reconciled and
must not be blamed for the remaining bundle mode failures. Next: validate in
the supported exact locked extras lane, or explicitly separate optional-backend
test environments while retaining required coverage. Do not upgrade dependencies
or download models just to silence diagnostics.
See [alignment source](../../src/local_transcript_max/alignment/forced_alignment.py)
and [project extras](../../pyproject.toml).

### KI-021 — SaT fixture assumes POSIX path spelling

**Confirmed portability assertion failure.** The backend constructor receives
Windows path spelling from `Path('/model')`, while the test expects POSIX
strings. Next: compare against the platform's actual `Path` string contract and
retain exact local-files-only, CPU provider, tokenizer and version assertions.
Passing a fake constructor does not establish actual SaT inference.
See [presentation SaT test](../../tests/test_presentation_sat.py).

### KI-022 — Vulnerability exception approval has expired

**Confirmed date-dependent gate failure; dependency risk needs fresh assessment.**
The exception register and positive fixtures used an October 1, 2026 expiry;
that is past this report's October 22 cutoff. Six directly classified expiry
failures and three additional positive expectations remain non-green.
Next: review the current locked audit and exact upstream remediations; retire
resolved exceptions or obtain a new scoped owner risk decision when warranted.
Separate deterministic test-clock fixtures from real expiry enforcement.
Do not extend dates, manufacture approval or disable the real-time gate simply
to pass. Expiry alone does not prove a newly introduced vulnerability.
See [exception register](../../compliance/vulnerability_exceptions.toml),
[audit checker](../../scripts/check_vulnerability_audit.py) and
[audit tests](../../tests/test_vulnerability_audit.py).

### KI-023 — Changed-source real-model and native Mac acceptance unperformed

**Verification gap.** Synthetic PCM and stub-ASR pipeline tests prove selected
samples/config/provenance/completion binding, not recognition accuracy. Historical
real-model success does not validate the changed source. Native Swift UI,
analyze/build, signed lifecycle, package/install and clean-recipient checks were
not run on this Windows investigation host. Next: bounded permitted offline
model checks and the supported native acceptance matrix at an exact source SHA.
Perceptual listening remains separate. No release, native usability or model
accuracy acceptance can be claimed from the present source checks.
See [field repair validation](2026-10-22-field-repairs.md#validation).

## Repository, adoption and evidence boundaries

### KI-024 — Public repository and private-only publication workflow conflict

**Confirmed current governance/tooling mismatch.** GitHub reports this named
repository public, while current private-publication policy and
`check_private_push_authorization.py` require private visibility. That helper
correctly rejects the public repository. The owner explicitly authorized this
thread's reviewed source synchronization to the existing public repository;
matching URL/default branch, write access, clean tree, ancestry and privacy
checks were enforced independently. No visibility change or private artifact
publication was performed. Next: reconcile current source-publication policy
and tooling with the owner's intended public/private source scope, preserving
separate model/package/private-release gates and immutable historical facts.
Test both permitted and denied visibility/permission/URL/dirty/behind cases;
do not silently broaden the existing private helper.
See [working rules](../../AGENTS.md), [agent policy](../legal/AI_AGENT_POLICY.md)
and [push helper](../../scripts/check_private_push_authorization.py).

### KI-025 — Existing-checkout LF adoption changes byte identities

**Compatibility/adoption task; source fix already delivered.** `.gitattributes`
makes new text checkouts canonical LF while preserving binary bytes. An existing
CRLF checkout may need a reviewed refresh; a byte-pinned deployment's lock hash
can change without a Git content change. This thread preserved deployed pins.
Next: reconcile source revision, logical diff and raw lock/license identities
under the destination's adoption procedure, preserving dirty work and rollback.
Validate checkout-byte regressions and a bounded offline integration case.
Do not update a production pin merely because upstream source is synchronized.
Findings: AUR-011, 053; [checkout test](../../tests/test_checkout_bytes.py).

### KI-026 — Historical evidence remains partial

**Known investigation limitation, not a product defect.** All nine supplied
histories reached the reader's available endpoint, but capped outputs and
unavailable original traces prevent absolute completeness. Some causal and
acoustic conclusions remain uncertain. Private sources stay private.
Next: consult missing original traces or permitted adjudicated media if they
become available, then update the relevant issue's confidence and disposition.
Do not infer execution, listening, causality or a verified fix from summaries.
Findings: AUR-003, 015, 021–023, 050 and the source-coverage appendix.

### KI-027 — Editorial/integration contracts need to stay explicit

**Reusable operating lessons; product automation not implemented.** A canonical
edit plan, saved-project parity, ASR or decoding does not establish good take
selection or listening acceptance. Export/source comparison needs exact source
identity, coordinate mapping and quarantine of boundary-crossing words. Human
corrections apply only to their approved scope. Those lessons are documented
generally; episode approvals, a specific presentation offset and outer workflow
receipts remain with the integration. Next: any reusable handoff feature needs
an explicit consumer/coordinate/approval contract and complete-interior review
alongside joins. No automatic retake deletion or script-as-truth behavior.
Findings: AUR-024, 025, 028–030, 034, 037, 038, 043, 048–050;
[accuracy boundaries](../accuracy.md#recognition-timing-and-editorial-review).

## Reconciled exclusions and closure

Working source/model provenance, isolated environments, offline protection,
vocabulary hints, raw-output conservation, fresh outputs and completion hashes
remain supported approaches (AUR-001, 002, 007–011, 016, 032, 033, 036).
The upstream atomic-copy and strict-alignment repairs were already present
(AUR-054, 055). They are not unfinished implementation or new credit.
Generic ancestor-indirection protection already exists; a deployment's drive
letter policy is external. Already-repaired integration heading/wrapped-output
assertions and a one-off research script's implicit-encoding failure do not
establish corresponding open Auralign defects (AUR-044–046).

Implemented configuration, stream, QA, diagnostics, checkout and privacy fixes
are recorded separately (AUR-005, 006, 012, 013, 017–020, 031, 050–053, 056).
Their residual limitations appear above. No generalizable issue is excluded
merely because it was first observed in the owner's integration.

For closure, record the actual affected revision, bounded reproduction or
acceptance criterion, fixing commit, relevant passing checks and any remaining
platform/model/perceptual limitation. Keep this register synchronized with
source changes; a merged document or proposed experiment does not close an item.
