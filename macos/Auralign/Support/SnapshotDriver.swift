// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit

enum SnapshotWindowRole: String, CaseIterable {
    case main
    case modelSetup = "model-setup"
    case settings

    var windowIdentifier: String {
        "com.auralign.studio.window.\(rawValue)"
    }
}

#if DEBUG
import ApplicationServices
import CryptoKit
import Darwin
import ScreenCaptureKit
import SwiftUI

/// Debug-build-only automation hook for development verification: drives the
/// real AppModel through distributed notifications and writes window
/// snapshots rendered by the app itself (no screen-recording permission
/// required). Compiled out of release builds entirely.
@MainActor
enum SnapshotDriver {
    static let notificationName = Notification.Name("com.auralign.studio.debug.command")
    private static var observer: NSObjectProtocol?
    private static var outputRoot: URL?
    private static var effectiveSidebarVisibility = "expanded"

    enum SnapshotAppearance: Equatable {
        case light
        case dark

        var appKitName: NSAppearance.Name {
            switch self {
            case .light: .aqua
            case .dark: .darkAqua
            }
        }
    }

    enum SnapshotWindowActivity: String, Equatable {
        case active
        case inactive
    }

    enum SnapshotSidebarVisibility: String, Equatable {
        case expanded
        case collapsed

        var navigationVisibility: NavigationSplitViewVisibility {
            switch self {
            case .expanded: .all
            case .collapsed: .detailOnly
            }
        }
    }

    static func activate(model: AppModel) {
        let environment = ProcessInfo.processInfo.environment
        guard model.isPresentationFixture,
              model.hasApplicationOwnership,
              observer == nil,
              let root = configuredOutputRoot(environment: environment) else { return }
        outputRoot = root
        observer = DistributedNotificationCenter.default().addObserver(
            forName: notificationName,
            object: nil,
            queue: .main
        ) { notification in
            guard let command = notification.object as? String else { return }
            MainActor.assumeIsolated {
                handle(command: command, model: model)
            }
        }
        scheduleStartupCaptureIfRequested(environment: environment, model: model)
    }

    private static func scheduleStartupCaptureIfRequested(
        environment: [String: String],
        model: AppModel
    ) {
        guard let destination = environment["AURALIGN_SNAPSHOT_ON_LAUNCH"],
              !destination.isEmpty else { return }
        let requestedSection: WorkspaceSection?
        if let rawSection = environment["AURALIGN_SNAPSHOT_SECTION"] {
            guard let parsedSection = parseSection(
                rawSection,
                availableSectionIDs: Set(model.catalog.sections.map(\.id))
            ) else { return }
            requestedSection = parsedSection
        } else {
            requestedSection = nil
        }
        let requestedAppearance: SnapshotAppearance?
        if let rawAppearance = environment["AURALIGN_SNAPSHOT_APPEARANCE"] {
            guard let parsedAppearance = parseAppearance(rawAppearance) else { return }
            requestedAppearance = parsedAppearance
        } else {
            requestedAppearance = nil
        }
        let requestedSize: NSSize?
        if let rawSize = environment["AURALIGN_SNAPSHOT_WINDOW_SIZE"] {
            guard let parsedSize = parseWindowSize(rawSize) else { return }
            requestedSize = parsedSize
        } else {
            requestedSize = nil
        }
        let requestedWindowRole: SnapshotWindowRole
        if let rawRole = environment["AURALIGN_SNAPSHOT_WINDOW_ROLE"] {
            guard let parsedRole = SnapshotWindowRole(rawValue: rawRole) else {
                return
            }
            requestedWindowRole = parsedRole
        } else {
            requestedWindowRole = .main
        }
        let requestedWindowActivity: SnapshotWindowActivity
        if let rawActivity = environment["AURALIGN_SNAPSHOT_WINDOW_ACTIVITY"] {
            guard let parsedActivity = parseWindowActivity(rawActivity) else {
                return
            }
            requestedWindowActivity = parsedActivity
        } else {
            requestedWindowActivity = .active
        }
        if let rawSidebar = environment["AURALIGN_SNAPSHOT_SIDEBAR"],
           parseSidebarVisibility(rawSidebar) == nil {
            return
        }
        let shouldExit = environment["AURALIGN_SNAPSHOT_EXIT_AFTER_CAPTURE"] == "1"
        guard let nonceRaw = environment["AURALIGN_SNAPSHOT_NONCE"],
              let nonce = UUID(uuidString: nonceRaw)
        else { return }

        Task { @MainActor in
            // Give the scene one run-loop turn to create its first window,
            // then drive only production model/window state.
            await Task.yield()
            if let requestedSection {
                model.selectedSection = requestedSection
            }
            if let requestedAppearance {
                NSApp.appearance = NSAppearance(named: requestedAppearance.appKitName)
            }
            guard let window = await waitForWindow(
                role: requestedWindowRole,
                model: model
            ) else {
                terminateAfterStartupCaptureIfRequested(
                    shouldExit,
                    model: model
                )
                return
            }
            applyWindowActivity(requestedWindowActivity, to: window)

            if let requestedSize,
               abs(window.frame.width - requestedSize.width) > 0.5
                || abs(window.frame.height - requestedSize.height) > 0.5 {
                setWindowFrameSize(requestedSize, window: window)
                window.center()
            }
            guard await waitForStableLayout(
                window: window,
                requestedSize: requestedSize
            ) else {
                terminateAfterStartupCaptureIfRequested(
                    shouldExit,
                    model: model
                )
                return
            }
            // Size first: minimum-width adaptation can rebuild the exact field
            // editor. Hidden-field recovery is accepted only after the final
            // geometry exists and that real editor owns keyboard focus.
            guard await waitForNavigationRecovery(
                model: model,
                window: window
            ) else {
                terminateAfterStartupCaptureIfRequested(
                    shouldExit,
                    model: model
                )
                return
            }
            guard await waitForStableLayout(
                window: window,
                requestedSize: requestedSize
            ), navigationRecoveryIsComplete(model: model, window: window)
            else {
                terminateAfterStartupCaptureIfRequested(
                    shouldExit,
                    model: model
                )
                return
            }
            // Navigation, focus recovery, and frame changes can alter key/main
            // status. Reassert the requested activity state at the final
            // evidence boundary and refuse to capture a mismatched window.
            applyWindowActivity(requestedWindowActivity, to: window)
            guard await waitForWindowActivity(
                requestedWindowActivity,
                window: window
            ) else {
                terminateAfterStartupCaptureIfRequested(
                    shouldExit,
                    model: model
                )
                return
            }

            let captureSucceeded = await snapshot(
                to: destination,
                role: requestedWindowRole,
                nonce: nonce,
                model: model
            )
            if shouldExitAfterCapture(
                requested: shouldExit,
                captureSucceeded: captureSucceeded
            ) {
                terminateAfterStartupCaptureIfRequested(
                    shouldExit,
                    model: model
                )
            }
        }
    }

