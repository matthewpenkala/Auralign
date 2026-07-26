// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

struct ModelDownloadProgress: Equatable, Sendable {
    var completedBytes: Int64
    var totalBytes: Int64
}

enum ModelDownloadEvent: Sendable {
    case preparing
    case downloading(ModelDownloadProgress)
    case paused(ModelDownloadProgress)
    case verifying
    case publishing
    case accepted(URL)
}

enum ModelTransferError: LocalizedError {
    case paused(Data?)
    case cancelled
    case invalidResponse
    case rejectedRedirect
    case transport(Error, Data?)

    var resumeData: Data? {
        switch self {
        case .paused(let data), .transport(_, let data):
            return data
        case .cancelled, .invalidResponse, .rejectedRedirect:
            return nil
        }
    }

    var errorDescription: String? {
        switch self {
        case .paused:
            return "The model download is paused."
        case .cancelled:
            return "The model download was cancelled."
        case .invalidResponse:
            return "The model server returned an invalid download response."
        case .rejectedRedirect:
            return "The model server redirected outside the signed host policy."
        case .transport(let error, _):
            return "The model download failed: \(error.localizedDescription)"
        }
    }
}

protocol ModelFileDownloading: Sendable {
    func download(
        identifier: String,
        request: URLRequest,
        resumeData: Data?,
        destination: URL,
        allowedRedirectHostSuffixes: [String],
        progress: @escaping @Sendable (ModelDownloadProgress) -> Void
    ) async throws

    func pause(identifier: String)
    func cancel(identifier: String)
}

/// URLSession transport isolated behind a protocol so unit tests can inject a
/// deterministic file transport without weakening production URL policy.
final class URLSessionModelFileTransport: ModelFileDownloading, @unchecked Sendable {
    private let configuration: URLSessionConfiguration
    private let lock = NSLock()
    private var operations: [String: ModelDownloadOperation] = [:]

    init(configuration: URLSessionConfiguration = .ephemeral) {
        let configured = configuration.copy() as? URLSessionConfiguration
            ?? URLSessionConfiguration.ephemeral
        configured.requestCachePolicy = .reloadIgnoringLocalCacheData
        configured.urlCache = nil
        configured.httpCookieStorage = nil
        configured.httpShouldSetCookies = false
        configured.waitsForConnectivity = true
        self.configuration = configured
    }

    func download(
        identifier: String,
        request: URLRequest,
        resumeData: Data?,
        destination: URL,
        allowedRedirectHostSuffixes: [String],
        progress: @escaping @Sendable (ModelDownloadProgress) -> Void
    ) async throws {
        let operation = ModelDownloadOperation(
            configuration: configuration,
            request: request,
            resumeData: resumeData,
            destination: destination,
            allowedRedirectHostSuffixes: allowedRedirectHostSuffixes,
            progress: progress
        )
        lock.withLock {
            precondition(operations[identifier] == nil)
            operations[identifier] = operation
        }
        defer {
            _ = lock.withLock {
                operations.removeValue(forKey: identifier)
            }
        }
        try await operation.value()
    }

    func pause(identifier: String) {
        lock.withLock { operations[identifier] }?.pause()
    }

    func cancel(identifier: String) {
        lock.withLock { operations[identifier] }?.cancel()
    }
}

