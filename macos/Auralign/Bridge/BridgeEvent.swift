// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// One JSONL line from the engine bridge: `{"event": ..., "payload": {...}}`.
struct BridgeEvent: Equatable {
    var name: String
    var payload: JSONValue

    /// Successful events emitted by one-shot Python bridge commands.
    static let oneShotSuccessNames: Set<String> = [
        "doctor",
        "handshake",
        "models",
        "presets",
        "probe",
        "schema",
        "schema_written",
        "validate",
    ]

    /// Exact remote vocabulary accepted from one-shot bridge processes. A
    /// command may return its single success event or one structured failure.
    static let oneShotNames = oneShotSuccessNames.union(["bridge_error"])

    /// Exact remote vocabulary accepted from a streaming `transcribe`
    /// process. One-shot responses and app-owned lifecycle events are
    /// deliberately excluded.
    static let streamingNames: Set<String> = [
        "alignment_done", "alignment_start",
        "asr_done", "asr_start",
        "bridge_error",
        "canonical_audio_done", "canonical_audio_start",
        "cleanup_done", "cleanup_start",
        "complete", "config_invalid",
        "diarization_done", "diarization_start",
        "export_done", "export_start",
        "failed", "fusion_done",
        "inspect_done", "inspect_start",
        "job_accepted",
        "model_release_done", "model_release_start",
        "preflight_done", "preflight_start",
        "pyannote_progress",
        "redecode_done", "redecode_start",
        "result",
        "speaker_id_done", "speaker_id_start",
        "speaker_identity_done", "speaker_identity_start",
        "vad_done", "vad_start",
    ]

    /// All child-authored event names, retained for bounded diagnostic
    /// projections. `process_exited` is app-owned and never valid on the wire.
    static let allowedNames = oneShotNames.union(streamingNames)

    enum WireVocabulary {
        case allRemote
        case oneShot
        case streaming

        fileprivate var names: Set<String> {
            switch self {
            case .allRemote:
                return BridgeEvent.allowedNames
            case .oneShot:
                return BridgeEvent.oneShotNames
            case .streaming:
                return BridgeEvent.streamingNames
            }
        }
    }

    /// Decode one child-authored JSONL event.
    ///
    /// Python's emitter contract always supplies a dictionary payload, even
    /// when it is empty. Missing, null, scalar, and array payloads therefore
    /// fail closed instead of being silently converted into empty evidence.
    static func decode(
        line: String,
        vocabulary: WireVocabulary = .allRemote
    ) -> BridgeEvent? {
        guard let data = line.data(using: .utf8),
              let value = try? JSONValue.decode(from: data),
              let object = value.objectValue,
              let name = object["event"]?.stringValue,
              vocabulary.names.contains(name),
              let payload = object["payload"],
              payload.objectValue != nil
        else { return nil }
        return BridgeEvent(name: name, payload: payload)
    }
}

/// A structured validation error surfaced by the Pydantic authority.
struct ConfigFieldError: Equatable, Identifiable {
    var location: [String]
    var message: String
    var type: String = "value_error"

    var id: String { location.joined(separator: ".") + "|" + message }

    /// Dotted key path prefix used to attach the error to a rendered field.
    var dottedLocation: String { location.joined(separator: ".") }

    /// Strictly parse an authoritative validation-error array.
    ///
    /// `nil` means the array or any row was malformed. An empty array remains
    /// distinguishable and means the engine explicitly returned no rows.
    /// Never compact-map protocol evidence: silently dropping one malformed
    /// row could make an invalid hidden field disappear from recovery UI.
    static func parse(from payload: JSONValue) -> [ConfigFieldError]? {
        guard let errors = payload.value(at: "errors")?.arrayValue else { return nil }
        var parsed: [ConfigFieldError] = []
        parsed.reserveCapacity(errors.count)
        for entry in errors {
            guard let object = entry.objectValue,
                  let message = object["message"]?.stringValue,
                  !message.isEmpty,
                  let type = object["type"]?.stringValue,
                  !type.isEmpty,
                  let locationValues = object["loc"]?.arrayValue,
                  !locationValues.isEmpty
            else {
                return nil
            }
            let location = locationValues.compactMap(\.stringValue)
            guard location.count == locationValues.count,
                  location.allSatisfy({ !$0.isEmpty })
            else {
                return nil
            }
            parsed.append(
                ConfigFieldError(location: location, message: message, type: type)
            )
        }
        guard Set(parsed.map(\.id)).count == parsed.count else { return nil }
        return parsed
    }
}

/// A doctor check row from the engine's readiness report.
struct DoctorCheck: Equatable, Identifiable {
    var name: String
    var status: String
    var message: String

    var id: String { name + "|" + message }
}

struct DoctorSummary: Equatable {
    var ok: Bool
    var offline: Bool
    var checks: [DoctorCheck]
    var startupEvidence: DoctorStartupEvidence? = nil

    var errors: [DoctorCheck] { checks.filter { $0.status == "error" } }
    var warnings: [DoctorCheck] { checks.filter { $0.status == "warning" } }

