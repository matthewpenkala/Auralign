<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# October 22, 2026 field repairs

This source change generalizes verified media-ingestion and review lessons from
the owner's Remix Devotionals use. The complete retrospective and source
register remain private and outside Git. No private recordings, transcript
content, chat exports, production paths or model files accompany these changes.

## Changes

- **Deterministic stream selection.** Inspection formerly chose the first audio
  stream while FFmpeg's automatic selection could decode another microphone.
  Inspection, canonical preparation and decoding now share one absolute stream
  index. `audio.stream_index` / `--audio-stream-index` permits explicit selection;
  the default remains the first inspected audio stream. Probe metadata records
  stream identity, count, time base and start time. This deliberately changes
  multi-stream decoding that formerly depended on FFmpeg's default/channel
  heuristics. Single-stream behavior is preserved. The field is advanced and
  hidden in the native GUI until a microphone-selection flow exists.
- **Configuration preview.** `transcribe --print-effective-config` exposes the
  effective settings and exact complete-configuration digest without inference
  or output creation. This makes environment/YAML/CLI disagreements inspectable
  without changing precedence or sharing private override values.
- **Usable timing coverage.** QA no longer counts nonfinite or nonpositive word
  intervals as usable timestamps. Errors identify word indexes; missing timing
  remains separately counted. Segment validity also rejects nonfinite/negative
  bounds. A nonfinite word cannot poison the cursor used to inspect later words.
- **Contextual timing cues.** Long words are review warnings. Existing
  raw-to-published displacement statistics gain bounded indexed spans, signed
  shifts and separately attributed ASR/published scores. Raw wording, timing,
  confidence fields and automatic re-decode policy remain unchanged.
  Invalid finite raw ASR intervals remain review cues after positive forced
  timing, so alignment cannot silently erase that particular warning signal.
- **Operator guidance.** Media selection, configuration identity, Windows shared
  DLL setup and the limits of ASR/alignment evidence are documented explicitly.
  The CLI completion message counts **transcript turns**, including unknown or
  grouped labels, rather than implying that this count identifies speakers.
- **Exact checkout bytes.** Newly reproduced during this investigation: a
  Windows `core.autocrlf=true` checkout changed license/notice bytes and failed
  existing exact-hash contracts. Repository-local `.gitattributes` now keeps
  detected text as LF while preserving binary resources. A real Git fixture
  proves a clone with `core.autocrlf=true` retains both exact text and binary
  bytes. Existing checkouts may need a reviewed line-ending-only refresh;
  never discard dirty work or change global Git settings to obtain this result.
  The policy uses Git's documented
  [text detection and LF checkout attributes](https://git-scm.com/docs/gitattributes#_text).

## Evidence and compatibility boundary

Field results included forced words lasting several seconds and large endpoint
movement across concealed restarts despite complete, valid timing. They support
review diagnostics, not a new automatic retake classifier. One-second movement
and two-second word thresholds are transparent new conservative review choices,
not measured accuracy guarantees. No new model, dependency, acquisition,
cloud service, lexical cleanup or editorial policy is added.

Existing per-job model release, exact model/cache verification, character-level
alignment conservation, short-segment fallback labeling, completion metadata
binding, fresh-output guards and candidate-only re-decode are preserved. They
predate this patch and are not credited as new fixes.

## Validation

Tests use synthetic permitted inputs. Real FFmpeg/FFprobe multi-stream tests
make a second stereo tone stream the container default and compare decoded PCM
samples with the intended first or explicit stream. Unit and CLI tests exercise
effective precedence, private-value exclusion, no-inference/no-output behavior,
invalid timings, indexed review cues, finite statistics and bounded reporting.
Native GUI manifests/catalog coverage are synchronized and tested on Python;
Swift contract assertions are updated for the added nullable integer leaf.

These checks do not measure model recognition accuracy or certify native macOS
execution, private episode content, listening review or release packaging.
Deployment into the originating production integration is a separate operation;
its pinned checkout, environment, model cache and wrappers remain unchanged.
