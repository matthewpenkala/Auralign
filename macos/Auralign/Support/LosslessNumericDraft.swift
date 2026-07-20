// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

enum LosslessDoubleParseResult: Equatable {
    case success(Double)
    case failure(String)
}

/// Locale-independent floating-point parsing and rendering for configuration
/// drafts. `String(Double)` emits Swift's shortest round-trip-safe
/// representation, including scientific notation where appropriate.
enum LosslessDoubleParser {
    static func parse(_ text: String) -> LosslessDoubleParseResult {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            return .failure("Enter a finite number.")
        }
        guard let value = Double(trimmed), value.isFinite else {
            return .failure("Enter a finite number; “\(trimmed)” is invalid.")
        }
        return .success(value)
    }

    static func format(_ value: Double) -> String? {
        guard value.isFinite else { return nil }
        return String(value)
    }
}

enum FloatListParseResult: Equatable {
    case success([Double])
    case failure(String)
}

enum FloatListParser {
    static func parse(_ text: String) -> FloatListParseResult {
        let trimmedInput = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedInput.isEmpty else {
            return .failure("Enter at least one number.")
        }
        let tokens = text.split(separator: ",", omittingEmptySubsequences: false)
        var values: [Double] = []
        values.reserveCapacity(tokens.count)
        for (offset, token) in tokens.enumerated() {
            let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else {
                return .failure("Value \(offset + 1) is empty.")
            }
            guard let number = Double(trimmed), number.isFinite else {
                return .failure("Value \(offset + 1) is not a finite number: “\(trimmed)”.")
            }
            values.append(number)
        }
        return .success(values)
    }

    static func format(_ values: [Double]) -> String? {
        var rendered: [String] = []
        rendered.reserveCapacity(values.count)
        for value in values {
            guard let text = LosslessDoubleParser.format(value) else { return nil }
            rendered.append(text)
        }
        return rendered.joined(separator: ", ")
    }
}

enum FloatOrListShape: Equatable {
    case scalar
    case list

    init?(value: JSONValue) {
        switch value {
        case .double, .int:
            self = .scalar
        case .array:
            self = .list
        case .null, .bool, .string, .object:
            return nil
        }
    }
}

struct NumericDraftUpdate: Equatable {
    var storedValue: JSONValue
    var validationError: String?
    var shape: FloatOrListShape
}

/// Locale-independent whole-number editing for schema fields whose Python
/// authority requires an integer. Invalid or incomplete drafts remain exact
/// strings so navigation, focus changes, and SwiftUI re-rendering cannot
/// silently replace what the user typed with a preset/default value.
enum LosslessIntegerDraft {
    static func text(for value: JSONValue) -> String {
        if let draft = value.stringValue {
            return draft
        }
        guard let value = value.strictIntValue else { return "" }
        return String(value)
    }

    static func update(for text: String) -> NumericDraftUpdate {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let value = Int(trimmed) else {
            return NumericDraftUpdate(
                storedValue: .string(text),
                validationError: "Enter a whole number.",
                shape: .scalar
            )
        }
        return NumericDraftUpdate(
            storedValue: .int(value),
            validationError: nil,
            shape: .scalar
        )
    }
}

/// Converts editable numeric text into the exact JSON value sent through the
/// existing ConfigStore. Invalid drafts remain strings so Python validation
/// continues to fail closed without the UI silently repairing user input.
enum LosslessNumericDraft {
    static func scalarText(for value: JSONValue) -> String {
        if let draft = value.stringValue {
            return draft
        }
        guard let number = value.doubleValue else { return "" }
        return LosslessDoubleParser.format(number) ?? String(number)
    }

    static func scalarUpdate(for text: String) -> NumericDraftUpdate {
        switch LosslessDoubleParser.parse(text) {
        case .success(let value):
            return NumericDraftUpdate(
                storedValue: .double(value),
                validationError: nil,
                shape: .scalar
            )
        case .failure(let message):
            return NumericDraftUpdate(
                storedValue: .string(text),
                validationError: message,
                shape: .scalar
            )
        }
    }

    static func sequenceText(for value: JSONValue) -> String {
        if let draft = value.stringValue {
            return draft
        }
        if let values = value.arrayValue {
            let numbers = values.compactMap(\.doubleValue)
            guard numbers.count == values.count else {
                return values.map { $0.canonicalJSONString() }.joined(separator: ", ")
            }
            return FloatListParser.format(numbers)
                ?? numbers.map { String($0) }.joined(separator: ", ")
        }
        guard let number = value.doubleValue else { return "" }
        return LosslessDoubleParser.format(number) ?? String(number)
    }

    static func sequenceUpdate(
        for text: String,
        preserving preferredShape: FloatOrListShape
    ) -> NumericDraftUpdate {
        let intendedShape: FloatOrListShape = text.contains(",") ? .list : preferredShape
        switch FloatListParser.parse(text) {
        case .success(let values):
            let shape: FloatOrListShape = values.count > 1 ? .list : intendedShape
            let storedValue: JSONValue
            switch shape {
            case .scalar:
                storedValue = .double(values[0])
            case .list:
                storedValue = .array(values.map(JSONValue.double))
            }
            return NumericDraftUpdate(
                storedValue: storedValue,
                validationError: nil,
                shape: shape
            )
        case .failure(let message):
            return NumericDraftUpdate(
                storedValue: .string(text),
                validationError: message,
                shape: intendedShape
            )
        }
    }
}
