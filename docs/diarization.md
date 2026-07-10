<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Diarization

The primary backend is pyannote community diarization. Speaker labels are
probabilistic labels such as `SPEAKER_00`; they are not real names. Current
speaker matching can derive display names from local enrolled profiles.
`ltm speakers rename` records legacy metadata but does not currently rewrite
jobs or exports.

Agentic diarization is implemented as deterministic orchestration: generate
candidates, score them, tie-break deterministically, preserve raw candidates,
and write a decision report. It does not invent real names or rewrite transcript
words.

Core controls:

```bash
uv run ltm transcribe input.wav --diarization-backend pyannote --num-speakers <N>
uv run ltm transcribe input.wav --diarization-backend off
uv run ltm transcribe input.wav \
  --agentic-diarization \
  --diarization-candidates auto \
  --diarization-candidate-range 2:8 \
  --known-speakers <N> \
  --write-diarization-decision-report
```

On Apple Silicon, `diarization.device: auto` uses CUDA if available and
otherwise resolves to CPU. It does not silently choose Torch MPS. Explicit
`--diarization-device mps`, `LTM_DIARIZATION_DEVICE=mps`, or
`diarization.device: mps` remains available for same-media benchmark
experiments; those runs emit an experimental pyannote MPS warning in runtime
metadata.

CPU is the acceptance baseline for Apple Silicon production validation. Do not
switch an in-progress or failed acceptance run to MPS to obtain a passing or
faster result. An MPS run is a separately labeled, non-acceptance optimization
candidate and should begin only after the same media has completed successfully
on CPU. MPS may become a supported acceleration path only after repeated
same-media runs demonstrate runtime stability and artifact equivalence against
the CPU baseline; changing the default or support status requires a separate
evidence-backed decision.

Normal diarization turns are preserved in `diarization.json` and
`diarization.rttm`. When pyannote exposes exclusive diarization, it is also
written to `exclusive_diarization.json` and used for single-label word-speaker
fusion by default. The normal overlap-aware turn list remains preserved for QA.

Agentic outputs:

```text
diarization_decision_report.json
diarization_candidates/candidate_*.json
diarization_candidates/candidate_*.rttm
```

Candidate score components include speaker-count fit, VAD/word coverage, median
turn duration, short-turn penalty, flapping penalty, overlap ratio, long
unlabeled regions, timestamp contradictions, and deterministic tie-break notes.

The previous NeMo Sortformer placeholder always failed and has been removed
from the public CLI/config/dependency surface. It is outside the supported
runtime contract.
