// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Darwin
import XCTest
@testable import Auralign

final class JobArtifactAccessTests: XCTestCase {
    private var temporaryContainers: [URL] = []

    override func tearDown() {
        for container in temporaryContainers {
            try? FileManager.default.removeItem(at: container)
        }
        temporaryContainers = []
        super.tearDown()
    }

    func testPartialInventoryAcceptsExactly512ArtifactsAndRejects513() throws {
        let fixture = try makeAuthority()
        try createJobDirectory(fixture.job)
        for index in 0..<JobArtifactAccess.maximumArtifacts {
            XCTAssertTrue(
                FileManager.default.createFile(
                    atPath: fixture.job
                        .appendingPathComponent(String(format: "artifact-%03d.txt", index))
                        .path,
                    contents: Data()
                )
            )
        }
        let identity = try fileIdentity(fixture.job)

        let accepted = try fixture.access.inventoryPartialAfterExit(
            expectedIdentity: identity
        )
        XCTAssertEqual(accepted?.artifacts.count, 512)

        XCTAssertTrue(
            FileManager.default.createFile(
                atPath: fixture.job.appendingPathComponent("overflow.txt").path,
                contents: Data()
            )
        )
        XCTAssertThrowsError(
            try fixture.access.inventoryPartialAfterExit(expectedIdentity: identity)
        ) { error in
            XCTAssertEqual((error as? JobArtifactAccessError)?.code, .tooManyEntries)
        }
    }

    func testHiddenEntriesCountToward1024DirectoryLimit() throws {
        let fixture = try makeAuthority()
        try createJobDirectory(fixture.job)
        for index in 0..<JobArtifactAccess.maximumDirectoryEntries {
            XCTAssertTrue(
                FileManager.default.createFile(
                    atPath: fixture.job
                        .appendingPathComponent(String(format: ".hidden-%04d", index))
                        .path,
                    contents: Data()
                )
            )
        }
        let identity = try fileIdentity(fixture.job)

        let accepted = try fixture.access.inventoryPartialAfterExit(
            expectedIdentity: identity
        )
        XCTAssertEqual(accepted?.artifacts, [])

        XCTAssertTrue(
            FileManager.default.createFile(
                atPath: fixture.job.appendingPathComponent(".hidden-overflow").path,
                contents: Data()
            )
        )
        XCTAssertThrowsError(
            try fixture.access.inventoryPartialAfterExit(expectedIdentity: identity)
        ) { error in
            XCTAssertEqual((error as? JobArtifactAccessError)?.code, .tooManyEntries)
        }
    }

    func testVisibleSymlinkAndFIFOFailClosedWhileDirectoriesAreOmitted() throws {
        do {
            let fixture = try makeAuthority()
            try createJobDirectory(fixture.job)
            let nested = fixture.job.appendingPathComponent("nested", isDirectory: true)
            try FileManager.default.createDirectory(
                at: nested,
                withIntermediateDirectories: false
            )
            let target = fixture.job.appendingPathComponent(".private-target")
            try Data().write(to: target)
            try FileManager.default.createSymbolicLink(
                at: fixture.job.appendingPathComponent("visible-link"),
                withDestinationURL: target
            )
            XCTAssertThrowsError(
                try fixture.access.inventoryPartialAfterExit(
                    expectedIdentity: fileIdentity(fixture.job)
                )
            ) { error in
                XCTAssertEqual((error as? JobArtifactAccessError)?.code, .unsafeEntry)
            }
        }

        do {
            let fixture = try makeAuthority()
            try createJobDirectory(fixture.job)
            let fifo = fixture.job.appendingPathComponent("visible-fifo")
            XCTAssertEqual(Darwin.mkfifo(fifo.path, 0o600), 0)
            XCTAssertThrowsError(
                try fixture.access.inventoryPartialAfterExit(
                    expectedIdentity: fileIdentity(fixture.job)
                )
            ) { error in
                XCTAssertEqual((error as? JobArtifactAccessError)?.code, .unsafeEntry)
            }
        }

        do {
            let fixture = try makeAuthority()
            try createJobDirectory(fixture.job)
            try FileManager.default.createDirectory(
                at: fixture.job.appendingPathComponent("nested", isDirectory: true),
                withIntermediateDirectories: false
            )
            let output = try fixture.access.inventoryPartialAfterExit(
                expectedIdentity: fileIdentity(fixture.job)
            )
            XCTAssertEqual(output?.artifacts, [])
        }
    }