    static func parse(from payload: JSONValue) -> DoctorSummary? {
        guard let object = payload.objectValue,
              let ok = object["ok"]?.boolValue,
              let offline = object["offline"]?.boolValue,
              let checkValues = object["checks"]?.arrayValue
        else { return nil }
        let allowedStatuses: Set<String> = ["ok", "info", "warning", "error"]
        let checks = checkValues.compactMap { value -> DoctorCheck? in
            guard let check = value.objectValue,
                  let name = check["name"]?.stringValue,
                  let status = check["status"]?.stringValue,
                  allowedStatuses.contains(status),
                  let message = check["message"]?.stringValue else { return nil }
            return DoctorCheck(name: name, status: status, message: message)
        }
        guard checks.count == checkValues.count else { return nil }
        guard let startupEvidence = DoctorStartupEvidence.parse(
            from: object["startup_evidence"]
        ) else {
            return nil
        }
        return DoctorSummary(
            ok: ok,
            offline: offline,
            checks: checks,
            startupEvidence: startupEvidence
        )
    }
}

/// Python-owned completeness declaration for heavyweight backend startup
/// probes. Ready state is impossible unless this exact versioned contract is
/// internally consistent and complete.
struct DoctorStartupEvidence: Equatable {
    var requested: Bool
    var complete: Bool
    var expectedIDs: [String]
    var observedIDs: [String]

    static func parse(from value: JSONValue?) -> DoctorStartupEvidence? {
        guard let object = value?.objectValue,
              object["schema_version"]?.stringValue
                == "doctor_startup_evidence.v1",
              let requested = object["requested"]?.boolValue,
              let complete = object["complete"]?.boolValue,
              let expectedCount = object["expected_count"]?.strictIntValue,
              let observedCount = object["observed_count"]?.strictIntValue,
              let expectedValues = object["expected_ids"]?.arrayValue,
              let observedValues = object["observed_ids"]?.arrayValue
        else {
            return nil
        }
        let expectedIDs = expectedValues.compactMap(\.stringValue)
        let observedIDs = observedValues.compactMap(\.stringValue)
        guard expectedIDs.count == expectedValues.count,
              observedIDs.count == observedValues.count,
              !expectedIDs.isEmpty,
              requested,
              expectedIDs.count == expectedCount,
              observedIDs.count == observedCount,
              Set(expectedIDs).count == expectedIDs.count,
              Set(observedIDs).count == observedIDs.count,
              expectedIDs.allSatisfy({ $0.hasPrefix("startup:") }),
              observedIDs.allSatisfy({ $0.hasPrefix("startup:") }),
              complete == (requested && observedIDs == expectedIDs)
        else {
            return nil
        }
        return DoctorStartupEvidence(
            requested: requested,
            complete: complete,
            expectedIDs: expectedIDs,
            observedIDs: observedIDs
        )
    }
}

/// Cached-model availability from the bridge `models` command.
struct ModelCacheStatus: Equatable, Identifiable {
    var role: String
    var key: String
    var modelId: String
    var gated: Bool
    var optional: Bool
    var present: Bool
    var partial: Bool
    var revisionVerified: Bool
    /// Python-authoritative selection for the current effective
    /// configuration. Informational Swift logic must not re-derive it.
    var selectedForConfig: Bool
    var problem: String?

    var id: String { role + "/" + key }

}

/// Media metadata from the bridge `probe` command (ffprobe-backed).
struct MediaProbe: Equatable {
    var durationSeconds: Double?
    var sampleRate: Int?
    var channels: Int?
    var codec: String?
    var formatName: String?

    static func parse(from payload: JSONValue) -> MediaProbe? {
        guard let duration = payload.value(at: "duration_seconds")?.doubleValue,
              duration.isFinite,
              duration >= 0,
              duration <= 315_576_000,
              let sampleRate = payload.value(at: "sample_rate")?.strictIntValue,
              sampleRate > 0,
              sampleRate <= 10_000_000,
              let channels = payload.value(at: "channels")?.strictIntValue,
              channels > 0,
              channels <= 1_024
        else { return nil }
        return MediaProbe(
            durationSeconds: duration,
            sampleRate: sampleRate,
            channels: channels,
            codec: payload.value(at: "codec")?.stringValue,
            formatName: payload.value(at: "format_name")?.stringValue
        )
    }

    var durationText: String {
        guard let durationSeconds,
              durationSeconds.isFinite,
              durationSeconds >= 0,
              durationSeconds <= 315_576_000
        else {
            return "Unknown duration"
        }
        let rounded = durationSeconds.rounded()
        let hours = floor(rounded / 3_600)
        let minutes = floor(rounded.truncatingRemainder(dividingBy: 3_600) / 60)
        let seconds = rounded.truncatingRemainder(dividingBy: 60)
        if hours >= 1 {
            return String(format: "%.0f:%02.0f:%02.0f", hours, minutes, seconds)
        }
        return String(format: "%.0f:%02.0f", minutes, seconds)
    }
}
