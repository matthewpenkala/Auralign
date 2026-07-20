// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

struct ArtifactFileIdentity: Equatable {
    var device: UInt64
    var inode: UInt64
}

struct PendingArtifactDescriptor: Equatable {
    var name: String
    var sizeBytes: Int64
    var identity: ArtifactFileIdentity
}

/// Exact identity evidence emitted immediately after the Python authority
/// creates and retains the fresh job directory.
struct JobAcceptanceEnvelope: Equatable {
    static let schemaVersion = "gui_job_acceptance.v1"

    var outputDirectory: URL
    var outputIdentity: ArtifactFileIdentity

    static func parse(
        _ payload: JSONValue,
        expectedOutputDirectory: URL
    ) throws -> JobAcceptanceEnvelope {
        let object = try PendingJobResultEnvelope.exactObject(
            payload,
            keys: [
                "schema_version",
                "output_directory",
                "output_identity",
            ],
            label: "job acceptance"
        )
        guard object["schema_version"]?.stringValue == schemaVersion,
              let outputPath = object["output_directory"]?.stringValue,
              !outputPath.isEmpty,
              (outputPath as NSString).isAbsolutePath
        else {
            throw ResultContractError(
                "The engine returned malformed job-acceptance evidence."
            )
        }
        let outputDirectory = URL(fileURLWithPath: outputPath, isDirectory: true)
            .standardizedFileURL
        guard outputDirectory.path == outputPath,
              outputDirectory.path != "/",
              outputDirectory == expectedOutputDirectory.standardizedFileURL
        else {
            throw ResultContractError(
                "The accepted job directory did not match the planned job."
            )
        }
        return JobAcceptanceEnvelope(
            outputDirectory: outputDirectory,
            outputIdentity: try PendingJobResultEnvelope.parseIdentity(
                object["output_identity"],
                label: "accepted job directory"
            )
        )
    }
}

enum ResultQAStatus: String, Equatable {
    case available
    case disabled
    case missing
    case invalid
    case oversized
}

struct ResultQAFlag: Equatable, Identifiable {
    var ordinal: Int = 0
    var code: String
    var severity: String
    var message: String

    var id: Int { ordinal }
}

struct ResultQAProjection: Equatable {
    var status: ResultQAStatus
    var overallQualitySummary: String?
    var flagCount: Int?
    var errorFlagCount: Int?
    var warningFlagCount: Int?
    var infoFlagCount: Int?
    var changedSpanCount: Int?
    var flags: [ResultQAFlag]
    var omittedFlagCount: Int
}

enum TranscriptPreviewStatus: String, Equatable {
    case available
    case missing
    case empty
}

struct ResultTranscriptPreview: Equatable {
    var status: TranscriptPreviewStatus
    var text: String?
    var displayedBytes: Int
    var sourceBytes: Int64?
    var truncated: Bool
}

/// Immutable protocol evidence retained until the child exits successfully and
/// is reaped. No filesystem content is read while this envelope is pending.
struct PendingJobResultEnvelope: Equatable {
    static let schemaVersion = "gui_job_result.v1"
    static let artifactScope = "immediate_regular_files"
    static let maximumArtifacts = 512
    static let maximumCount = 1_000_000_000
    static let maximumArtifactNameBytes = 255

    var outputDirectory: URL
    var outputIdentity: ArtifactFileIdentity
    var language: String?
    var speakerTurns: Int
    var words: Int
    var segments: Int
    var artifacts: [PendingArtifactDescriptor]
    var qa: ResultQAProjection
    var transcriptPreview: ResultTranscriptPreview
    var configFingerprint: String

