// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import XCTest
@testable import Auralign

private struct StubMediaDropProvider: MediaDropURLProvider {
    let canLoadAuralignFileURL: Bool
    let url: URL?

    func loadAuralignFileURL() async -> URL? {
        url
    }
}

@MainActor
final class MediaDropResolverTests: XCTestCase {
    func testUnsupportedDropFailsBeforeAsynchronousAcceptance() async {
        let providers = [
            StubMediaDropProvider(canLoadAuralignFileURL: false, url: nil),
        ]
        XCTAssertFalse(MediaDropResolver.hasSupportedProvider(providers))
        let resolution = await MediaDropResolver.resolve(providers) { _ in
            XCTFail("an unsupported drop must not reach model acceptance")
            return true
        }
        XCTAssertEqual(resolution, .unsupported)
    }

    func testUnreadableProviderProducesExplicitFailure() async {
        let providers = [
            StubMediaDropProvider(canLoadAuralignFileURL: true, url: nil),
        ]
        XCTAssertTrue(MediaDropResolver.hasSupportedProvider(providers))
        let resolution = await MediaDropResolver.resolve(providers) { _ in
            XCTFail("a failed provider must not reach model acceptance")
            return true
        }
        XCTAssertEqual(resolution, .unreadable)
    }

    func testMultipleProvidersSelectFirstActuallyLoadableURL() async {
        let expected = URL(fileURLWithPath: "/AURALIGN_SYNTHETIC/accepted.wav")
        let ignored = URL(fileURLWithPath: "/AURALIGN_SYNTHETIC/later.wav")
        let providers = [
            StubMediaDropProvider(canLoadAuralignFileURL: true, url: nil),
            StubMediaDropProvider(canLoadAuralignFileURL: true, url: expected),
            StubMediaDropProvider(canLoadAuralignFileURL: true, url: ignored),
        ]
        var accepted: [URL] = []
        let resolution = await MediaDropResolver.resolve(providers) { url in
            accepted.append(url)
            return true
        }
        XCTAssertEqual(resolution, .selected)
        XCTAssertEqual(accepted, [expected])
    }

    func testModelRejectionStopsAtFirstLoadableURL() async {
        let first = URL(fileURLWithPath: "/AURALIGN_SYNTHETIC/rejected.wav")
        let second = URL(fileURLWithPath: "/AURALIGN_SYNTHETIC/not-attempted.wav")
        let providers = [
            StubMediaDropProvider(canLoadAuralignFileURL: true, url: first),
            StubMediaDropProvider(canLoadAuralignFileURL: true, url: second),
        ]
        var attempted: [URL] = []
        let resolution = await MediaDropResolver.resolve(providers) { url in
            attempted.append(url)
            return false
        }
        XCTAssertEqual(resolution, .rejected)
        XCTAssertEqual(attempted, [first])
    }
}