    private static func waitForWindow(
        role: SnapshotWindowRole,
        model: AppModel,
        attempts: Int = 80
    ) async -> NSWindow? {
        for _ in 0..<attempts {
            if let window = window(for: role) {
                return window
            }
            if role == .settings,
               !shouldAutoOpenSettings(
                   environment: ProcessInfo.processInfo.environment
               ) {
                return nil
            }
            try? await Task.sleep(nanoseconds: 100_000_000)
            if Task.isCancelled || !model.isPresentationFixture {
                return nil
            }
        }
        return nil
    }

    private static func applyWindowActivity(
        _ activity: SnapshotWindowActivity,
        to window: NSWindow
    ) {
        switch activity {
        case .active:
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
        case .inactive:
            window.orderFront(nil)
            window.resignKey()
            NSApp.deactivate()
        }
    }

    private static func waitForWindowActivity(
        _ activity: SnapshotWindowActivity,
        window: NSWindow,
        attempts: Int = 50
    ) async -> Bool {
        for _ in 0..<attempts {
            let matches: Bool
            switch activity {
            case .active:
                matches = NSApp.isActive
                    && window.isKeyWindow
                    && window.isMainWindow
            case .inactive:
                matches = !NSApp.isActive && !window.isKeyWindow
            }
            if matches { return true }
            applyWindowActivity(activity, to: window)
            try? await Task.sleep(nanoseconds: 100_000_000)
            if Task.isCancelled { return false }
        }
        return false
    }

    private static func waitForNavigationRecovery(
        model: AppModel,
        window: NSWindow,
        attempts: Int = 80
    ) async -> Bool {
        for _ in 0..<attempts {
            if navigationRecoveryIsComplete(model: model, window: window) {
                // Permit focus-ring and accessibility state to settle after
                // the completion marker is published.
                try? await Task.sleep(nanoseconds: 200_000_000)
                return navigationRecoveryIsComplete(
                    model: model,
                    window: window
                )
            }
            try? await Task.sleep(nanoseconds: 100_000_000)
            if Task.isCancelled { return false }
        }
        return false
    }

