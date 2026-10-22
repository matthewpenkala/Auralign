<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Inspecting effective configuration

An inherited environment can change the device, backend, batching, model
lifecycle or cleanup endpoint even when YAML looks correct. Inspect the actual
transcribe invocation before committing compute resources:

```bash
ltm transcribe input.wav --config job.yaml --asr-device cpu \
  --model-lifecycle per-job --out fresh-job --print-effective-config
```

The precedence is preset, YAML, environment, then explicitly supplied CLI
options. Omitted CLI defaults do not replace an explicit YAML/environment
setting. The preview uses the same configuration resolution and validation as
transcription. It prints JSON and exits before pipeline execution, media
probing, model verification/loading, device startup or output-directory
creation. The input argument must still name an existing readable file.
Configuration validity is a separate gate from `ltm doctor --startup` and a
successful bounded inference run.

Rejected configuration reports known field names and error categories without
raw inputs or validator messages. YAML syntax errors report only the category
and available line/column coordinates; parser excerpts and private values are
omitted. A rejected preview does not create the output directory.

The `effective_configuration_preview.v1` payload contains:

- `source_configuration`: the existing privacy-safe configuration projection;
  prompts, local paths and cleanup endpoint contents are omitted or redacted;
- `source_config_sha256`: the same digest calculation used in run metadata,
  including the complete private configuration in memory;
- `present_environment_overrides`: recognized override names present in the
  environment, without their values; a name may be superseded by a CLI flag;
- explicit false values for `inference_performed`, `models_verified` and
  `media_probed`.

Credentials and arbitrary environment variables are never enumerated. A hash
binds configuration identity; it is not proof of authorship, correct source
selection, readiness or output accuracy. Keep previews local when configuration
identity itself is sensitive. For reproducible automation compare the complete
digest as well as the readable selected settings, retain the source revision
and lock identity, and scope environment changes to the child process rather
than changing a shared installation.