    static func parse(
        _ payload: JSONValue,
        configFingerprint: String,
        expectedOutputDirectory: URL
    ) throws -> PendingJobResultEnvelope {
        let object = try exactObject(
            payload,
            keys: [
                "schema_version",
                "output_directory",
                "output_identity",
                "artifact_scope",
                "artifact_count",
                "language",
                "speaker_turns",
                "words",
                "segments",
                "artifacts",
                "qa",
                "transcript_preview",
            ],
            label: "result"
        )
        guard object["schema_version"]?.stringValue == schemaVersion else {
            throw ResultContractError("The engine returned an unsupported result schema.")
        }
        guard object["artifact_scope"]?.stringValue == artifactScope else {
            throw ResultContractError("The engine returned an unsupported artifact scope.")
        }
        guard let outputPath = object["output_directory"]?.stringValue,
              !outputPath.isEmpty,
              (outputPath as NSString).isAbsolutePath
        else {
            throw ResultContractError("The engine returned an invalid result directory.")
        }
        let outputDirectory = URL(fileURLWithPath: outputPath, isDirectory: true)
            .standardizedFileURL
        guard outputDirectory.path == outputPath,
              outputDirectory.path != "/",
              outputDirectory == expectedOutputDirectory.standardizedFileURL
        else {
            throw ResultContractError(
                "The engine result directory did not match the planned job."
            )
        }
        let outputIdentity = try parseIdentity(
            object["output_identity"],
            label: "result directory"
        )
        let language = try parseLanguage(object["language"])
        let speakerTurns = try parseCount(object["speaker_turns"], label: "speaker turns")
        let words = try parseCount(object["words"], label: "words")
        let segments = try parseCount(object["segments"], label: "segments")
        guard let artifactValues = object["artifacts"]?.arrayValue,
              artifactValues.count <= maximumArtifacts
        else {
            throw ResultContractError("The engine returned an invalid artifact list.")
        }
        let artifacts = try artifactValues.map(parseArtifact)
        let names = artifacts.map(\.name)
        guard names == names.sorted(),
              Set(names).count == names.count
        else {
            throw ResultContractError("The engine result artifacts were not sorted and unique.")
        }
        guard let declaredCount = object["artifact_count"]?.strictIntValue,
              declaredCount == artifacts.count
        else {
            throw ResultContractError("The engine result artifact count did not match its list.")
        }
        let qa = try parseQA(object["qa"])
        let transcriptPreview = try parseTranscriptPreview(
            object["transcript_preview"]
        )
        try validateProjectionArtifacts(
            artifacts,
            qa: qa,
            transcriptPreview: transcriptPreview
        )
        return PendingJobResultEnvelope(
            outputDirectory: outputDirectory,
            outputIdentity: outputIdentity,
            language: language,
            speakerTurns: speakerTurns,
            words: words,
            segments: segments,
            artifacts: artifacts,
            qa: qa,
            transcriptPreview: transcriptPreview,
            configFingerprint: configFingerprint
        )
    }

    private static func validateProjectionArtifacts(
        _ artifacts: [PendingArtifactDescriptor],
        qa: ResultQAProjection,
        transcriptPreview: ResultTranscriptPreview
    ) throws {
        let byName = Dictionary(uniqueKeysWithValues: artifacts.map { ($0.name, $0) })
        let qaArtifact = byName["qa_report.json"]
        switch qa.status {
        case .available:
            guard let qaArtifact,
                  qaArtifact.sizeBytes > 0,
                  qaArtifact.sizeBytes <= 1_048_576
            else {
                throw ResultContractError(
                    "The QA projection did not match the declared QA artifact."
                )
            }
        case .invalid:
            guard let qaArtifact, qaArtifact.sizeBytes <= 1_048_576 else {
                throw ResultContractError(
                    "The QA projection did not match the declared QA artifact."
                )
            }
        case .oversized:
            guard let qaArtifact, qaArtifact.sizeBytes > 1_048_576 else {
                throw ResultContractError(
                    "The QA projection did not match the declared QA artifact."
                )
            }
        case .missing:
            guard qaArtifact == nil else {
                throw ResultContractError(
                    "The QA projection contradicted the declared artifacts."
                )
            }
        case .disabled:
            guard let qaArtifact,
                  qaArtifact.sizeBytes > 0,
                  qaArtifact.sizeBytes <= 1_048_576
            else {
                throw ResultContractError(
                    "Disabled QA did not declare its expected empty report."
                )
            }
        }

        let transcriptArtifact = byName["transcript.txt"]
        switch transcriptPreview.status {
        case .available, .empty:
            guard let transcriptArtifact,
                  transcriptPreview.sourceBytes == transcriptArtifact.sizeBytes
            else {
                throw ResultContractError(
                    "The transcript preview did not match the declared transcript artifact."
                )
            }
        case .missing:
            guard transcriptArtifact == nil else {
                throw ResultContractError(
                    "The transcript preview contradicted the declared artifacts."
                )
            }
        }
    }

