// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit
import Darwin
import SwiftUI

/// Process-race-safe ownership lock for the user-facing application. The
/// LaunchServices plist declaration handles ordinary launches; this lock also
/// covers direct executable and debugger launches that bypass LaunchServices.
final class ApplicationInstanceLock {
    private static let registryLock = NSLock()
    private static var heldPaths: Set<String> = []

    private let lockURL: URL
    private var descriptor: Int32 = -1
    private var registeredPath: String?
    private var contendingOwnerPID: pid_t?

    init(
        lockURL: URL = ApplicationInstanceLock.standardLockURL()
    ) {
        self.lockURL = lockURL
    }

    static func standardLockURL(
        fileManager: FileManager = .default
    ) -> URL {
        let applicationSupport = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first
            ?? fileManager.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support", isDirectory: true)
        return applicationSupport
            .appendingPathComponent("com.auralign.studio", isDirectory: true)
            .appendingPathComponent("application-instance.lock")
    }

    func acquire() -> Bool {
        guard descriptor == -1 else { return true }
        guard preparePrivateLockDirectory() else { return false }
        let path = lockURL.standardizedFileURL.path
        let claimedInProcess = Self.registryLock.withLock { () -> Bool in
            guard !Self.heldPaths.contains(path) else { return false }
            Self.heldPaths.insert(path)
            return true
        }
        guard claimedInProcess else {
            contendingOwnerPID = ProcessInfo.processInfo.processIdentifier
            return false
        }
        let opened = lockURL.withUnsafeFileSystemRepresentation { path -> Int32 in
            guard let path else { return -1 }
            return Darwin.open(
                path,
                O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW,
                S_IRUSR | S_IWUSR
            )
        }
        guard opened >= 0 else {
            Self.releaseInProcess(path)
            return false
        }
        var metadata = stat()
        guard Darwin.fstat(opened, &metadata) == 0,
              (metadata.st_mode & S_IFMT) == S_IFREG,
              (metadata.st_mode & 0o077) == 0,
              metadata.st_uid == Darwin.geteuid(),
              metadata.st_nlink == 1
        else {
            Darwin.close(opened)
            Self.releaseInProcess(path)
            return false
        }
        var record = Darwin.flock()
        record.l_type = Int16(F_WRLCK)
        record.l_whence = Int16(SEEK_SET)
        guard Darwin.fcntl(opened, F_SETLK, &record) == 0 else {
            var owner = Darwin.flock()
            owner.l_type = Int16(F_WRLCK)
            owner.l_whence = Int16(SEEK_SET)
            if Darwin.fcntl(opened, F_GETLK, &owner) == 0,
               owner.l_type != Int16(F_UNLCK),
               owner.l_pid > 0 {
                contendingOwnerPID = owner.l_pid
            }
            Darwin.close(opened)
            Self.releaseInProcess(path)
            return false
        }
        descriptor = opened
        registeredPath = path
        contendingOwnerPID = nil
        return true
    }

    private func preparePrivateLockDirectory() -> Bool {
        let directory = lockURL.deletingLastPathComponent().standardizedFileURL
        do {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
        } catch {
            return false
        }
        var metadata = stat()
        let inspected = directory.withUnsafeFileSystemRepresentation { path -> Bool in
            guard let path else { return false }
            return Darwin.lstat(path, &metadata) == 0
        }
        return inspected
            && (metadata.st_mode & S_IFMT) == S_IFDIR
            && (metadata.st_mode & 0o077) == 0
            && metadata.st_uid == Darwin.geteuid()
            && metadata.st_nlink >= 2
    }

    /// Returns the exact kernel-reported conflicting lock owner. No PID is
    /// persisted or guessed from another same-bundle application.
    func ownerProcessIdentifier() -> pid_t? {
        descriptor >= 0
            ? ProcessInfo.processInfo.processIdentifier
            : contendingOwnerPID
    }

    deinit {
        guard descriptor >= 0 else { return }
        var record = Darwin.flock()
        record.l_type = Int16(F_UNLCK)
        record.l_whence = Int16(SEEK_SET)
        _ = Darwin.fcntl(descriptor, F_SETLK, &record)
        Darwin.close(descriptor)
        if let registeredPath {
            Self.releaseInProcess(registeredPath)
        }
    }

    private static func releaseInProcess(_ path: String) {
        _ = registryLock.withLock {
            heldPaths.remove(path)
        }
    }

}

private extension NSLock {
    func withLock<T>(_ body: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try body()
    }
}