    func testAcceptedChildReplacementSuppressesPartialAuthority() throws {
        let fixture = try makeAuthority()
        try createJobDirectory(fixture.job)
        let acceptedIdentity = try fileIdentity(fixture.job)
        try FileManager.default.removeItem(at: fixture.job)
        try createJobDirectory(fixture.job)
        try Data("partial".utf8).write(
            to: fixture.job.appendingPathComponent("partial.txt")
        )

        XCTAssertThrowsError(
            try fixture.access.inventoryPartialAfterExit(
                expectedIdentity: acceptedIdentity
            )
        ) { error in
            XCTAssertEqual(
                (error as? JobArtifactAccessError)?.code,
                .jobDirectoryChanged
            )
        }
    }

    func testRootPathReplacementInvalidatesRetainedAuthority() throws {
        let fixture = try makeAuthority()
        let moved = fixture.container.appendingPathComponent("moved-root")
        try FileManager.default.moveItem(at: fixture.root, to: moved)
        try FileManager.default.createDirectory(
            at: fixture.root,
            withIntermediateDirectories: false
        )
        try createJobDirectory(fixture.job)

        XCTAssertThrowsError(
            try fixture.access.inventoryPartialAfterExit(
                expectedIdentity: fileIdentity(fixture.job)
            )
        ) { error in
            XCTAssertEqual((error as? JobArtifactAccessError)?.code, .rootChanged)
        }
    }

    func testExactCompletionAndActionTimeReplacement() throws {
        let fixture = try makeAuthority()
        try createJobDirectory(fixture.job)
        let artifactURL = fixture.job.appendingPathComponent("summary.txt")
        try Data("verified".utf8).write(to: artifactURL)
        let outputIdentity = try fileIdentity(fixture.job)
        let artifactIdentity = try fileIdentity(artifactURL)
        let envelope = PendingJobResultEnvelope(
            outputDirectory: fixture.job,
            outputIdentity: outputIdentity,
            language: nil,
            speakerTurns: 0,
            words: 0,
            segments: 0,
            artifacts: [
                PendingArtifactDescriptor(
                    name: "summary.txt",
                    sizeBytes: 8,
                    identity: artifactIdentity
                ),
            ],
            qa: unavailableQA,
            transcriptPreview: missingPreview,
            configFingerprint: "fixture"
        )

        let completed = try fixture.access.materializeCompleted(envelope)
        let artifact = try XCTUnwrap(completed.artifacts.first)
        var actionRan = false
        try fixture.access.withRevalidatedArtifact(
            artifact,
            in: completed
        ) { _ in
            actionRan = true
        }
        XCTAssertTrue(actionRan)

        try FileManager.default.removeItem(at: artifactURL)
        try Data("replaced".utf8).write(to: artifactURL)
        XCTAssertThrowsError(
            try fixture.access.withRevalidatedArtifact(
                artifact,
                in: completed
            ) { _ in
                XCTFail("replacement must not reach the native action")
            }
        ) { error in
            XCTAssertEqual((error as? JobArtifactAccessError)?.code, .artifactChanged)
        }
    }

    private var unavailableQA: ResultQAProjection {
        ResultQAProjection(
            status: .missing,
            overallQualitySummary: nil,
            flagCount: nil,
            errorFlagCount: nil,
            warningFlagCount: nil,
            infoFlagCount: nil,
            changedSpanCount: nil,
            flags: [],
            omittedFlagCount: 0
        )
    }

    private var missingPreview: ResultTranscriptPreview {
        ResultTranscriptPreview(
            status: .missing,
            text: nil,
            displayedBytes: 0,
            sourceBytes: nil,
            truncated: false
        )
    }

    private func makeAuthority() throws -> (
        container: URL,
        root: URL,
        job: URL,
        access: JobArtifactAccess
    ) {
        let container = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "auralign-artifact-access-\(UUID().uuidString)",
                isDirectory: true
            )
        let root = container.appendingPathComponent("output", isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        temporaryContainers.append(container)
        let job = root.appendingPathComponent("job-output", isDirectory: true)
        return (
            container,
            root,
            job,
            try JobArtifactAccess.retain(
                outputRoot: root,
                plannedJobDirectory: job
            )
        )
    }

    private func createJobDirectory(_ url: URL) throws {
        try FileManager.default.createDirectory(
            at: url,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
    }

    private func fileIdentity(_ url: URL) throws -> ArtifactFileIdentity {
        var metadata = stat()
        let status = url.path.withCString { Darwin.lstat($0, &metadata) }
        XCTAssertEqual(status, 0)
        return ArtifactFileIdentity(
            device: UInt64(metadata.st_dev),
            inode: UInt64(metadata.st_ino)
        )
    }
}
