// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

enum ModelPackPhase: Equatable, Sendable {
    case notInstalled
    case queued
    case preparing
    case downloading
    case paused
    case verifying
    case publishing
    case installed
    case repairRequired(String)
    case failed(String)

    var label: String {
        switch self {
        case .notInstalled: return "Install required"
        case .queued: return "Queued"
        case .preparing: return "Preparing"
        case .downloading: return "Downloading"
        case .paused: return "Paused"
        case .verifying: return "Verifying"
        case .publishing: return "Finishing"
        case .installed: return "Installed"
        case .repairRequired: return "Repair required"
        case .failed: return "Download failed"
        }
    }

    var symbol: String {
        switch self {
        case .notInstalled: return "arrow.down.circle"
        case .queued: return "clock"
        case .preparing: return "tray"
        case .downloading: return "arrow.down.circle.fill"
        case .paused: return "pause.circle.fill"
        case .verifying: return "checkmark.shield"
        case .publishing: return "shippingbox"
        case .installed: return "checkmark.circle.fill"
        case .repairRequired: return "wrench.and.screwdriver"
        case .failed: return "exclamationmark.triangle.fill"
        }
    }

    var isActive: Bool {
        switch self {
        case .queued, .preparing, .downloading, .verifying, .publishing:
            return true
        case .notInstalled, .paused, .installed, .repairRequired, .failed:
            return false
        }
    }
}

struct ModelPackSnapshot: Identifiable, Equatable, Sendable {
    var id: String
    var version: String
    var displayName: String
    var description: String
    var accessibilityLabel: String
    var modelKeys: [String]
    var requiredForOfflineSetup: Bool
    var requiredForPresets: [String]
    var quality: String
    var speed: String
    var memory: String
    var phase: ModelPackPhase
    var completedBytes: Int64
    var totalBytes: Int64
    var sourceURLs: [String]
    var licenseExpressions: [String]

    var fractionComplete: Double {
        guard totalBytes > 0 else { return 0 }
        return min(1, max(0, Double(completedBytes) / Double(totalBytes)))
    }

    var progressDescription: String {
        "\(ByteCountFormatter.string(fromByteCount: completedBytes, countStyle: .file)) "
            + "of "
            + ByteCountFormatter.string(fromByteCount: totalBytes, countStyle: .file)
    }
}

@MainActor
final class ModelSetupController: ObservableObject {
    @Published private(set) var packs: [ModelPackSnapshot]
    @Published private(set) var catalogProblem: String?
    @Published private(set) var isRestoring = false

    let catalog: ModelDeliveryCatalog?
    var onAcceptedPackChange: (() -> Void)?

    private let service: ModelDownloadService
    private var queue: [String] = []
    private var queueTask: Task<Void, Never>?
    private var lastAnnouncedProgress: [String: Int] = [:]

    init(
        catalog: ModelDeliveryCatalog?,
        catalogProblem: String? = nil,
        service: ModelDownloadService = ModelDownloadService()
    ) {
        self.catalog = catalog
        self.catalogProblem = catalogProblem
        self.service = service
        packs = catalog?.packs.map(Self.initialSnapshot) ?? []
    }

    var hasDownloadablePacks: Bool { !packs.isEmpty }

    var requiredPacks: [ModelPackSnapshot] {
        packs.filter(\.requiredForOfflineSetup)
    }

    var optionalPacks: [ModelPackSnapshot] {
        packs.filter { !$0.requiredForOfflineSetup }
    }

    var needsOfflineSetup: Bool {
        requiredPacks.contains { $0.phase != .installed }
    }

    var hasActiveDownloads: Bool {
        packs.contains { $0.phase.isActive }
    }

    var totalRequiredBytes: Int64 {
        requiredPacks.reduce(0) { $0 + $1.totalBytes }
    }

    var installedRequiredBytes: Int64 {
        requiredPacks.reduce(0) {
            $0 + ($1.phase == .installed ? $1.totalBytes : $1.completedBytes)
        }
    }

    func state(for entry: ModelManifestEntry) -> ModelPackPhase? {
        guard let catalog,
              let pack = catalog.pack(for: entry)
        else {
            return nil
        }
        return packs.first(where: { $0.id == pack.id })?.phase
    }