/// Defers application termination while an owned engine process unwinds.
/// Quitting Auralign therefore cancels the active job and reaps its child tree
/// instead of silently leaving Python or native backend processes behind.
@MainActor
final class AuralignApplicationDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?
    private(set) var ownsApplicationInstance = false
    private var terminationTask: Task<Void, Never>?
    private let instanceLock = ApplicationInstanceLock()

    func attach(model: AppModel) {
        self.model = model
        if ownsApplicationInstance {
            _ = model.activateForApplicationOwnership()
        }
    }

    func applicationWillFinishLaunching(_ notification: Notification) {
        guard instanceLock.acquire() else {
            if let ownerPID = instanceLock.ownerProcessIdentifier(),
               ownerPID != ProcessInfo.processInfo.processIdentifier {
                NSRunningApplication(processIdentifier: ownerPID)?
                    .activate(options: [.activateAllWindows])
            }
            DispatchQueue.main.async {
                NSApp.terminate(nil)
            }
            return
        }
        ownsApplicationInstance = true
        if let model {
            _ = model.activateForApplicationOwnership()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard ownsApplicationInstance, let model else {
            return .terminateNow
        }
        guard terminationTask == nil else {
            return .terminateLater
        }
        terminationTask = Task { [weak self] in
            await model.stopForApplicationTermination()
            sender.reply(toApplicationShouldTerminate: true)
            self?.terminationTask = nil
        }
        return .terminateLater
    }
}

#if DEBUG
enum DebugPresentationMode: Equatable {
    case live
    case fixture(PresentationFixture)
    case invalid

    static func resolve(
        environment: [String: String],
        arguments: [String]
    ) -> DebugPresentationMode {
        let fixtureRequest = PresentationFixture.request(
            environment: environment,
            arguments: arguments
        )
        // Tournament-only selectors were retired after direction selection.
        // Any stale selector remains a malformed synthetic request and must
        // never fall through to a live engine.
        if environment["AURALIGN_PROTOTYPE_DIRECTION"] != nil
            || environment["AURALIGN_PROTOTYPE_SCREEN"] != nil {
            return .invalid
        }

        switch fixtureRequest {
        case .none:
            return .live
        case .invalid:
            return .invalid
        case .fixture(let fixture):
            return .fixture(fixture)
        }
    }
}
#endif

@main
struct AuralignApp: App {
    @NSApplicationDelegateAdaptor(AuralignApplicationDelegate.self)
    private var applicationDelegate
    @StateObject private var model: AppModel
    #if DEBUG
    private let debugPresentationMode: DebugPresentationMode
    #endif

    init() {
        let environment = ProcessInfo.processInfo.environment
        let arguments = ProcessInfo.processInfo.arguments
        #if DEBUG
        let mode = DebugPresentationMode.resolve(
            environment: environment,
            arguments: arguments
        )
        debugPresentationMode = mode
        switch mode {
        case .live:
            _model = StateObject(
                wrappedValue: AppModel()
            )
        case .fixture(let fixture):
            _model = StateObject(
                wrappedValue: AppModel.makePresentationFixture(fixture)
            )
        case .invalid:
            _model = StateObject(
                wrappedValue: AppModel.makeInvalidPresentationFixtureRequest()
            )
        }
        #else
        _model = StateObject(
            wrappedValue: AppModel()
        )
        #endif
    }

    var body: some Scene {
        Window("Auralign", id: "main") {
            mainContent
                .environmentObject(model)
                .modifier(
                    AuralignAccessibilityEnvironmentModifier()
                )
                .background(WindowRoleMarker(role: .main))
                #if DEBUG
                .background(DebugSettingsAutoOpener())
                #endif
                .onAppear {
                    applicationDelegate.attach(model: model)
                    if let window = NSApp.keyWindow
                        ?? NSApp.windows.first(where: \.isVisible) {
                        window.minSize = NSSize(width: 900, height: 600)
                    }
                    #if DEBUG
                    SnapshotDriver.activate(model: model)
                    #endif
                }
        }
        .defaultSize(width: 1180, height: 780)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Transcription") {
                    _ = model.beginNewJob()
                }
                .keyboardShortcut("n", modifiers: .command)
                .disabled(model.workMutationLocked || model.isPresentationFixture)
                Button("Choose Media…") {
                    guard let url = LocalFilePanel.chooseMedia() else { return }
                    _ = model.selectInput(url)
                    model.selectedSection = .newJob
                }
                .keyboardShortcut("o", modifiers: .command)
                .disabled(model.workMutationLocked || model.isPresentationFixture)
            }
            CommandMenu("Job") {
                Button("Start Transcription") { model.startJob() }
                    .keyboardShortcut("r", modifiers: .command)
                    .disabled(model.startBlocker != nil || model.isPresentationFixture)
                Button("Cancel Job") { model.job.cancel() }
                    .keyboardShortcut(".", modifiers: .command)
                    .disabled(!model.job.canRequestCancellation)
                Divider()
                Button("Refresh Readiness") {
                    model.requestReadinessRefresh()
                }
                .keyboardShortcut("k", modifiers: [.command, .shift])
                .disabled(model.workMutationLocked || model.isRunningDoctor)
                Button("Reset All Overrides") {
                    model.config.clearAllOverrides()
                }
                .disabled(
                    model.workMutationLocked
                        || model.isPresentationFixture
                        || model.config.modifiedKeys.isEmpty
                )
                Divider()
                Button("Show Results") {
                    model.selectedSection = .results
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
                .disabled(!model.job.hasResultSurface)
            }
        }
        Settings {
            settingsContent
                .environmentObject(model)
                .modifier(
                    AuralignAccessibilityEnvironmentModifier()
                )
                .background(WindowRoleMarker(role: .settings))
        }
    }

    @ViewBuilder
    private var mainContent: some View {
        #if DEBUG
        switch debugPresentationMode {
        case .live:
            MainView()
        case .fixture(let fixture):
            VStack(spacing: 0) {
                PresentationFixtureBanner(fixture: fixture)
                MainView()
            }
        case .invalid:
            VStack(spacing: 0) {
                InvalidFixtureRequestBanner()
                MainView()
            }
        }
        #else
        MainView()
        #endif
    }

    @ViewBuilder
    private var settingsContent: some View {
        #if DEBUG
        switch debugPresentationMode {
        case .fixture(let fixture):
            VStack(spacing: 0) {
                PresentationFixtureBanner(fixture: fixture)
                SettingsView()
            }
        case .invalid:
            VStack(spacing: 0) {
                InvalidFixtureRequestBanner()
                SettingsView()
            }
        case .live:
            SettingsView()
        }
        #else
        SettingsView()
        #endif
    }
}