    private static func parseQA(_ value: JSONValue?) throws -> ResultQAProjection {
        guard let value else {
            throw ResultContractError("The engine omitted QA result evidence.")
        }
        let object = try exactObject(
            value,
            keys: [
                "status",
                "source_artifact",
                "byte_limit",
                "overall_quality_summary",
                "flag_count",
                "error_flag_count",
                "warning_flag_count",
                "info_flag_count",
                "changed_span_count",
                "flags",
                "omitted_flag_count",
            ],
            label: "result QA"
        )
        guard let statusText = object["status"]?.stringValue,
              let status = ResultQAStatus(rawValue: statusText),
              object["source_artifact"]?.stringValue == "qa_report.json",
              object["byte_limit"]?.strictIntValue == 1_048_576,
              let flagValues = object["flags"]?.arrayValue,
              flagValues.count <= 128,
              let omitted = object["omitted_flag_count"]?.strictIntValue,
              omitted >= 0,
              omitted <= maximumCount
        else {
            throw ResultContractError("The engine returned malformed QA evidence.")
        }
        let flags = try flagValues.enumerated().map {
            try parseQAFlag($0.element, ordinal: $0.offset)
        }
        let overall = try nullableString(object["overall_quality_summary"])
        let flagCount = try nullableCount(object["flag_count"], label: "QA flags")
        let errorCount = try nullableCount(
            object["error_flag_count"],
            label: "QA error flags"
        )
        let warningCount = try nullableCount(
            object["warning_flag_count"],
            label: "QA warning flags"
        )
        let infoCount = try nullableCount(
            object["info_flag_count"],
            label: "QA information flags"
        )
        let changedCount = try nullableCount(
            object["changed_span_count"],
            label: "QA changed spans"
        )
        let counts = [flagCount, errorCount, warningCount, infoCount, changedCount]
        switch status {
        case .available:
            guard let overall,
                  ["no_deterministic_warnings", "review_warnings"].contains(overall),
                  counts.allSatisfy({ $0 != nil }),
                  let flagCount,
                  let errorCount,
                  let warningCount,
                  let infoCount,
                  flagCount == flags.count + omitted,
                  errorCount + warningCount + infoCount == flagCount,
                  flags.filter({ $0.severity == "error" }).count <= errorCount,
                  flags.filter({ $0.severity == "warning" }).count <= warningCount,
                  flags.filter({ $0.severity == "info" }).count <= infoCount,
                  overall
                    == (flagCount == 0
                        ? "no_deterministic_warnings"
                        : "review_warnings")
            else {
                throw ResultContractError(
                    "The engine returned incomplete available QA evidence."
                )
            }
        case .disabled:
            guard overall == nil,
                  counts.allSatisfy({ $0 == 0 }),
                  flags.isEmpty,
                  omitted == 0
            else {
                throw ResultContractError(
                    "The engine attributed facts to disabled QA."
                )
            }
        case .missing, .invalid, .oversized:
            guard overall == nil,
                  counts.allSatisfy({ $0 == nil }),
                  flags.isEmpty,
                  omitted == 0
            else {
                throw ResultContractError(
                    "The engine attributed facts to unavailable QA."
                )
            }
        }
        return ResultQAProjection(
            status: status,
            overallQualitySummary: overall,
            flagCount: flagCount,
            errorFlagCount: errorCount,
            warningFlagCount: warningCount,
            infoFlagCount: infoCount,
            changedSpanCount: changedCount,
            flags: flags,
            omittedFlagCount: omitted
        )
    }

    private static func parseQAFlag(
        _ value: JSONValue,
        ordinal: Int
    ) throws -> ResultQAFlag {
        let object = try exactObject(
            value,
            keys: ["code", "severity", "message"],
            label: "result QA flag"
        )
        guard let code = object["code"]?.stringValue,
              !code.isEmpty,
              code.utf8.count <= 128,
              let severity = object["severity"]?.stringValue,
              ["info", "warning", "error"].contains(severity),
              let message = object["message"]?.stringValue,
              message.utf8.count <= 1_024
        else {
            throw ResultContractError("The engine returned a malformed QA flag.")
        }
        return ResultQAFlag(
            ordinal: ordinal,
            code: code,
            severity: severity,
            message: message
        )
    }

    private static func parseTranscriptPreview(
        _ value: JSONValue?
    ) throws -> ResultTranscriptPreview {
        guard let value else {
            throw ResultContractError("The engine omitted transcript preview evidence.")
        }
        let object = try exactObject(
            value,
            keys: [
                "status",
                "source_artifact",
                "byte_limit",
                "text",
                "displayed_bytes",
                "source_bytes",
                "truncated",
            ],
            label: "transcript preview"
        )
        guard let statusText = object["status"]?.stringValue,
              let status = TranscriptPreviewStatus(rawValue: statusText),
              object["source_artifact"]?.stringValue == "transcript.txt",
              object["byte_limit"]?.strictIntValue == 65_536,
              let displayedBytes = object["displayed_bytes"]?.strictIntValue,
              displayedBytes >= 0,
              displayedBytes <= 65_536,
              let truncated = object["truncated"]?.boolValue
        else {
            throw ResultContractError(
                "The engine returned malformed transcript preview evidence."
            )
        }
        let text = try nullableString(object["text"])
        let sourceBytes = try nullableSize(object["source_bytes"], label: "transcript")
        switch status {
        case .available:
            guard let text,
                  !text.isEmpty,
                  let sourceBytes,
                  Int64(displayedBytes) <= sourceBytes,
                  displayedBytes == text.utf8.count,
                  truncated == (Int64(displayedBytes) < sourceBytes)
            else {
                throw ResultContractError(
                    "The engine returned inconsistent transcript preview evidence."
                )
            }
        case .empty:
            guard text == "",
                  displayedBytes == 0,
                  sourceBytes == 0,
                  !truncated
            else {
                throw ResultContractError(
                    "The engine returned inconsistent empty transcript evidence."
                )
            }
        case .missing:
            guard text == nil,
                  displayedBytes == 0,
                  sourceBytes == nil,
                  !truncated
            else {
                throw ResultContractError(
                    "The engine attributed facts to a missing transcript preview."
                )
            }
        }
        return ResultTranscriptPreview(
            status: status,
            text: text,
            displayedBytes: displayedBytes,
            sourceBytes: sourceBytes,
            truncated: truncated
        )
    }