    private static func navigationRecoveryIsComplete(
        model: AppModel,
        window: NSWindow
    ) -> Bool {
        let fieldReady = model.fieldNavigationRequest.map { request in
            model.consumedFieldNavigationRequestID == request.id
                && keyboardFocusedAccessibilityIdentifier(
                    in: window,
                    model: model
                )
                    == AccessibilityIdentifier.fieldControl(request.fieldKey)
        } ?? true
        let sectionReady = model.sectionNavigationRequest.map {
            model.consumedSectionNavigationRequestID == $0.id
        } ?? true
        return fieldReady && sectionReady
    }

    private static func keyboardFocusedAccessibilityIdentifier(
        in window: NSWindow,
        model: AppModel
    ) -> String? {
        if let identifier =
            model.keyboardFocusedConfigurationControlIdentifier {
            return identifier
        }
        let firstResponder = window.firstResponder
        var accessibilityCandidate: Any? = firstResponder
        for _ in 0..<12 {
            guard let candidate = accessibilityCandidate
                as? NSAccessibilityProtocol
            else { break }
            if let identifier = candidate.accessibilityIdentifier(),
               !identifier.isEmpty {
                return identifier
            }
            accessibilityCandidate = candidate.accessibilityParent()
        }
        let applicationElement = AXUIElementCreateApplication(
            ProcessInfo.processInfo.processIdentifier
        )
        var focusedValue: CFTypeRef?
        if AXUIElementCopyAttributeValue(
            applicationElement,
            kAXFocusedUIElementAttribute as CFString,
            &focusedValue
        ) == .success,
           let focusedValue {
            let focusedElement = focusedValue as! AXUIElement
            var identifierValue: CFTypeRef?
            if AXUIElementCopyAttributeValue(
                focusedElement,
                kAXIdentifierAttribute as CFString,
                &identifierValue
            ) == .success,
               let identifier = identifierValue as? String,
               !identifier.isEmpty {
                return identifier
            }
        }
        if let root = window.contentView?.superview {
            func fieldEditorOwner(in view: NSView) -> NSView? {
                if let textField = view as? NSTextField,
                   textField.currentEditor() === firstResponder {
                    return textField
                }
                for subview in view.subviews {
                    if let owner = fieldEditorOwner(in: subview) {
                        return owner
                    }
                }
                return nil
            }
            if var candidate = fieldEditorOwner(in: root) {
                while true {
                    let identifier = candidate.accessibilityIdentifier()
                    if !identifier.isEmpty {
                        return identifier
                    }
                    guard let parent = candidate.superview else { break }
                    candidate = parent
                }
            }
        }
        var candidate = firstResponder as? NSView
        while let view = candidate {
            let identifier = view.accessibilityIdentifier()
            if !identifier.isEmpty {
                return identifier
            }
            candidate = view.superview
        }
        return nil
    }

    private static func waitForStableLayout(
        window: NSWindow,
        requestedSize: NSSize?,
        attempts: Int = 80
    ) async -> Bool {
        var previousFrame: NSRect?
        var stableSamples = 0
        for _ in 0..<attempts {
            window.layoutIfNeeded()
            window.displayIfNeeded()
            let frame = window.frame
            let requestedSizeMatches = requestedSize.map {
                abs(frame.width - $0.width) <= 0.5
                    && abs(frame.height - $0.height) <= 0.5
            } ?? true
            if requestedSizeMatches, previousFrame == frame {
                stableSamples += 1
                if stableSamples >= 3 {
                    return true
                }
            } else {
                stableSamples = 0
            }
            previousFrame = frame
            try? await Task.sleep(nanoseconds: 100_000_000)
            if Task.isCancelled { return false }
        }
        return false
    }