private struct WindowRoleMarker: NSViewRepresentable {
    let role: SnapshotWindowRole

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        assignRole(to: view)
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        assignRole(to: nsView)
    }

    private func assignRole(to view: NSView) {
        DispatchQueue.main.async {
            view.window?.identifier = NSUserInterfaceItemIdentifier(role.windowIdentifier)
        }
    }
}

struct AuralignAccessibilityEnvironmentModifier: ViewModifier {
    @State private var displayOptionsRevision = 0
    #if DEBUG
    private let overrides: PresentationAccessibilityOverrides?
    #endif

    init(environment: [String: String] = ProcessInfo.processInfo.environment) {
        #if DEBUG
        overrides = PresentationAccessibilityOverrides.authorized(
            environment: environment
        )
        #endif
    }

    func body(content: Content) -> some View {
        let workspace = {
            // Reading the revision here invalidates the resolved values after
            // macOS posts an accessibility-display-options change.
            _ = displayOptionsRevision
            return NSWorkspace.shared
        }()
        #if DEBUG
        let reduceTransparency = overrides?.reduceTransparency
            ?? workspace.accessibilityDisplayShouldReduceTransparency
        let increaseContrast = overrides?.increaseContrast
            ?? workspace.accessibilityDisplayShouldIncreaseContrast
        let reduceMotion = overrides?.reduceMotion
            ?? workspace.accessibilityDisplayShouldReduceMotion
        let differentiateWithoutColor = overrides?.differentiateWithoutColor
            ?? workspace.accessibilityDisplayShouldDifferentiateWithoutColor
        #else
        let reduceTransparency =
            workspace.accessibilityDisplayShouldReduceTransparency
        let increaseContrast =
            workspace.accessibilityDisplayShouldIncreaseContrast
        let reduceMotion = workspace.accessibilityDisplayShouldReduceMotion
        let differentiateWithoutColor =
            workspace.accessibilityDisplayShouldDifferentiateWithoutColor
        #endif
        #if DEBUG
        // SwiftUI exposes the documented accessibility values as read-only on
        // macOS. Its writable backing environment keys are used only in
        // dual-gated Debug presentation evidence so native controls and
        // Auralign surfaces render from the same requested state. This code is
        // absent from Release; live Debug launches simply re-inject the real
        // system values resolved above.
        content
            .environment(\.auralignReduceTransparency, reduceTransparency)
            .environment(\.auralignIncreaseContrast, increaseContrast)
            .environment(\.auralignReduceMotion, reduceMotion)
            .environment(
                \.auralignDifferentiateWithoutColor,
                differentiateWithoutColor
            )
            .environment(
                \._accessibilityReduceTransparency,
                reduceTransparency
            )
            .environment(
                \._colorSchemeContrast,
                increaseContrast
                    ? ColorSchemeContrast.increased
                    : ColorSchemeContrast.standard
            )
            .environment(\._accessibilityReduceMotion, reduceMotion)
            .environment(
                \._accessibilityDifferentiateWithoutColor,
                differentiateWithoutColor
            )
            .onReceive(
                workspace.notificationCenter.publisher(
                    for: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification
                )
            ) { _ in
                displayOptionsRevision &+= 1
            }
        #else
        content
            .environment(\.auralignReduceTransparency, reduceTransparency)
            .environment(\.auralignIncreaseContrast, increaseContrast)
            .environment(\.auralignReduceMotion, reduceMotion)
            .environment(
                \.auralignDifferentiateWithoutColor,
                differentiateWithoutColor
            )
            .onReceive(
                workspace.notificationCenter.publisher(
                    for: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification
                )
            ) { _ in
                displayOptionsRevision &+= 1
            }
        #endif
    }
}

#if DEBUG
private struct DebugSettingsAutoOpener: View {
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .task {
                let environment = ProcessInfo.processInfo.environment
                guard SnapshotDriver.shouldAutoOpenSettings(
                    environment: environment
                ) else { return }
                await Task.yield()
                openSettings()
            }
    }
}
#endif