    private static func parseArtifact(_ value: JSONValue) throws -> PendingArtifactDescriptor {
        let object = try exactObject(
            value,
            keys: ["name", "size_bytes", "identity"],
            label: "result artifact"
        )
        guard let name = object["name"]?.stringValue,
              validArtifactName(name)
        else {
            throw ResultContractError("The engine returned an unsafe artifact name.")
        }
        guard let size = object["size_bytes"]?.strictIntValue,
              size >= 0
        else {
            throw ResultContractError("The engine returned an invalid artifact size.")
        }
        return PendingArtifactDescriptor(
            name: name,
            sizeBytes: Int64(size),
            identity: try parseIdentity(object["identity"], label: "result artifact")
        )
    }

    fileprivate static func parseIdentity(
        _ value: JSONValue?,
        label: String
    ) throws -> ArtifactFileIdentity {
        guard let value else {
            throw ResultContractError("The engine omitted the \(label) identity.")
        }
        let object = try exactObject(
            value,
            keys: ["device", "inode"],
            label: "\(label) identity"
        )
        guard let device = object["device"]?.strictIntValue,
              let inode = object["inode"]?.strictIntValue,
              device >= 0,
              inode >= 0
        else {
            throw ResultContractError("The engine returned an invalid \(label) identity.")
        }
        return ArtifactFileIdentity(
            device: UInt64(device),
            inode: UInt64(inode)
        )
    }

    private static func parseLanguage(_ value: JSONValue?) throws -> String? {
        guard let value else {
            throw ResultContractError("The engine omitted the result language field.")
        }
        if value.isNull { return nil }
        guard let language = value.stringValue,
              !language.isEmpty,
              language.utf8.count <= 32,
              language.allSatisfy({
                  $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_"
              })
        else {
            throw ResultContractError("The engine returned an invalid language tag.")
        }
        return language
    }

    private static func parseCount(
        _ value: JSONValue?,
        label: String
    ) throws -> Int {
        guard let count = value?.strictIntValue,
              count >= 0,
              count <= maximumCount
        else {
            throw ResultContractError("The engine returned an invalid \(label) count.")
        }
        return count
    }

    private static func nullableCount(
        _ value: JSONValue?,
        label: String
    ) throws -> Int? {
        guard let value else {
            throw ResultContractError("The engine omitted the \(label) field.")
        }
        if value.isNull { return nil }
        return try parseCount(value, label: label)
    }

    private static func nullableSize(
        _ value: JSONValue?,
        label: String
    ) throws -> Int64? {
        guard let value else {
            throw ResultContractError("The engine omitted the \(label) size field.")
        }
        if value.isNull { return nil }
        guard let size = value.strictIntValue, size >= 0 else {
            throw ResultContractError("The engine returned an invalid \(label) size.")
        }
        return Int64(size)
    }

    private static func nullableString(_ value: JSONValue?) throws -> String? {
        guard let value else {
            throw ResultContractError("The engine omitted a nullable result field.")
        }
        if value.isNull { return nil }
        guard let text = value.stringValue else {
            throw ResultContractError("The engine returned an invalid result string.")
        }
        return text
    }

    private static func validArtifactName(_ name: String) -> Bool {
        !name.isEmpty
            && name != "."
            && name != ".."
            && !name.hasPrefix(".")
            && name.utf8.count <= maximumArtifactNameBytes
            && !name.contains("/")
            && !name.contains("\0")
    }

    fileprivate static func exactObject(
        _ value: JSONValue,
        keys: Set<String>,
        label: String
    ) throws -> [String: JSONValue] {
        guard let object = value.objectValue,
              Set(object.keys) == keys
        else {
            throw ResultContractError("The engine returned a malformed \(label) object.")
        }
        return object
    }
}

struct ResultContractError: LocalizedError, Equatable {
    var message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? { message }
}
