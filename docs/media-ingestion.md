<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Audio stream selection

Auralign inspects and decodes the same audio stream. By default it selects the
first audio stream in FFprobe order, regardless of other streams' default
disposition or channel count. Previously inspection described the first audio
stream while FFmpeg could automatically decode another stream. For multi-stream
media this could silently transcribe a different microphone. Single-stream
inputs retain their existing behavior.

List available streams locally before selecting a microphone:

```bash
ffprobe -v error -show_streams -of json input.mkv
ltm transcribe input.mkv --audio-stream-index 2 --out fresh-job
```

`--audio-stream-index` is the **absolute zero-based FFprobe stream index**. It
includes video and subtitle stream positions: index `2` means `0:2`, not the
third audio stream. The index must identify an audio stream. Missing audio,
absent indexes, and indexes belonging to video or subtitles fail before
canonical decoding. An explicitly supplied CLI index overrides YAML:

```yaml
audio:
  stream_index: 2
```

Omit the field or use `null` to select the first audio stream. No inherited
environment variable selects a stream. The setting is catalogued but hidden
from the native product UI until that UI has a stream-selection workflow;
the CLI and YAML are the reviewed selection entry points.
`audio_metadata.json`, the preprocessing
decision and run metadata identify the selected absolute index and total audio
stream count. Probe metadata also preserves the selected stream's reported
`time_base` and `start_time_seconds` when available. These describe the source;
transcript timestamps remain seconds relative to the decoded canonical audio.
They are not automatic camera-timecode or nonzero-container-offset conversion.

Stream selection does not select or audition a channel inside a multichannel
stream. The existing mono configuration still downmixes that chosen stream;
extract an explicitly reviewed microphone channel first when needed. Selecting
the correct stream, successful decoding and complete forced timing do not prove
recognition accuracy or a perceptual listening review.

The regression suite muxes two short synthetic PCM tones, makes the second
stereo stream the container default, and proves sample-for-sample that default
and explicit choices decode the intended source. It uses no models or private
recordings.
