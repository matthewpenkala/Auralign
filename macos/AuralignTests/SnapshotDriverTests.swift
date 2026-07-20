// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

#if DEBUG

import CoreGraphics
import XCTest
@testable import Auralign

@MainActor
final class SnapshotDriverTests: XCTestCase {
    func testOutputRootRequiresExplicitGateAndExistingDirectory() throws {
        let fileManager = FileManager.default
        let temporaryRoot = fileManager.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fileManager.createDirectory(
            at: temporaryRoot,
            withIntermediateDirectories: true
        )
        defer { try? fileManager.removeItem(at: temporaryRoot) }

        XCTAssertNil(
            SnapshotDriver.configuredOutputRoot(
                environment: ["AURALIGN_SNAPSHOT_DIR": temporaryRoot.path]
            )
        )
        XCTAssertNil(
            SnapshotDriver.configuredOutputRoot(
                environment: [
                    "AURALIGN_ENABLE_DEBUG_AUTOMATION": "true",
                    "AURALIGN_SNAPSHOT_DIR": temporaryRoot.path,
                ]
            )
        )
        XCTAssertNil(
            SnapshotDriver.configuredOutputRoot(
                environment: [
                    PresentationFixture.enableEnvironmentKey: "1",
                    "AURALIGN_ENABLE_DEBUG_AUTOMATION": "1",
                    "AURALIGN_SNAPSHOT_DIR": "relative-captures",
                ]
            )
        )
        XCTAssertEqual(
            SnapshotDriver.configuredOutputRoot(
                environment: [
                    PresentationFixture.enableEnvironmentKey: "1",
                    "AURALIGN_ENABLE_DEBUG_AUTOMATION": "1",
                    "AURALIGN_SNAPSHOT_DIR": temporaryRoot.path,
                ]
            ),
            temporaryRoot.standardizedFileURL.resolvingSymlinksInPath()
        )

        let missing = temporaryRoot.appendingPathComponent("missing", isDirectory: true)
        XCTAssertNil(
            SnapshotDriver.configuredOutputRoot(
                environment: [
                    PresentationFixture.enableEnvironmentKey: "1",
                    "AURALIGN_ENABLE_DEBUG_AUTOMATION": "1",
                    "AURALIGN_SNAPSHOT_DIR": missing.path,
                ]
            )
        )

        let ordinaryFile = temporaryRoot.appendingPathComponent("ordinary-file")
        try Data("not a directory".utf8).write(to: ordinaryFile)
        XCTAssertNil(
            SnapshotDriver.configuredOutputRoot(
                environment: [
                    PresentationFixture.enableEnvironmentKey: "1",
                    "AURALIGN_ENABLE_DEBUG_AUTOMATION": "1",
                    "AURALIGN_SNAPSHOT_DIR": ordinaryFile.path,
                ]
            )
        )
    }

    func testAppearanceParserAcceptsOnlyExactLightAndDarkValues() {
        XCTAssertEqual(SnapshotDriver.parseAppearance("light"), .light)
        XCTAssertEqual(SnapshotDriver.parseAppearance("dark"), .dark)
        XCTAssertNil(SnapshotDriver.parseAppearance(""))
        XCTAssertNil(SnapshotDriver.parseAppearance("Dark"))
        XCTAssertNil(SnapshotDriver.parseAppearance("system"))
        XCTAssertNil(SnapshotDriver.parseAppearance(" light"))
    }

    func testWindowActivityParserAcceptsOnlyExactSupportedValues() {
        XCTAssertEqual(
            SnapshotDriver.parseWindowActivity("active"),
            .active
        )
        XCTAssertEqual(
            SnapshotDriver.parseWindowActivity("inactive"),
            .inactive
        )
        XCTAssertNil(SnapshotDriver.parseWindowActivity(""))
        XCTAssertNil(SnapshotDriver.parseWindowActivity("Active"))
        XCTAssertNil(SnapshotDriver.parseWindowActivity("background"))
    }

    func testSidebarParserAndAuthorizationFailClosed() {
        XCTAssertEqual(
            SnapshotDriver.parseSidebarVisibility("expanded"),
            .expanded
        )
        XCTAssertEqual(
            SnapshotDriver.parseSidebarVisibility("collapsed"),
            .collapsed
        )
        for invalid in ["", "Expanded", "hidden", " collapsed"] {
            XCTAssertNil(SnapshotDriver.parseSidebarVisibility(invalid))
        }

        let requested = [
            PresentationFixture.enableEnvironmentKey: "1",
            "AURALIGN_ENABLE_DEBUG_AUTOMATION": "1",
            "AURALIGN_SNAPSHOT_SIDEBAR": "collapsed",
        ]
        XCTAssertEqual(
            SnapshotDriver.authorizedInitialColumnVisibility(
                environment: requested
            ),
            .detailOnly
        )
        XCTAssertEqual(
            SnapshotDriver.authorizedInitialColumnVisibility(
                environment: ["AURALIGN_SNAPSHOT_SIDEBAR": "collapsed"]
            ),
            .all
        )
        var malformed = requested
        malformed["AURALIGN_SNAPSHOT_SIDEBAR"] = "hidden"
        XCTAssertEqual(
            SnapshotDriver.authorizedInitialColumnVisibility(
                environment: malformed
            ),
            .all
        )
    }