    func installationLabel(for entry: ModelManifestEntry) -> String {
        guard entry.delivery == "native-download" else {
            return entry.policyLabel
        }
        return state(for: entry)?.label ?? "Delivery unavailable"
    }

    func displayName(for entry: ModelManifestEntry) -> String {
        deliveryModel(for: entry)?.displayName ?? entry.productDisplayName
    }

    func accessibilityLabel(for entry: ModelManifestEntry) -> String {
        deliveryModel(for: entry)?.accessibilityLabel
            ?? displayName(for: entry)
    }

    func productSummary(for entry: ModelManifestEntry) -> String {
        guard let delivery = deliveryModel(for: entry) else {
            return entry.productSummary
        }
        return [
            delivery.displayName + ".",
            delivery.description,
            "Quality: \(delivery.quality).",
            "Speed: \(delivery.speed).",
            "Memory: \(delivery.memory).",
            "Status: \(installationLabel(for: entry)).",
        ].joined(separator: " ")
    }

    private func deliveryModel(
        for entry: ModelManifestEntry
    ) -> ModelDeliveryModel? {
        catalog?.models.first {
            $0.role == entry.role && $0.key == entry.key
        }
    }

    private func displayName(forPackID identifier: String) -> String {
        packs.first(where: { $0.id == identifier })?.displayName
            ?? "Model pack"
    }

    func restore() {
        guard !isRestoring, let catalog else { return }
        isRestoring = true
        Task {
            let states = await service.restore(catalog: catalog)
            for pack in catalog.packs {
                let result = states[pack.id]
                update(pack.id) { snapshot in
                    if result?.isReady == true {
                        snapshot.phase = .installed
                        snapshot.completedBytes = snapshot.totalBytes
                    } else if result?.problem == "not-installed" {
                        snapshot.phase = .notInstalled
                        snapshot.completedBytes = 0
                    } else {
                        snapshot.phase = .repairRequired(
                            result?.problem ?? "The installed pack could not be verified."
                        )
                    }
                }
            }
            isRestoring = false
        }
    }

    func installRequired() {
        enqueue(
            requiredPacks.filter { $0.phase != .installed }.map(\.id)
        )
    }

    func install(packID: String) {
        enqueue([packID])
    }

    func pause(packID: String) {
        if let index = queue.firstIndex(of: packID) {
            queue.remove(at: index)
            update(packID) { $0.phase = .paused }
            announce(
                "\(displayName(forPackID: packID)) download paused.",
                token: "\(packID)-paused"
            )
            return
        }
        Task { await service.pause(packID: packID) }
    }

    func resume(packID: String) {
        enqueue([packID], atFront: true)
    }

    func cancel(packID: String) {
        if let index = queue.firstIndex(of: packID) {
            queue.remove(at: index)
            update(packID) {
                $0.phase = .notInstalled
                $0.completedBytes = 0
            }
            return
        }
        Task { await service.cancel(packID: packID) }
    }

    func retry(packID: String) {
        enqueue([packID], atFront: true)
    }

    func pauseActiveDownloads() {
        for pack in packs where pack.phase.isActive {
            pause(packID: pack.id)
        }
    }

    private func enqueue(_ identifiers: [String], atFront: Bool = false) {
        guard catalogProblem == nil else { return }
        let valid = identifiers.filter { identifier in
            packs.contains {
                $0.id == identifier
                    && $0.phase != .installed
                    && !$0.phase.isActive
            }
        }
        for identifier in valid where !queue.contains(identifier) {
            if atFront {
                queue.insert(identifier, at: 0)
            } else {
                queue.append(identifier)
            }
            update(identifier) { $0.phase = .queued }
        }
        startQueueIfNeeded()
    }

    private func startQueueIfNeeded() {
        guard queueTask == nil, !queue.isEmpty else { return }
        queueTask = Task { [weak self] in
            guard let self else { return }
            while !self.queue.isEmpty, !Task.isCancelled {
                let identifier = self.queue.removeFirst()
                await self.runPack(identifier)
            }
            self.queueTask = nil
            if !self.queue.isEmpty {
                self.startQueueIfNeeded()
            }
        }
    }