    private static func terminateAfterStartupCaptureIfRequested(
        _ shouldExit: Bool,
        model: AppModel
    ) {
        guard shouldExit else { return }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 150_000_000)
            // Presentation fixtures can truthfully exercise the
            // running/stopping UI without owning a child process.
            if model.isPresentationFixture {
                model.job.releasePresentationFixtureOwnershipForTermination()
            }
            // Run the same ownership-safe unwind used by the application
            // delegate before ending this dual-gated, inert Debug process.
            // Calling AppKit termination here can wait indefinitely for a
            // termination reply when the executable was launched directly
            // rather than by LaunchServices, so the fixture exits explicitly
            // only after every app-owned task and child has been awaited.
            await model.stopForApplicationTermination()
            Darwin.exit(EXIT_SUCCESS)
        }
    }

    private static func handle(command: String, model: AppModel) {
        let parts = command.split(separator: "|", maxSplits: 1).map(String.init)
        let verb = parts.first ?? ""
        let argument = parts.count > 1 ? parts[1] : ""
        if model.isPresentationFixture {
            let readOnlyFixtureVerbs: Set<String> = [
                "select",
                "appearance",
                "snapshot",
                "snapshotview",
                "dump",
                "windowsize",
            ]
            guard readOnlyFixtureVerbs.contains(verb) else { return }
        }
        switch verb {
        case "select":
            guard let section = parseSection(
                argument,
                availableSectionIDs: Set(model.catalog.sections.map(\.id))
            ) else { return }
            model.selectedSection = section
        case "appearance":
            guard let appearance = parseAppearance(argument) else { return }
            NSApp.appearance = NSAppearance(named: appearance.appKitName)
        case "input":
            _ = model.selectInput(URL(fileURLWithPath: argument))
        case "outputroot":
            _ = model.selectOutputRoot(
                URL(fileURLWithPath: argument, isDirectory: true)
            )
        case "preset":
            _ = model.config.selectPreset(argument)
        case "override":
            applyOverride(argument, model: model)
        case "clearoverrides":
            model.config.clearAllOverrides()
        case "debugreport":
            _ = model.setWriteDebugReport(argument == "true")
        case "start":
            model.startJob()
        case "cancel":
            model.job.cancel()
        case "snapshot":
            Task { @MainActor in
                _ = await snapshot(
                    to: argument,
                    role: .main,
                    nonce: UUID(),
                    model: model
                )
            }
        case "dump":
            dumpViewTree(to: argument)
        case "snapshotview":
            let pieces = argument.split(separator: "|", maxSplits: 1).map(String.init)
            if pieces.count == 2 {
                snapshotView(classContaining: pieces[0], to: pieces[1])
            }
        case "windowsize":
            if let size = parseWindowSize(argument),
               let window = NSApp.keyWindow ?? NSApp.windows.first(where: \.isVisible) {
                setWindowFrameSize(size, window: window)
                window.center()
            }
        default:
            break
        }
    }

    @discardableResult
    private static func snapshotView(classContaining name: String, to path: String) -> Bool {
        guard let window = window(for: .main),
              let root = window.contentView?.superview,
              let destination = authorizedDestination(path) else { return false }
        func find(_ view: NSView) -> NSView? {
            if String(describing: type(of: view)).localizedCaseInsensitiveContains(name) {
                return view
            }
            for subview in view.subviews {
                if let match = find(subview) { return match }
            }
            return nil
        }
        guard let target = find(root),
              let rep = target.bitmapImageRepForCachingDisplay(in: target.bounds)
        else { return false }
        target.cacheDisplay(in: target.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else {
            return false
        }
        return writeNewAtomically(png, to: destination)
    }

    @discardableResult
    private static func dumpViewTree(to path: String) -> Bool {
        guard let window = window(for: .main),
              let root = window.contentView?.superview,
              let destination = authorizedDestination(path) else { return false }
        var lines: [String] = []
        func walk(_ view: NSView, depth: Int) {
            let indent = String(repeating: "  ", count: depth)
            var line = "\(indent)\(type(of: view)) frame=\(view.frame)"
            if view.isHidden { line += " HIDDEN" }
            if let table = view as? NSTableView {
                line += " rows=\(table.numberOfRows)"
            }
            if view is NSTextField {
                line += " text=<redacted>"
            }
            lines.append(line)
            for subview in view.subviews {
                walk(subview, depth: depth + 1)
            }
        }
        walk(root, depth: 0)
        guard let data = lines.joined(separator: "\n").data(using: .utf8) else {
            return false
        }
        return writeNewAtomically(data, to: destination)
    }

    static func parseSection(
        _ id: String,
        availableSectionIDs: Set<String>
    ) -> WorkspaceSection? {
        switch id {
        case "newJob": return .newJob
        case "allParameters": return .allParameters
        case "readiness": return .readiness
        case "processing": return .processing
        case "results": return .results
        default:
            guard availableSectionIDs.contains(id) else { return nil }
            return .configuration(id)
        }
    }

    private static func applyOverride(_ argument: String, model: AppModel) {
        let pieces = argument.split(separator: "=", maxSplits: 1).map(String.init)
        guard pieces.count == 2,
              let data = pieces[1].data(using: .utf8),
              let value = try? JSONValue.decode(from: data) else { return }
        model.config.setOverride(pieces[0], to: value)
    }

    @discardableResult
    private static func snapshot(
        to path: String,
        role: SnapshotWindowRole,
        nonce: UUID,
        model: AppModel
    ) async -> Bool {
        guard let window = window(for: role),
              let destination = authorizedDestination(path)
        else { return false }
        window.layoutIfNeeded()
        window.displayIfNeeded()
        let scale = max(1, window.backingScaleFactor)
        let expectedPixelSize = CGSize(
            width: (window.frame.width * scale).rounded(),
            height: (window.frame.height * scale).rounded()
        )

        if #available(macOS 14.4, *),
           let image = await captureOwnWindow(window),
           isVisuallyValid(image, expectedPixelSize: expectedPixelSize),
           let png = NSBitmapImageRep(cgImage: image)
            .representation(using: .png, properties: [:]),
           publishCapture(
               png,
               image: image,
               to: destination,
               renderer: "ScreenCaptureKit.currentProcess",
               role: role,
               nonce: nonce,
               window: window,
               model: model
           ) {
            return true
        }

        guard let frameView = window.contentView?.superview,
              let rep = frameView.bitmapImageRepForCachingDisplay(in: frameView.bounds)
        else { return false }
        // cacheDisplay cannot faithfully composite every backdrop. This
        // fallback intentionally flattens materials and records that reduced
        // fidelity in the capture receipt.
        var effectViews: [(NSVisualEffectView, NSVisualEffectView.Material)] = []
        var hiddenViews: [NSView] = []
        func collect(_ view: NSView) {
            if let effect = view as? NSVisualEffectView {
                effectViews.append((effect, effect.material))
                effect.material = .windowBackground
            } else if String(describing: type(of: view)).localizedCaseInsensitiveContains(
                "backdrop"
            ), !view.isHidden {
                view.isHidden = true
                hiddenViews.append(view)
            }
            view.subviews.forEach(collect)
        }
        collect(frameView)
        defer {
            for (effect, material) in effectViews {
                effect.material = material
            }
            for view in hiddenViews {
                view.isHidden = false
            }
        }
        frameView.layoutSubtreeIfNeeded()
        frameView.displayIfNeeded()
        frameView.cacheDisplay(in: frameView.bounds, to: rep)
        guard let image = rep.cgImage,
              isVisuallyValid(image, expectedPixelSize: expectedPixelSize),
              let png = rep.representation(using: .png, properties: [:])
        else { return false }
        return publishCapture(
            png,
            image: image,
            to: destination,
            renderer: "AppKit.cacheDisplay.flattened-material-layout-only",
            role: role,
            nonce: nonce,
            window: window,
            model: model
        )
    }

    static func isVisuallyValid(
        _ image: CGImage,
        expectedPixelSize: CGSize? = nil
    ) -> Bool {
        guard image.width > 0, image.height > 0 else { return false }
        if let expectedPixelSize {
            guard abs(CGFloat(image.width) - expectedPixelSize.width) <= 8,
                  abs(CGFloat(image.height) - expectedPixelSize.height) <= 8
            else { return false }
        }

        let width = min(128, image.width)
        let height = min(128, image.height)
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let rendered = pixels.withUnsafeMutableBytes { bytes -> Bool in
            guard let context = CGContext(
                data: bytes.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                    | CGBitmapInfo.byteOrder32Big.rawValue
            ) else { return false }
            context.interpolationQuality = .low
            context.draw(
                image,
                in: CGRect(x: 0, y: 0, width: width, height: height)
            )
            return true
        }
        guard rendered else { return false }

        var nontransparent = 0
        var minX = width
        var minY = height
        var maxX = -1
        var maxY = -1
        var luminanceSum = 0.0
        var luminanceSquaredSum = 0.0
        var bins: Set<Int> = []
        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * 4
                let red = Int(pixels[offset])
                let green = Int(pixels[offset + 1])
                let blue = Int(pixels[offset + 2])
                let alpha = Int(pixels[offset + 3])
                guard alpha >= 8 else { continue }
                nontransparent += 1
                minX = min(minX, x)
                minY = min(minY, y)
                maxX = max(maxX, x)
                maxY = max(maxY, y)
                let luminance =
                    0.2126 * Double(red)
                    + 0.7152 * Double(green)
                    + 0.0722 * Double(blue)
                luminanceSum += luminance
                luminanceSquaredSum += luminance * luminance
                bins.insert(((red >> 5) << 6) | ((green >> 5) << 3) | (blue >> 5))
            }
        }

        let total = width * height
        guard nontransparent >= Int(Double(total) * 0.80),
              maxX >= minX,
              maxY >= minY,
              Double(maxX - minX + 1) / Double(width) >= 0.90,
              Double(maxY - minY + 1) / Double(height) >= 0.90,
              bins.count >= 6
        else { return false }
        let count = Double(nontransparent)
        let mean = luminanceSum / count
        let variance = max(0, luminanceSquaredSum / count - mean * mean)
        return sqrt(variance) >= 2.0
    }

    private static func publishCapture(
        _ png: Data,
        image: CGImage,
        to destination: URL,
        renderer: String,
        role: SnapshotWindowRole,
        nonce: UUID,
        window: NSWindow,
        model: AppModel
    ) -> Bool {
        let receiptDestination = destination
            .deletingPathExtension()
            .appendingPathExtension("capture.json")
        guard let executableURL = Bundle.main.executableURL,
              let executableData = try? Data(
                  contentsOf: executableURL,
                  options: .mappedIfSafe
              ),
              !executableData.isEmpty,
              !FileManager.default.fileExists(atPath: receiptDestination.path),
              writeNewAtomically(png, to: destination)
        else { return false }

        let environment = ProcessInfo.processInfo.environment
        let workspace = workspaceSectionIdentifier(model.selectedSection)
        let workspaceState = NSWorkspace.shared
        let accessibilityOverrides =
            PresentationAccessibilityOverrides.authorized(
                environment: environment
            )
        let systemReduceMotion =
            workspaceState.accessibilityDisplayShouldReduceMotion
        let systemReduceTransparency =
            workspaceState.accessibilityDisplayShouldReduceTransparency
        let systemIncreaseContrast =
            workspaceState.accessibilityDisplayShouldIncreaseContrast
        let systemDifferentiateWithoutColor =
            workspaceState.accessibilityDisplayShouldDifferentiateWithoutColor
        let receipt = SnapshotReceipt(
            schemaVersion: "auralign.snapshot.v3",
            evidenceClass: "synthetic_fixture",
            nonce: nonce.uuidString.lowercased(),
            capturedAt: ISO8601DateFormatter().string(from: Date()),
            processID: ProcessInfo.processInfo.processIdentifier,
            windowRole: role.rawValue,
            requestedWindowActivity:
                environment["AURALIGN_SNAPSHOT_WINDOW_ACTIVITY"] ?? "active",
            requestedAppearance: environment["AURALIGN_SNAPSHOT_APPEARANCE"],
            requestedWindowSize: environment["AURALIGN_SNAPSHOT_WINDOW_SIZE"],
            requestedWorkspaceSection: environment["AURALIGN_SNAPSHOT_SECTION"],
            selectedWorkspaceSection: workspace,
            requestedSidebarVisibility:
                environment["AURALIGN_SNAPSHOT_SIDEBAR"] ?? "expanded",
            effectiveSidebarVisibility: effectiveSidebarVisibility,
            doubleLocalizedStrings:
                UserDefaults.standard.bool(forKey: "NSDoubleLocalizedStrings"),
            requestedFieldNavigationID: model.fieldNavigationRequest?.id,
            requestedFieldKey: model.fieldNavigationRequest?.fieldKey,
            consumedFieldNavigationID:
                model.consumedFieldNavigationRequestID,
            expectedFocusedControlIdentifier: model.fieldNavigationRequest.map {
                AccessibilityIdentifier.fieldControl($0.fieldKey)
            },
            keyboardFocusedAccessibilityIdentifier:
                keyboardFocusedAccessibilityIdentifier(
                    in: window,
                    model: model
                ),
            keyboardFocusRecoveryCompleted:
                navigationRecoveryIsComplete(model: model, window: window),
            isKeyWindow: window.isKeyWindow,
            isMainWindow: window.isMainWindow,
            isVisible: window.isVisible,
            isMiniaturized: window.isMiniaturized,
            isApplicationActive: NSApp.isActive,
            pointWidth: window.frame.width,
            pointHeight: window.frame.height,
            pixelWidth: image.width,
            pixelHeight: image.height,
            appearance: window.effectiveAppearance.name.rawValue,
            systemReduceMotion: systemReduceMotion,
            systemReduceTransparency: systemReduceTransparency,
            systemIncreaseContrast: systemIncreaseContrast,
            systemDifferentiateWithoutColor: systemDifferentiateWithoutColor,
            presentationAccessibilityOverrideApplied:
                accessibilityOverrides != nil,
            effectiveReduceMotion:
                accessibilityOverrides?.reduceMotion ?? systemReduceMotion,
            effectiveReduceTransparency:
                accessibilityOverrides?.reduceTransparency
                    ?? systemReduceTransparency,
            effectiveIncreaseContrast:
                accessibilityOverrides?.increaseContrast
                    ?? systemIncreaseContrast,
            effectiveDifferentiateWithoutColor:
                accessibilityOverrides?.differentiateWithoutColor
                    ?? systemDifferentiateWithoutColor,
            renderer: renderer,
            fixture: requestedFixtureName(),
            pngSHA256: sha256(png),
            executableSHA256: sha256(executableData)
        )
        guard let receiptData = try? JSONEncoder().encode(receipt),
              writeNewAtomically(receiptData, to: receiptDestination)
        else {
            try? FileManager.default.removeItem(at: destination)
            return false
        }
        return true
    }

    private static func requestedFixtureName() -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(
            of: PresentationFixture.launchArgument
        ), arguments.indices.contains(arguments.index(after: index))
        else { return nil }
        return arguments[arguments.index(after: index)]
    }

    private static func workspaceSectionIdentifier(
        _ section: WorkspaceSection?
    ) -> String? {
        switch section {
        case .newJob: "newJob"
        case .configuration(let id): id
        case .allParameters: "allParameters"
        case .readiness: "readiness"
        case .processing: "processing"
        case .results: "results"
        case .none: nil
        }
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private struct SnapshotReceipt: Codable {
        let schemaVersion: String
        let evidenceClass: String
        let nonce: String
        let capturedAt: String
        let processID: Int32
        let windowRole: String
        let requestedWindowActivity: String
        let requestedAppearance: String?
        let requestedWindowSize: String?
        let requestedWorkspaceSection: String?
        let selectedWorkspaceSection: String?
        let requestedSidebarVisibility: String
        let effectiveSidebarVisibility: String
        let doubleLocalizedStrings: Bool
        let requestedFieldNavigationID: Int?
        let requestedFieldKey: String?
        let consumedFieldNavigationID: Int?
        let expectedFocusedControlIdentifier: String?
        let keyboardFocusedAccessibilityIdentifier: String?
        let keyboardFocusRecoveryCompleted: Bool
        let isKeyWindow: Bool
        let isMainWindow: Bool
        let isVisible: Bool
        let isMiniaturized: Bool
        let isApplicationActive: Bool
        let pointWidth: Double
        let pointHeight: Double
        let pixelWidth: Int
        let pixelHeight: Int
        let appearance: String
        let systemReduceMotion: Bool
        let systemReduceTransparency: Bool
        let systemIncreaseContrast: Bool
        let systemDifferentiateWithoutColor: Bool
        let presentationAccessibilityOverrideApplied: Bool
        let effectiveReduceMotion: Bool
        let effectiveReduceTransparency: Bool
        let effectiveIncreaseContrast: Bool
        let effectiveDifferentiateWithoutColor: Bool
        let renderer: String
        let fixture: String?
        let pngSHA256: String
        let executableSHA256: String
    }

    @available(macOS 14.4, *)
    private static func captureOwnWindow(_ window: NSWindow) async -> CGImage? {
        do {
            // This current-process-only API returns redacted shareable content
            // without asking for global Screen Recording consent.
            let content = try await SCShareableContent.currentProcess
            guard let shareableWindow = content.windows.first(where: {
                $0.windowID == CGWindowID(window.windowNumber)
            }) else { return nil }
            let filter = SCContentFilter(desktopIndependentWindow: shareableWindow)
            let configuration = SCStreamConfiguration()
            let scale = max(1, window.backingScaleFactor)
            configuration.width = max(
                1,
                Int((shareableWindow.frame.width * scale).rounded())
            )
            configuration.height = max(
                1,
                Int((shareableWindow.frame.height * scale).rounded())
            )
            configuration.captureResolution = .best
            configuration.showsCursor = false
            configuration.capturesAudio = false
            return try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
        } catch {
            return nil
        }
    }

    private static func authorizedDestination(_ path: String) -> URL? {
        guard let outputRoot else { return nil }
        return authorizeDestination(path, under: outputRoot)
    }

    static func configuredOutputRoot(
        environment: [String: String],
        fileManager: FileManager = .default
    ) -> URL? {
        guard environment[PresentationFixture.enableEnvironmentKey] == "1",
              environment["AURALIGN_ENABLE_DEBUG_AUTOMATION"] == "1",
              let configuredRoot = environment["AURALIGN_SNAPSHOT_DIR"],
              !configuredRoot.isEmpty,
              (configuredRoot as NSString).isAbsolutePath else { return nil }
        let root = URL(fileURLWithPath: configuredRoot, isDirectory: true)
            .standardizedFileURL
            .resolvingSymlinksInPath()
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: root.path, isDirectory: &isDirectory),
              isDirectory.boolValue else { return nil }
        return root
    }

    static func shouldAutoOpenSettings(
        environment: [String: String],
        fileManager: FileManager = .default
    ) -> Bool {
        guard environment[PresentationFixture.enableEnvironmentKey] == "1",
              environment["AURALIGN_ENABLE_DEBUG_AUTOMATION"] == "1",
              environment["AURALIGN_SNAPSHOT_WINDOW_ROLE"]
                == SnapshotWindowRole.settings.rawValue,
              let nonce = environment["AURALIGN_SNAPSHOT_NONCE"],
              UUID(uuidString: nonce) != nil,
              let destination = environment["AURALIGN_SNAPSHOT_ON_LAUNCH"],
              let root = configuredOutputRoot(
                  environment: environment,
                  fileManager: fileManager
              )
        else { return false }
        return authorizeDestination(
            destination,
            under: root,
            fileManager: fileManager
        ) != nil
    }

    static func parseAppearance(_ rawValue: String) -> SnapshotAppearance? {
        switch rawValue {
        case "light": .light
        case "dark": .dark
        default: nil
        }
    }

    static func parseWindowSize(_ rawValue: String) -> NSSize? {
        let components = rawValue.split(
            separator: ",",
            omittingEmptySubsequences: false
        )
        guard components.count == 2,
              let width = Double(components[0]),
              let height = Double(components[1]),
              width.isFinite,
              height.isFinite,
              width > 0,
              height > 0 else { return nil }
        return NSSize(width: width, height: height)
    }

    static func parseWindowActivity(
        _ rawValue: String
    ) -> SnapshotWindowActivity? {
        SnapshotWindowActivity(rawValue: rawValue)
    }

    static func parseSidebarVisibility(
        _ rawValue: String
    ) -> SnapshotSidebarVisibility? {
        SnapshotSidebarVisibility(rawValue: rawValue)
    }

    static func authorizedInitialColumnVisibility(
        environment: [String: String]
    ) -> NavigationSplitViewVisibility {
        guard environment[PresentationFixture.enableEnvironmentKey] == "1",
              environment["AURALIGN_ENABLE_DEBUG_AUTOMATION"] == "1"
        else { return .all }
        guard let raw = environment["AURALIGN_SNAPSHOT_SIDEBAR"] else {
            return .all
        }
        return parseSidebarVisibility(raw)?.navigationVisibility ?? .all
    }

    static func recordEffectiveSidebarVisibility(
        _ visibility: NavigationSplitViewVisibility
    ) {
        effectiveSidebarVisibility = visibility == .detailOnly
            ? "collapsed"
            : "expanded"
    }

    static func shouldExitAfterCapture(
        requested: Bool,
        captureSucceeded: Bool
    ) -> Bool {
        requested && captureSucceeded
    }

    private static func window(for role: SnapshotWindowRole) -> NSWindow? {
        NSApp.windows.first {
            $0.identifier?.rawValue == role.windowIdentifier && $0.isVisible
        }
    }

    private static func setWindowFrameSize(_ size: NSSize, window: NSWindow) {
        window.setFrame(
            NSRect(origin: window.frame.origin, size: size),
            display: true
        )
    }

    static func writeAtomically(_ data: Data, to destination: URL) -> Bool {
        do {
            try data.write(to: destination, options: .atomic)
            return true
        } catch {
            return false
        }
    }

    static func writeNewAtomically(_ data: Data, to destination: URL) -> Bool {
        let temporary = destination
            .deletingLastPathComponent()
            .appendingPathComponent(
                ".\(destination.lastPathComponent).\(UUID().uuidString).tmp"
            )
        defer {
            try? FileManager.default.removeItem(at: temporary)
        }
        do {
            try data.write(
                to: temporary,
                options: .withoutOverwriting
            )
            // A same-directory hard link publishes the fully written inode in
            // one operation and fails with EEXIST if another capture already
            // owns the evidence path. Removing the private temporary link does
            // not affect the published destination.
            try FileManager.default.linkItem(
                at: temporary,
                to: destination
            )
            return true
        } catch {
            return false
        }
    }

    static func authorizeDestination(
        _ path: String,
        under outputRoot: URL,
        fileManager: FileManager = .default
    ) -> URL? {
        guard !path.isEmpty, (path as NSString).isAbsolutePath else { return nil }
        let root = outputRoot
            .standardizedFileURL
            .resolvingSymlinksInPath()
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: root.path, isDirectory: &isDirectory),
              isDirectory.boolValue else { return nil }
        let proposedDestination = URL(fileURLWithPath: path).standardizedFileURL
        let resolvedParent = proposedDestination
            .deletingLastPathComponent()
            .resolvingSymlinksInPath()
        let destination = resolvedParent
            .appendingPathComponent(proposedDestination.lastPathComponent)
            .standardizedFileURL
        if fileManager.fileExists(atPath: destination.path) {
            return nil
        }
        guard destination != root,
              isSameOrDescendant(destination, of: root),
              isSameOrDescendant(resolvedParent, of: root)
        else { return nil }
        return destination
    }

    private static func isSameOrDescendant(_ candidate: URL, of root: URL) -> Bool {
        let rootComponents = root.pathComponents
        let candidateComponents = candidate.pathComponents
        guard candidateComponents.count >= rootComponents.count else { return false }
        return zip(rootComponents, candidateComponents).allSatisfy {
            $0.0 == $0.1
        }
    }
}
#endif