private final class ModelDownloadOperation:
    NSObject,
    URLSessionDownloadDelegate,
    URLSessionTaskDelegate,
    @unchecked Sendable
{
    private enum RequestedStop {
        case none
        case pause
        case cancel
    }

    private let configuration: URLSessionConfiguration
    private let request: URLRequest
    private let initialResumeData: Data?
    private let destination: URL
    private let allowedRedirectHostSuffixes: [String]
    private let progressHandler: @Sendable (ModelDownloadProgress) -> Void
    private let lock = NSLock()

    private var session: URLSession?
    private var task: URLSessionDownloadTask?
    private var continuation: CheckedContinuation<Void, Error>?
    private var requestedStop = RequestedStop.none
    private var producedResumeData: Data?
    private var responseWasAccepted = false
    private var movedDownload = false
    private var policyError: ModelTransferError?
    private var completed = false

    init(
        configuration: URLSessionConfiguration,
        request: URLRequest,
        resumeData: Data?,
        destination: URL,
        allowedRedirectHostSuffixes: [String],
        progress: @escaping @Sendable (ModelDownloadProgress) -> Void
    ) {
        self.configuration = configuration.copy() as? URLSessionConfiguration
            ?? .ephemeral
        self.request = request
        initialResumeData = resumeData
        self.destination = destination
        self.allowedRedirectHostSuffixes = allowedRedirectHostSuffixes
        progressHandler = progress
    }

    func value() async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation {
                (continuation: CheckedContinuation<Void, Error>) in
                let session = URLSession(
                    configuration: configuration,
                    delegate: self,
                    delegateQueue: nil
                )
                let task: URLSessionDownloadTask
                if let initialResumeData {
                    task = session.downloadTask(withResumeData: initialResumeData)
                } else {
                    task = session.downloadTask(with: request)
                }
                lock.withLock {
                    self.session = session
                    self.task = task
                    self.continuation = continuation
                }
                task.resume()
            }
        } onCancel: {
            self.cancel()
        }
    }

    func pause() {
        let task = lock.withLock { () -> URLSessionDownloadTask? in
            guard !completed else { return nil }
            requestedStop = .pause
            return self.task
        }
        task?.cancel { [weak self] data in
            self?.lock.withLock {
                self?.producedResumeData = data
            }
        }
    }

    func cancel() {
        let task = lock.withLock { () -> URLSessionDownloadTask? in
            guard !completed else { return nil }
            requestedStop = .cancel
            return self.task
        }
        task?.cancel()
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        guard request.url?.scheme?.lowercased() == "https",
              ModelDeliveryCatalog.isAllowedRedirectHost(
                  request.url?.host,
                  suffixes: allowedRedirectHostSuffixes
              )
        else {
            lock.withLock {
                policyError = .rejectedRedirect
            }
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        _ = session
        _ = downloadTask
        _ = bytesWritten
        progressHandler(
            ModelDownloadProgress(
                completedBytes: max(0, totalBytesWritten),
                totalBytes: max(0, totalBytesExpectedToWrite)
            )
        )
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        _ = session
        guard let response = downloadTask.response as? HTTPURLResponse,
              [200, 206].contains(response.statusCode),
              response.url?.scheme?.lowercased() == "https",
              ModelDeliveryCatalog.isAllowedRedirectHost(
                  response.url?.host,
                  suffixes: allowedRedirectHostSuffixes
              )
        else {
            lock.withLock {
                policyError = .invalidResponse
            }
            return
        }
        do {
            let manager = FileManager.default
            try manager.createDirectory(
                at: destination.deletingLastPathComponent(),
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            if manager.fileExists(atPath: destination.path) {
                try manager.removeItem(at: destination)
            }
            try manager.moveItem(at: location, to: destination)
            lock.withLock {
                responseWasAccepted = true
                movedDownload = true
            }
        } catch {
            lock.withLock {
                policyError = .transport(error, nil)
            }
        }
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        _ = task
        let result = lock.withLock { () -> Result<Void, Error> in
            if let policyError {
                return .failure(policyError)
            }
            switch requestedStop {
            case .pause:
                let errorResumeData = (error as NSError?)?.userInfo[
                    NSURLSessionDownloadTaskResumeData
                ] as? Data
                return .failure(
                    ModelTransferError.paused(
                        producedResumeData ?? errorResumeData
                    )
                )
            case .cancel:
                return .failure(ModelTransferError.cancelled)
            case .none:
                if let error {
                    let resumeData = (error as NSError).userInfo[
                        NSURLSessionDownloadTaskResumeData
                    ] as? Data
                    return .failure(
                        ModelTransferError.transport(error, resumeData)
                    )
                }
                return responseWasAccepted && movedDownload
                    ? .success(())
                    : .failure(ModelTransferError.invalidResponse)
            }
        }
        finish(result, session: session)
    }

    private func finish(
        _ result: Result<Void, Error>,
        session: URLSession
    ) {
        let continuation = lock.withLock {
            () -> CheckedContinuation<Void, Error>? in
            guard !completed else { return nil }
            completed = true
            let continuation = self.continuation
            self.continuation = nil
            self.task = nil
            self.session = nil
            return continuation
        }
        session.finishTasksAndInvalidate()
        switch result {
        case .success:
            continuation?.resume()
        case .failure(let error):
            continuation?.resume(throwing: error)
        }
    }
}

/// Serial, resumable acquisition of one independently versioned model pack.
/// Transfer completion is not readiness: this actor only emits `.accepted`
/// after the store has rehashed, sealed, atomically promoted, and reverified
/// the complete pack.
actor ModelDownloadService {
    private let store: ModelPackStore
    private let transport: any ModelFileDownloading
    private var activeFileIdentifierByPack: [String: String] = [:]

    init(
        store: ModelPackStore = ModelPackStore(),
        transport: any ModelFileDownloading = URLSessionModelFileTransport()
    ) {
        self.store = store
        self.transport = transport
    }

    func restore(
        catalog: ModelDeliveryCatalog
    ) async -> [String: ModelPackVerificationResult] {
        var result: [String: ModelPackVerificationResult] = [:]
        for pack in catalog.packs {
            result[pack.id] = await store.verifyInstalledPack(
                pack,
                catalogDigest: catalog.digest
            )
        }
        return result
    }

    func install(
        pack: ModelDeliveryPack,
        catalog: ModelDeliveryCatalog,
        onEvent: @escaping @Sendable (ModelDownloadEvent) -> Void
    ) async throws {
        try catalog.validate()
        guard catalog.packs.contains(pack),
              !catalog.digest.isEmpty
        else {
            throw ServiceError.packNotInSignedCatalog
        }
        onEvent(.preparing)
        let installed = await store.verifyInstalledPack(
            pack,
            catalogDigest: catalog.digest
        )
        if installed.isReady {
            onEvent(
                .accepted(try await store.installedPackURL(pack))
            )
            return
        }
        if installed.problem != "not-installed" {
            try await store.removeInvalidInstalledPack(
                pack,
                catalogDigest: catalog.digest
            )
        }
        _ = try await store.prepareStaging(
            for: pack,
            catalogDigest: catalog.digest
        )
        let alreadyComplete = await completedBytes(
            pack: pack,
            catalogDigest: catalog.digest
        )
        var completedBeforeCurrent = alreadyComplete

        for file in pack.files {
            if await store.stagedFileIsValid(
                file,
                pack: pack,
                catalogDigest: catalog.digest
            ) {
                continue
            }

            let transferIdentifier = "\(pack.id)|\(file.id)"
            activeFileIdentifierByPack[pack.id] = transferIdentifier
            let resumeData = try await store.resumeData(
                for: file,
                pack: pack
            )
            let temporaryURL = try await store.temporaryDownloadURL(
                for: file,
                pack: pack,
                catalogDigest: catalog.digest
            )
            guard let sourceURL = file.sourceURL else {
                throw ServiceError.packNotInSignedCatalog
            }
            var request = URLRequest(url: sourceURL)
            request.httpMethod = "GET"
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 7 * 24 * 60 * 60
            try await writeJournal(
                pack: pack,
                catalog: catalog,
                file: file,
                state: "downloading",
                completedBytes: completedBeforeCurrent
            )
            let completedBeforeFile = completedBeforeCurrent

            do {
                try await transport.download(
                    identifier: transferIdentifier,
                    request: request,
                    resumeData: resumeData,
                    destination: temporaryURL,
                    allowedRedirectHostSuffixes:
                        catalog.redirectHostSuffixes,
                    progress: { progress in
                        onEvent(
                            .downloading(
                                ModelDownloadProgress(
                                    completedBytes: min(
                                        pack.totalBytes,
                                        completedBeforeFile
                                            + progress.completedBytes
                                    ),
                                    totalBytes: pack.totalBytes
                                )
                            )
                        )
                    }
                )
                onEvent(.verifying)
                try await store.acceptDownloadedFile(
                    at: temporaryURL,
                    file: file,
                    pack: pack,
                    catalogDigest: catalog.digest
                )
                try await store.removeResumeData(for: file, pack: pack)
                completedBeforeCurrent += file.expectedSize
            } catch let error as ModelTransferError {
                activeFileIdentifierByPack.removeValue(forKey: pack.id)
                try await store.storeResumeData(
                    error.resumeData,
                    for: file,
                    pack: pack
                )
                switch error {
                case .paused:
                    let progress = ModelDownloadProgress(
                        completedBytes: completedBeforeCurrent,
                        totalBytes: pack.totalBytes
                    )
                    try await writeJournal(
                        pack: pack,
                        catalog: catalog,
                        file: file,
                        state: "paused",
                        completedBytes: completedBeforeCurrent
                    )
                    onEvent(.paused(progress))
                case .cancelled:
                    try await store.discardStaging(
                        for: pack,
                        catalogDigest: catalog.digest
                    )
                    try await store.writeJournalData(nil)
                case .invalidResponse, .rejectedRedirect, .transport:
                    try await writeJournal(
                        pack: pack,
                        catalog: catalog,
                        file: file,
                        state: "interrupted",
                        completedBytes: completedBeforeCurrent
                    )
                }
                throw error
            }
            activeFileIdentifierByPack.removeValue(forKey: pack.id)
        }

        onEvent(.publishing)
        let installedURL = try await store.sealAndPublish(
            pack: pack,
            catalogDigest: catalog.digest
        )
        try await store.writeJournalData(nil)
        onEvent(.accepted(installedURL))
    }

    func pause(packID: String) {
        guard let identifier = activeFileIdentifierByPack[packID] else {
            return
        }
        transport.pause(identifier: identifier)
    }

    func cancel(packID: String) {
        guard let identifier = activeFileIdentifierByPack[packID] else {
            return
        }
        transport.cancel(identifier: identifier)
    }

    private func completedBytes(
        pack: ModelDeliveryPack,
        catalogDigest: String
    ) async -> Int64 {
        var result: Int64 = 0
        for file in pack.files {
            if await store.stagedFileIsValid(
                file,
                pack: pack,
                catalogDigest: catalogDigest
            ) {
                result += file.expectedSize
            }
        }
        return result
    }

    private func writeJournal(
        pack: ModelDeliveryPack,
        catalog: ModelDeliveryCatalog,
        file: ModelDeliveryFile,
        state: String,
        completedBytes: Int64
    ) async throws {
        let journal = ModelDownloadJournal(
            schema: "auralign-model-download-journal.v1",
            catalogSHA256: catalog.digest,
            packID: pack.id,
            packVersion: pack.version,
            fileID: file.id,
            state: state,
            completedBytes: completedBytes,
            totalBytes: pack.totalBytes
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        try await store.writeJournalData(try encoder.encode(journal))
    }

    enum ServiceError: LocalizedError {
        case packNotInSignedCatalog

        var errorDescription: String? {
            "The requested model pack is absent from the signed delivery catalog."
        }
    }
}

private struct ModelDownloadJournal: Codable {
    var schema: String
    var catalogSHA256: String
    var packID: String
    var packVersion: String
    var fileID: String
    var state: String
    var completedBytes: Int64
    var totalBytes: Int64

    enum CodingKeys: String, CodingKey {
        case schema, state
        case catalogSHA256 = "catalog_sha256"
        case packID = "pack_id"
        case packVersion = "pack_version"
        case fileID = "file_id"
        case completedBytes = "completed_bytes"
        case totalBytes = "total_bytes"
    }
}

private extension NSLock {
    func withLock<T>(_ body: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try body()
    }
}
