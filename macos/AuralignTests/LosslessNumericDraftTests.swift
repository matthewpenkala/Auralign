// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

final class LosslessNumericDraftTests: XCTestCase {
    func testIntegerDraftsPreserveExactBoundsAndInvalidText() {
        for value in [Int.min, Int.max, 0, -1, 1] {
            let text = String(value)
            let update = LosslessIntegerDraft.update(for: text)
            XCTAssertEqual(update.storedValue, .int(value), text)
            XCTAssertNil(update.validationError, text)
            XCTAssertEqual(
                LosslessIntegerDraft.text(for: update.storedValue),
                text
            )
        }

        for draft in ["", " ", "-", "+", "1.0", "1e2", "one"] {
            let update = LosslessIntegerDraft.update(for: draft)
            XCTAssertEqual(update.storedValue, .string(draft), draft)
            XCTAssertNotNil(update.validationError, draft)
            XCTAssertEqual(
                LosslessIntegerDraft.text(for: update.storedValue),
                draft
            )
        }
    }

    func testIntegerDraftsRejectOverflowWithoutLosingTheDraft() {
        let overflow = String(Int.max) + "0"
        let underflow = String(Int.min) + "0"
        for draft in [overflow, underflow] {
            let update = LosslessIntegerDraft.update(for: draft)
            XCTAssertEqual(update.storedValue, .string(draft))
            XCTAssertEqual(
                LosslessIntegerDraft.text(for: update.storedValue),
                draft
            )
            XCTAssertNotNil(update.validationError)
        }
    }

    func testScalarFormattingUsesRoundTripSafeRepresentations() {
        let cases: [(Double, String)] = [
            (0.12345678901234566, "0.12345678901234566"),
            (1e-12, "1e-12"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (0.2, "0.2"),
        ]

        for (value, expectedText) in cases {
            let text = LosslessDoubleParser.format(value)
            XCTAssertEqual(text, expectedText)
            XCTAssertEqual(LosslessDoubleParser.parse(expectedText), .success(value))
        }
    }

    func testScalarDraftUpdatesPreserveExactValuesAndInvalidText() {
        let precise = LosslessNumericDraft.scalarUpdate(for: "0.12345678901234566")
        XCTAssertEqual(precise.storedValue, .double(0.12345678901234566))
        XCTAssertNil(precise.validationError)

        let scientific = LosslessNumericDraft.scalarUpdate(for: "1e-12")
        XCTAssertEqual(scientific.storedValue, .double(1e-12))
        XCTAssertNil(scientific.validationError)

        let maximum = LosslessNumericDraft.scalarUpdate(for: "1.7976931348623157e308")
        XCTAssertEqual(maximum.storedValue, .double(Double.greatestFiniteMagnitude))
        XCTAssertNil(maximum.validationError)

        let incomplete = LosslessNumericDraft.scalarUpdate(for: "1e-")
        XCTAssertEqual(incomplete.storedValue, .string("1e-"))
        XCTAssertNotNil(incomplete.validationError)
        XCTAssertEqual(LosslessNumericDraft.scalarText(for: incomplete.storedValue), "1e-")
    }

    func testScalarParserRejectsEmptyIncompleteAndNonFiniteDrafts() {
        let invalidDrafts = [
            "",
            " ",
            "-",
            "+",
            ".",
            "1e",
            "1e-",
            "nan",
            "NaN",
            "inf",
            "-inf",
            "infinity",
            "1e309",
        ]

        for draft in invalidDrafts {
            assertScalarFailure(LosslessDoubleParser.parse(draft), draft: draft)
            let update = LosslessNumericDraft.scalarUpdate(for: draft)
            XCTAssertEqual(update.storedValue, .string(draft), draft)
            XCTAssertNotNil(update.validationError, draft)
        }
    }

    func testListFormattingAndParsingRoundTripWithoutPrecisionLoss() {
        let values = [
            0.12345678901234566,
            1e-12,
            1.7976931348623157e308,
        ]
        let expected = "0.12345678901234566, 1e-12, 1.7976931348623157e+308"

        XCTAssertEqual(FloatListParser.format(values), expected)
        XCTAssertEqual(FloatListParser.parse(expected), .success(values))
        XCTAssertEqual(
            LosslessNumericDraft.sequenceText(
                for: .array(values.map(JSONValue.double))
            ),
            expected
        )
    }

    func testFloatOrListIdentitySurvivesSingleValueEditing() {
        let scalar = LosslessNumericDraft.sequenceUpdate(
            for: "0.2",
            preserving: .scalar
        )
        XCTAssertEqual(scalar.storedValue, .double(0.2))
        XCTAssertEqual(scalar.shape, .scalar)

        let oneItemList = LosslessNumericDraft.sequenceUpdate(
            for: "0.2",
            preserving: .list
        )
        XCTAssertEqual(oneItemList.storedValue, .array([.double(0.2)]))
        XCTAssertEqual(oneItemList.shape, .list)

        let promotedList = LosslessNumericDraft.sequenceUpdate(
            for: "0.2, 0.4",
            preserving: .scalar
        )
        XCTAssertEqual(
            promotedList.storedValue,
            .array([.double(0.2), .double(0.4)])
        )
        XCTAssertEqual(promotedList.shape, .list)
    }

    func testInvalidListDraftsRemainVerbatimAndFailClosed() {
        let invalidDrafts = [
            "",
            "0,,0.4",
            "0,",
            ",0",
            "0, 1e-",
            "0, nan",
            "0, inf",
            "0, 1e309",
        ]

        for draft in invalidDrafts {
            let update = LosslessNumericDraft.sequenceUpdate(
                for: draft,
                preserving: .list
            )
            XCTAssertEqual(update.storedValue, .string(draft), draft)
            XCTAssertNotNil(update.validationError, draft)
            XCTAssertEqual(
                LosslessNumericDraft.sequenceText(for: update.storedValue),
                draft
            )
        }
    }

    private func assertScalarFailure(
        _ result: LosslessDoubleParseResult,
        draft: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        guard case .failure(let message) = result else {
            return XCTFail("Expected “\(draft)” to fail.", file: file, line: line)
        }
        XCTAssertFalse(message.isEmpty, file: file, line: line)
    }
}