    func testSettingsAutoOpenRequiresTheCompleteAuthorizedCaptureContract() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let destination = root.appendingPathComponent("settings.png")
        let valid: [String: String] = [
            PresentationFixture.enableEnvironmentKey: "1",
            "AURALIGN_ENABLE_DEBUG_AUTOMATION": "1",
            "AURALIGN_SNAPSHOT_DIR": root.path,
            "AURALIGN_SNAPSHOT_ON_LAUNCH": destination.path,
            "AURALIGN_SNAPSHOT_WINDOW_ROLE": SnapshotWindowRole.settings.rawValue,
            "AURALIGN_SNAPSHOT_NONCE": UUID().uuidString,
        ]

        XCTAssertTrue(SnapshotDriver.shouldAutoOpenSettings(environment: valid))
        for key in valid.keys {
            var incomplete = valid
            incomplete.removeValue(forKey: key)
            XCTAssertFalse(
                SnapshotDriver.shouldAutoOpenSettings(environment: incomplete),
                key
            )
        }
        var mainRole = valid
        mainRole["AURALIGN_SNAPSHOT_WINDOW_ROLE"] = SnapshotWindowRole.main.rawValue
        XCTAssertFalse(SnapshotDriver.shouldAutoOpenSettings(environment: mainRole))
        var outside = valid
        outside["AURALIGN_SNAPSHOT_ON_LAUNCH"] = root
            .deletingLastPathComponent()
            .appendingPathComponent("outside.png").path
        XCTAssertFalse(SnapshotDriver.shouldAutoOpenSettings(environment: outside))
    }

    func testWindowSizeRequiresExactlyTwoFinitePositiveDimensions() throws {
        let size = try XCTUnwrap(SnapshotDriver.parseWindowSize("900,600"))
        XCTAssertEqual(size.width, 900)
        XCTAssertEqual(size.height, 600)

        for invalidValue in [
            "",
            "900",
            "900,600,2",
            "900,",
            ",600",
            "0,600",
            "-900,600",
            "nan,600",
            "900,inf",
            "900,-infinity",
        ] {
            XCTAssertNil(
                SnapshotDriver.parseWindowSize(invalidValue),
                invalidValue
            )
        }
    }

    func testSectionParserAcceptsOnlyKnownDestinationsAndCatalogSections() {
        let catalogSections: Set<String> = ["source", "asr", "output"]

        XCTAssertEqual(
            SnapshotDriver.parseSection(
                "allParameters",
                availableSectionIDs: catalogSections
            ),
            .allParameters
        )
        XCTAssertEqual(
            SnapshotDriver.parseSection(
                "asr",
                availableSectionIDs: catalogSections
            ),
            .configuration("asr")
        )
        XCTAssertNil(
            SnapshotDriver.parseSection(
                "not-a-destination",
                availableSectionIDs: catalogSections
            )
        )
        XCTAssertNil(
            SnapshotDriver.parseSection(
                "",
                availableSectionIDs: catalogSections
            )
        )
    }

    func testDestinationMustBeAChildOfTheResolvedRoot() throws {
        let fileManager = FileManager.default
        let container = fileManager.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let root = container.appendingPathComponent("captures", isDirectory: true)
        let sibling = container.appendingPathComponent("captures-sibling", isDirectory: true)
        let outside = container.appendingPathComponent("outside", isDirectory: true)
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: sibling, withIntermediateDirectories: true)
        try fileManager.createDirectory(at: outside, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: container) }

        let directChild = root.appendingPathComponent("window.png")
        XCTAssertEqual(
            SnapshotDriver.authorizeDestination(directChild.path, under: root),
            directChild
        )
        XCTAssertNil(SnapshotDriver.authorizeDestination(root.path, under: root))
        XCTAssertNil(
            SnapshotDriver.authorizeDestination(
                sibling.appendingPathComponent("prefix.png").path,
                under: root
            )
        )
        XCTAssertNil(
            SnapshotDriver.authorizeDestination(
                root.appendingPathComponent("../outside/traversal.png").path,
                under: root
            )
        )
        XCTAssertNil(
            SnapshotDriver.authorizeDestination("relative.png", under: root)
        )

        let escapeLink = root.appendingPathComponent("escape", isDirectory: true)
        try fileManager.createSymbolicLink(at: escapeLink, withDestinationURL: outside)
        XCTAssertNil(
            SnapshotDriver.authorizeDestination(
                escapeLink.appendingPathComponent("escaped.png").path,
                under: root
            )
        )

        let outsideFile = outside.appendingPathComponent("outside.png")
        try Data("outside".utf8).write(to: outsideFile)
        let fileEscapeLink = root.appendingPathComponent("file-escape.png")
        try fileManager.createSymbolicLink(
            at: fileEscapeLink,
            withDestinationURL: outsideFile
        )
        XCTAssertNil(
            SnapshotDriver.authorizeDestination(fileEscapeLink.path, under: root)
        )
    }

    func testAtomicWriteReportsSuccessAndFailure() throws {
        let fileManager = FileManager.default
        let root = fileManager.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: root) }

        let destination = root.appendingPathComponent("snapshot.png")
        let first = Data([0x89, 0x50, 0x4E, 0x47])
        let replacement = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A])
        XCTAssertTrue(SnapshotDriver.writeAtomically(first, to: destination))
        XCTAssertEqual(try Data(contentsOf: destination), first)
        XCTAssertTrue(SnapshotDriver.writeAtomically(replacement, to: destination))
        XCTAssertEqual(try Data(contentsOf: destination), replacement)

        let missingParent = root
            .appendingPathComponent("missing", isDirectory: true)
            .appendingPathComponent("snapshot.png")
        XCTAssertFalse(
            SnapshotDriver.writeAtomically(replacement, to: missingParent)
        )
    }

    func testExitAfterCaptureRequiresARequestedSuccessfulWrite() {
        XCTAssertTrue(
            SnapshotDriver.shouldExitAfterCapture(
                requested: true,
                captureSucceeded: true
            )
        )
        XCTAssertFalse(
            SnapshotDriver.shouldExitAfterCapture(
                requested: true,
                captureSucceeded: false
            )
        )
        XCTAssertFalse(
            SnapshotDriver.shouldExitAfterCapture(
                requested: false,
                captureSucceeded: true
            )
        )
    }

    func testNewAtomicWriteNeverOverwritesEvidence() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let destination = root.appendingPathComponent("capture.png")
        XCTAssertTrue(
            SnapshotDriver.writeNewAtomically(Data("first".utf8), to: destination)
        )
        XCTAssertFalse(
            SnapshotDriver.writeNewAtomically(Data("second".utf8), to: destination)
        )
        XCTAssertEqual(try Data(contentsOf: destination), Data("first".utf8))
        XCTAssertNil(
            SnapshotDriver.authorizeDestination(destination.path, under: root)
        )
    }

    func testVisualValidationRejectsTransparentSolidAndWrongSizedImages() throws {
        let transparent = try makeImage(width: 64, height: 64) { _, _ in
            (0, 0, 0, 0)
        }
        let solid = try makeImage(width: 64, height: 64) { _, _ in
            (255, 255, 255, 255)
        }
        let varied = try makeImage(width: 64, height: 64) { x, y in
            (
                UInt8((x * 4) % 256),
                UInt8((y * 4) % 256),
                UInt8(((x + y) * 2) % 256),
                255
            )
        }

        XCTAssertFalse(SnapshotDriver.isVisuallyValid(transparent))
        XCTAssertFalse(SnapshotDriver.isVisuallyValid(solid))
        XCTAssertTrue(
            SnapshotDriver.isVisuallyValid(
                varied,
                expectedPixelSize: CGSize(width: 64, height: 64)
            )
        )
        XCTAssertFalse(
            SnapshotDriver.isVisuallyValid(
                varied,
                expectedPixelSize: CGSize(width: 96, height: 64)
            )
        )
    }

    private func makeImage(
        width: Int,
        height: Int,
        pixel: (Int, Int) -> (UInt8, UInt8, UInt8, UInt8)
    ) throws -> CGImage {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                let value = pixel(x, y)
                let offset = (y * width + x) * 4
                bytes[offset] = value.0
                bytes[offset + 1] = value.1
                bytes[offset + 2] = value.2
                bytes[offset + 3] = value.3
            }
        }
        let provider = try XCTUnwrap(
            CGDataProvider(data: Data(bytes) as CFData)
        )
        return try XCTUnwrap(
            CGImage(
                width: width,
                height: height,
                bitsPerComponent: 8,
                bitsPerPixel: 32,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(
                    rawValue: CGImageAlphaInfo.premultipliedLast.rawValue
                        | CGBitmapInfo.byteOrder32Big.rawValue
                ),
                provider: provider,
                decode: nil,
                shouldInterpolate: false,
                intent: .defaultIntent
            )
        )
    }
}

#endif
