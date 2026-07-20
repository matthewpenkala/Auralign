// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// A JSON tree used to carry pipeline configuration values without duplicating
/// the Python engine's typed model. Python remains the validation authority.
enum JSONValue: Equatable, Hashable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])
}

extension JSONValue: Codable {
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }
}

extension JSONValue {
    var isNull: Bool { self == .null }

    var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    var intValue: Int? {
        switch self {
        case .int(let value): return value
        case .double(let value)
        where value.isFinite
            && value == value.rounded()
            && value >= Double(Int.min)
            && value < Double(Int.max):
            return Int(value)
        default: return nil
        }
    }

    /// Protocol counts and indexes must arrive as JSON integers, not merely as
    /// a floating-point value that happens to have no fractional component.
    var strictIntValue: Int? {
        if case .int(let value) = self { return value }
        return nil
    }

    var doubleValue: Double? {
        switch self {
        case .double(let value): return value
        case .int(let value): return Double(value)
        default: return nil
        }
    }

    var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    /// Read a nested value along a dotted key path such as `asr.beam_size`.
    func value(at dottedPath: String) -> JSONValue? {
        var current = self
        for component in dottedPath.split(separator: ".") {
            guard let object = current.objectValue,
                  let next = object[String(component)] else { return nil }
            current = next
        }
        return current
    }

    /// Return a copy with `newValue` written at the dotted key path, creating
    /// intermediate objects as needed.
    func setting(_ dottedPath: String, to newValue: JSONValue) -> JSONValue {
        let components = dottedPath.split(separator: ".").map(String.init)
        return Self.write(into: self, path: components[...], value: newValue)
    }

    private static func write(
        into current: JSONValue,
        path: ArraySlice<String>,
        value: JSONValue
    ) -> JSONValue {
        guard let key = path.first else { return value }
        var object = current.objectValue ?? [:]
        let child = object[key] ?? .object([:])
        object[key] = write(into: child, path: path.dropFirst(), value: value)
        return .object(object)
    }

    /// Stable text rendering for display and change-detection hashing.
    func canonicalJSONString() -> String {
        switch self {
        case .null: return "null"
        case .bool(let value): return value ? "true" : "false"
        case .int(let value): return String(value)
        case .double(let value):
            if value == value.rounded() && abs(value) < 1e15 {
                return String(format: "%.1f", value)
            }
            return String(value)
        case .string(let value):
            let escaped = value
                .replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "\"", with: "\\\"")
                .replacingOccurrences(of: "\n", with: "\\n")
            return "\"\(escaped)\""
        case .array(let values):
            return "[" + values.map { $0.canonicalJSONString() }.joined(separator: ",") + "]"
        case .object(let entries):
            let body = entries.keys.sorted().map { key in
                "\"\(key)\":" + (entries[key] ?? .null).canonicalJSONString()
            }
            return "{" + body.joined(separator: ",") + "}"
        }
    }

    /// Compact human-readable rendering for value summaries in the UI.
    var displayText: String {
        switch self {
        case .null: return "—"
        case .bool(let value): return value ? "On" : "Off"
        case .int(let value): return String(value)
        case .double(let value):
            return value == value.rounded()
                ? String(format: "%.1f", value)
                : String(format: "%g", value)
        case .string(let value): return value
        case .array(let values):
            return values.map(\.displayText).joined(separator: ", ")
        case .object: return canonicalJSONString()
        }
    }

    func encodedData() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(self)
    }

    static func decode(from data: Data) throws -> JSONValue {
        try JSONDecoder().decode(JSONValue.self, from: data)
    }
}