    private func runPack(_ identifier: String) async {
        guard let catalog,
              let pack = catalog.packs.first(where: { $0.id == identifier })
        else {
            update(identifier) {
                $0.phase = .failed(
                    "This model pack is absent from the signed delivery catalog."
                )
            }
            return
        }

        do {
            try await service.install(
                pack: pack,
                catalog: catalog,
                onEvent: { [weak self] event in
                    Task { @MainActor in
                        self?.accept(event, for: pack)
                    }
                }
            )
        } catch let error as ModelTransferError {
            switch error {
            case .paused:
                update(identifier) { $0.phase = .paused }
            case .cancelled:
                update(identifier) {
                    $0.phase = .notInstalled
                    $0.completedBytes = 0
                }
            case .invalidResponse, .rejectedRedirect, .transport:
                update(identifier) {
                    $0.phase = .failed(
                        error.errorDescription ?? "The model download failed."
                    )
                }
            }
        } catch {
            update(identifier) {
                $0.phase = .failed(error.localizedDescription)
            }
        }
    }

    private func accept(
        _ event: ModelDownloadEvent,
        for pack: ModelDeliveryPack
    ) {
        switch event {
        case .preparing:
            update(pack.id) { $0.phase = .preparing }
            announce(
                "\(pack.displayName) setup started.",
                token: "\(pack.id)-preparing"
            )
        case .downloading(let progress):
            update(pack.id) {
                $0.phase = .downloading
                $0.completedBytes = progress.completedBytes
            }
            let percent = progress.totalBytes > 0
                ? Int((Double(progress.completedBytes) / Double(progress.totalBytes) * 100)
                    .rounded(.down))
                : 0
            let boundary = max(0, min(100, (percent / 5) * 5))
            if lastAnnouncedProgress[pack.id] != boundary {
                lastAnnouncedProgress[pack.id] = boundary
                announce(
                    "\(pack.displayName), \(boundary) percent downloaded.",
                    token: "\(pack.id)-progress-\(boundary)"
                )
            }
        case .paused(let progress):
            update(pack.id) {
                $0.phase = .paused
                $0.completedBytes = progress.completedBytes
            }
            announce(
                "\(pack.displayName) download paused.",
                token: "\(pack.id)-paused"
            )
        case .verifying:
            update(pack.id) { $0.phase = .verifying }
            announce(
                "Verifying \(pack.displayName).",
                token: "\(pack.id)-verifying"
            )
        case .publishing:
            update(pack.id) { $0.phase = .publishing }
        case .accepted:
            update(pack.id) {
                $0.phase = .installed
                $0.completedBytes = $0.totalBytes
            }
            announce(
                "\(pack.displayName) installed and verified.",
                token: "\(pack.id)-installed"
            )
            onAcceptedPackChange?()
        }
    }

    private func announce(_ message: String, token: String) {
        AccessibilityAnnouncementCenter.shared.announce(
            message,
            token: "model-setup-\(token)"
        )
    }

    private func update(
        _ identifier: String,
        mutation: (inout ModelPackSnapshot) -> Void
    ) {
        guard let index = packs.firstIndex(where: { $0.id == identifier }) else {
            return
        }
        mutation(&packs[index])
    }

    private static func initialSnapshot(
        _ pack: ModelDeliveryPack
    ) -> ModelPackSnapshot {
        ModelPackSnapshot(
            id: pack.id,
            version: pack.version,
            displayName: pack.displayName,
            description: pack.description,
            accessibilityLabel: pack.accessibilityLabel,
            modelKeys: pack.modelKeys,
            requiredForOfflineSetup: pack.requiredForOfflineSetup,
            requiredForPresets: pack.requiredForPresets,
            quality: pack.quality,
            speed: pack.speed,
            memory: pack.memory,
            phase: .notInstalled,
            completedBytes: 0,
            totalBytes: pack.totalBytes,
            sourceURLs: pack.sourceURLs,
            licenseExpressions: pack.licenseExpressions
        )
    }
}
