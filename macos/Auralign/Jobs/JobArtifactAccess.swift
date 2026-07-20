// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Darwin
import Foundation

/// A regular result file whose path, size, and filesystem identity were
/// verified through directory descriptors after the engine process exited.
///
/// Callers must not treat `url` alone as durable authority. Use
/// `JobArtifactAccess.withRevalidatedArtifact(_:in:perform:)` immediately
/// before a native action that consumes the path.
struct VerifiedJobArtifact: Identifiable, Equatable {
    var name: String
    var sizeBytes: Int64
    var url: URL
    var identity: ArtifactFileIdentity

    var id: String { name }

    var jobArtifact: JobArtifact {
        JobArtifact(
            name: name,
            sizeBytes: sizeBytes,
            url: url,
            identity: identity
        )
    }
}

/// A post-exit view of one job directory. Completed outputs are reconciled
/// exactly against the result envelope. Partial outputs are an independently
/// verified best-effort inventory and never imply successful completion.
struct VerifiedJobOutput: Equatable {
    var outputDirectory: URL
    var outputIdentity: ArtifactFileIdentity
    var artifacts: [VerifiedJobArtifact]
}

/// Fail-closed errors from the result filesystem boundary.
///
/// Messages deliberately describe the violated contract without including
/// private paths or artifact contents. `posixCode` is retained for local
/// technical diagnostics only.
struct JobArtifactAccessError: LocalizedError, Equatable {
    enum Code: String, Equatable {
        case invalidRoot
        case invalidJobDirectory
        case jobDirectoryAlreadyExists
        case rootChanged
        case jobDirectoryMissing
        case jobDirectoryChanged
        case tooManyEntries
        case invalidEntryName
        case unsafeEntry
        case artifactChanged
        case artifactMismatch
        case systemFailure
    }

    var code: Code
    var message: String
    var posixCode: Int32?

    var errorDescription: String? { message }

    var diagnosticDescription: String {
        guard let posixCode else { return code.rawValue }
        return "\(code.rawValue) (POSIX \(posixCode))"
    }
}

/// Retained descriptor authority for one planned output directory.
///
/// Acquire this object immediately before launching the engine and retain it
/// until the child has exited and been reaped. Acquisition requires:
///
/// - an existing, canonical, non-symlink output root;
/// - a canonical direct child as the planned job directory; and
/// - that child to be absent at acquisition time.
///
/// All post-exit traversal is relative to the retained root descriptor. The
/// implementation rejects replacement of the selected root or job directory,
/// never follows the job-directory or artifact leaf through a symlink, and
/// opens regular files with `O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`.
///
/// The class is intentionally synchronous. Native path actions should be
/// performed inside the supplied revalidation closures so the corresponding
/// descriptors remain open for the duration of the action.
final class JobArtifactAccess {
    static let maximumDirectoryEntries = 1_024
    static let maximumArtifacts = 512
    static let maximumArtifactNameBytes = 255

    let outputRoot: URL
    let plannedJobDirectory: URL
    let rootIdentity: ArtifactFileIdentity

    private let rootDescriptor: Int32
    private let childName: String

    private init(
        outputRoot: URL,
        plannedJobDirectory: URL,
        rootDescriptor: Int32,
        rootIdentity: ArtifactFileIdentity,
        childName: String
    ) {
        self.outputRoot = outputRoot
        self.plannedJobDirectory = plannedJobDirectory
        self.rootDescriptor = rootDescriptor
        self.rootIdentity = rootIdentity
        self.childName = childName
    }

    deinit {
        Darwin.close(rootDescriptor)
    }

    /// Retains descriptor authority before process launch.
    ///
    /// A later engine-created child can therefore be validated against both
    /// the selected root and the fresh direct-child plan without re-resolving
    /// an untrusted absolute path.
    static func retain(
        outputRoot requestedRoot: URL,
        plannedJobDirectory requestedJobDirectory: URL
    ) throws -> JobArtifactAccess {
        let outputRoot = requestedRoot.standardizedFileURL
        let plannedJobDirectory = requestedJobDirectory.standardizedFileURL

        guard requestedRoot.isFileURL,
              requestedJobDirectory.isFileURL,
              requestedRoot.path == outputRoot.path,
              requestedJobDirectory.path == plannedJobDirectory.path,
              (outputRoot.path as NSString).isAbsolutePath,
              outputRoot.path != "/"
        else {
            throw error(
                .invalidRoot,
                "The selected output folder is not a canonical local directory."
            )
        }

        let parent = plannedJobDirectory.deletingLastPathComponent()
        let childName = plannedJobDirectory.lastPathComponent
        guard parent.path == outputRoot.path,
              validLeafName(childName),
              !childName.hasPrefix(".")
        else {
            throw error(
                .invalidJobDirectory,
                "The planned job folder must be a visible, fresh direct child of the output folder."
            )
        }

        // Reject indirection in any component. The descriptor and lstat checks
        // below separately close the final-component race.
        guard outputRoot
            .resolvingSymlinksInPath()
            .standardizedFileURL
            .path == outputRoot.path
        else {
            throw error(
                .invalidRoot,
                "The selected output folder must not contain symbolic-link indirection."
            )
        }

        var pathStatus = stat()
        guard lstatPath(outputRoot.path, into: &pathStatus) == 0,
              isDirectory(pathStatus.st_mode),
              !isSymbolicLink(pathStatus.st_mode)
        else {
            throw error(
                .invalidRoot,
                "The selected output folder is unavailable or is not a safe directory.",
                posixCode: errno
            )
        }

        let descriptor = openPath(
            outputRoot.path,
            flags: O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC
        )
        guard descriptor >= 0 else {
            throw error(
                .invalidRoot,
                "The selected output folder could not be opened safely.",
                posixCode: errno
            )
        }

        do {
            var descriptorStatus = stat()
            guard Darwin.fstat(descriptor, &descriptorStatus) == 0,
                  isDirectory(descriptorStatus.st_mode)
            else {
                throw error(
                    .invalidRoot,
                    "The selected output folder could not be verified.",
                    posixCode: errno
                )
            }
            let pathIdentity = try identity(of: pathStatus)
            let descriptorIdentity = try identity(of: descriptorStatus)
            guard pathIdentity == descriptorIdentity else {
                throw error(
                    .rootChanged,
                    "The selected output folder changed while it was being opened."
                )
            }
            guard outputRoot
                .resolvingSymlinksInPath()
                .standardizedFileURL
                .path == outputRoot.path
            else {
                throw error(
                    .rootChanged,
                    "The selected output folder gained symbolic-link indirection."
                )
            }

            var childStatus = stat()
            let childLookup = fstatatLeaf(
                descriptor,
                name: childName,
                into: &childStatus,
                flags: AT_SYMLINK_NOFOLLOW
            )
            if childLookup == 0 {
                throw error(
                    .jobDirectoryAlreadyExists,
                    "The planned job folder already exists. Start again with a fresh destination."
                )
            }
            guard errno == ENOENT else {
                throw error(
                    .systemFailure,
                    "The planned job folder could not be checked safely.",
                    posixCode: errno
                )
            }

            return JobArtifactAccess(
                outputRoot: outputRoot,
                plannedJobDirectory: plannedJobDirectory,
                rootDescriptor: descriptor,
                rootIdentity: descriptorIdentity,
                childName: childName
            )
        } catch {
            Darwin.close(descriptor)
            throw error
        }
    }

    /// Reconciles a successful result exactly after the child has exited.
    ///
    /// Hidden entries count toward the directory limit but are not published.
    /// Visible directories are omitted because the protocol scope is immediate
    /// regular files. Any visible symlink or special file rejects the result.
    func materializeCompleted(
        _ envelope: PendingJobResultEnvelope
    ) throws -> VerifiedJobOutput {
        guard envelope.outputDirectory.path == plannedJobDirectory.path else {
            throw Self.error(
                .jobDirectoryChanged,
                "The engine result directory did not match the planned job folder."
            )
        }

        guard let opened = try openJobDirectory(
            expectedIdentity: envelope.outputIdentity,
            allowMissing: false
        ) else {
            throw Self.error(
                .jobDirectoryMissing,
                "The completed job folder is missing."
            )
        }
        defer { Darwin.close(opened.descriptor) }

        let actualArtifacts = try enumerateArtifacts(
            jobDescriptor: opened.descriptor
        )
        try reconcile(
            actual: actualArtifacts,
            expected: envelope.artifacts
        )
        try confirmJobDirectoryIdentity(opened.identity)
        return VerifiedJobOutput(
            outputDirectory: plannedJobDirectory,
            outputIdentity: opened.identity,
            artifacts: actualArtifacts
        )
    }

    /// Safely inventories partial files after failure or cancellation.
    ///
    /// `nil` means the engine never created the planned job directory. A
    /// returned value is not evidence that the run completed. Unsafe visible
    /// entries reject the entire inventory rather than being silently exposed.
    func inventoryPartialAfterExit(
        expectedIdentity: ArtifactFileIdentity
    ) throws -> VerifiedJobOutput? {
        guard let opened = try openJobDirectory(
            expectedIdentity: expectedIdentity,
            allowMissing: true
        ) else {
            return nil
        }
        defer { Darwin.close(opened.descriptor) }

        let artifacts = try enumerateArtifacts(jobDescriptor: opened.descriptor)
        try confirmJobDirectoryIdentity(opened.identity)
        return VerifiedJobOutput(
            outputDirectory: plannedJobDirectory,
            outputIdentity: opened.identity,
            artifacts: artifacts
        )
    }

    /// Revalidates the job directory and keeps its descriptor open while a
    /// synchronous native action uses the directory path.
    func withRevalidatedDirectory<T>(
        _ output: VerifiedJobOutput,
        perform body: (URL) throws -> T
    ) throws -> T {
        try requireOwned(output)
        guard let opened = try openJobDirectory(
            expectedIdentity: output.outputIdentity,
            allowMissing: false
        ) else {
            throw Self.error(
                .jobDirectoryMissing,
                "The job folder is no longer available."
            )
        }
        defer { Darwin.close(opened.descriptor) }
        return try body(plannedJobDirectory)
    }

    /// Revalidates one artifact and keeps both its file descriptor and its job
    /// directory descriptor open while a synchronous native action uses the
    /// path. The identity and size must still match the verified inventory.
    func withRevalidatedArtifact<T>(
        _ artifact: VerifiedJobArtifact,
        in output: VerifiedJobOutput,
        perform body: (URL) throws -> T
    ) throws -> T {
        try requireOwned(output)
        guard output.artifacts.contains(artifact),
              artifact.url.path
                  == plannedJobDirectory.appendingPathComponent(
                      artifact.name,
                      isDirectory: false
                  ).path
        else {
            throw Self.error(
                .artifactMismatch,
                "The selected artifact does not belong to this verified result."
            )
        }
        guard let opened = try openJobDirectory(
            expectedIdentity: output.outputIdentity,
            allowMissing: false
        ) else {
            throw Self.error(
                .jobDirectoryMissing,
                "The job folder is no longer available."
            )
        }
        defer { Darwin.close(opened.descriptor) }

        let artifactDescriptor = try openVerifiedRegularFile(
            parentDescriptor: opened.descriptor,
            name: artifact.name,
            expectedIdentity: artifact.identity,
            expectedSize: artifact.sizeBytes
        )
        defer { Darwin.close(artifactDescriptor) }
        return try body(artifact.url)
    }

    // MARK: - Directory authority

    private func requireOwned(_ output: VerifiedJobOutput) throws {
        guard output.outputDirectory.path == plannedJobDirectory.path else {
            throw Self.error(
                .jobDirectoryChanged,
                "The result does not belong to this output authority."
            )
        }
    }

    private func openJobDirectory(
        expectedIdentity: ArtifactFileIdentity?,
        allowMissing: Bool
    ) throws -> (descriptor: Int32, identity: ArtifactFileIdentity)? {
        try revalidateRoot()

        var childStatus = stat()
        let lookup = Self.fstatatLeaf(
            rootDescriptor,
            name: childName,
            into: &childStatus,
            flags: AT_SYMLINK_NOFOLLOW
        )
        if lookup != 0 {
            if allowMissing, errno == ENOENT {
                return nil
            }
            throw Self.error(
                .jobDirectoryMissing,
                "The planned job folder is unavailable.",
                posixCode: errno
            )
        }
        guard Self.isDirectory(childStatus.st_mode),
              !Self.isSymbolicLink(childStatus.st_mode)
        else {
            throw Self.error(
                .jobDirectoryChanged,
                "The planned job folder was replaced by an unsafe filesystem entry."
            )
        }

        let descriptor = Self.openatLeaf(
            rootDescriptor,
            name: childName,
            flags: O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC
        )
        guard descriptor >= 0 else {
            throw Self.error(
                .jobDirectoryChanged,
                "The planned job folder could not be opened safely.",
                posixCode: errno
            )
        }
        do {
            var descriptorStatus = stat()
            guard Darwin.fstat(descriptor, &descriptorStatus) == 0,
                  Self.isDirectory(descriptorStatus.st_mode)
            else {
                throw Self.error(
                    .jobDirectoryChanged,
                    "The planned job folder could not be verified.",
                    posixCode: errno
                )
            }
            let lookupIdentity = try Self.identity(of: childStatus)
            let descriptorIdentity = try Self.identity(of: descriptorStatus)
            guard lookupIdentity == descriptorIdentity else {
                throw Self.error(
                    .jobDirectoryChanged,
                    "The planned job folder changed while it was being opened."
                )
            }
            if let expectedIdentity,
               descriptorIdentity != expectedIdentity {
                throw Self.error(
                    .jobDirectoryChanged,
                    "The job folder identity did not match the engine result."
                )
            }
            return (descriptor, descriptorIdentity)
        } catch {
            Darwin.close(descriptor)
            throw error
        }
    }

    private func revalidateRoot() throws {
        var retainedStatus = stat()
        guard Darwin.fstat(rootDescriptor, &retainedStatus) == 0,
              Self.isDirectory(retainedStatus.st_mode),
              try Self.identity(of: retainedStatus) == rootIdentity
        else {
            throw Self.error(
                .rootChanged,
                "The retained output folder is no longer valid.",
                posixCode: errno
            )
        }

        var currentPathStatus = stat()
        guard Self.lstatPath(outputRoot.path, into: &currentPathStatus) == 0,
              Self.isDirectory(currentPathStatus.st_mode),
              !Self.isSymbolicLink(currentPathStatus.st_mode),
              try Self.identity(of: currentPathStatus) == rootIdentity,
              outputRoot
                  .resolvingSymlinksInPath()
                  .standardizedFileURL
                  .path == outputRoot.path
        else {
            throw Self.error(
                .rootChanged,
                "The selected output folder was moved or replaced.",
                posixCode: errno
            )
        }
    }

    private func confirmJobDirectoryIdentity(
        _ expectedIdentity: ArtifactFileIdentity
    ) throws {
        guard let reopened = try openJobDirectory(
            expectedIdentity: expectedIdentity,
            allowMissing: false
        ) else {
            throw Self.error(
                .jobDirectoryMissing,
                "The job folder disappeared during verification."
            )
        }
        Darwin.close(reopened.descriptor)
    }

    // MARK: - Bounded descriptor-relative inventory

    private func enumerateArtifacts(
        jobDescriptor: Int32
    ) throws -> [VerifiedJobArtifact] {
        let duplicate = Darwin.dup(jobDescriptor)
        guard duplicate >= 0 else {
            throw Self.error(
                .systemFailure,
                "The job folder could not be enumerated safely.",
                posixCode: errno
            )
        }
        guard Darwin.fcntl(duplicate, F_SETFD, FD_CLOEXEC) == 0 else {
            let code = errno
            Darwin.close(duplicate)
            throw Self.error(
                .systemFailure,
                "The job folder descriptor could not be secured.",
                posixCode: code
            )
        }
        guard let directory = Darwin.fdopendir(duplicate) else {
            let code = errno
            Darwin.close(duplicate)
            throw Self.error(
                .systemFailure,
                "The job folder could not be enumerated safely.",
                posixCode: code
            )
        }
        // fdopendir owns `duplicate` after success.
        defer { Darwin.closedir(directory) }

        var entryCount = 0
        var artifacts: [VerifiedJobArtifact] = []
        while true {
            errno = 0
            guard let entry = Darwin.readdir(directory) else {
                guard errno == 0 else {
                    throw Self.error(
                        .systemFailure,
                        "The job folder changed during enumeration.",
                        posixCode: errno
                    )
                }
                break
            }

            let name = try Self.validatedEntryName(entry)
            if name == "." || name == ".." {
                continue
            }
            entryCount += 1
            guard entryCount <= Self.maximumDirectoryEntries else {
                throw Self.error(
                    .tooManyEntries,
                    "The job folder contains too many entries to inspect safely."
                )
            }

            // Match Python's immediate-regular-file scope: hidden entries
            // count against the bound, then remain outside the published list.
            if name.hasPrefix(".") {
                continue
            }
            guard Self.validLeafName(name) else {
                throw Self.error(
                    .invalidEntryName,
                    "The job folder contains an unsafe artifact name."
                )
            }

            var lookupStatus = stat()
            guard Self.fstatatLeaf(
                jobDescriptor,
                name: name,
                into: &lookupStatus,
                flags: AT_SYMLINK_NOFOLLOW
            ) == 0
            else {
                throw Self.error(
                    .artifactChanged,
                    "A result entry changed during inspection.",
                    posixCode: errno
                )
            }

            if Self.isDirectory(lookupStatus.st_mode) {
                continue
            }
            guard Self.isRegularFile(lookupStatus.st_mode) else {
                throw Self.error(
                    .unsafeEntry,
                    "The job folder contains a visible symbolic link or special file."
                )
            }

            let descriptor = try openVerifiedRegularFile(
                parentDescriptor: jobDescriptor,
                name: name,
                lookupStatus: lookupStatus
            )
            var descriptorStatus = stat()
            guard Darwin.fstat(descriptor, &descriptorStatus) == 0 else {
                let code = errno
                Darwin.close(descriptor)
                throw Self.error(
                    .artifactChanged,
                    "A result artifact could not be verified.",
                    posixCode: code
                )
            }
            Darwin.close(descriptor)

            guard descriptorStatus.st_size >= 0 else {
                throw Self.error(
                    .unsafeEntry,
                    "A result artifact reported an invalid file size."
                )
            }
            artifacts.append(
                VerifiedJobArtifact(
                    name: name,
                    sizeBytes: Int64(descriptorStatus.st_size),
                    url: plannedJobDirectory.appendingPathComponent(
                        name,
                        isDirectory: false
                    ),
                    identity: try Self.identity(of: descriptorStatus)
                )
            )
            guard artifacts.count <= Self.maximumArtifacts else {
                throw Self.error(
                    .tooManyEntries,
                    "The job folder contains too many file artifacts to inspect safely."
                )
            }
        }
        return artifacts.sorted { $0.name < $1.name }
    }

    private func openVerifiedRegularFile(
        parentDescriptor: Int32,
        name: String,
        lookupStatus: stat
    ) throws -> Int32 {
        let descriptor = Self.openatLeaf(
            parentDescriptor,
            name: name,
            flags: O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC
        )
        guard descriptor >= 0 else {
            throw Self.error(
                .artifactChanged,
                "A result artifact changed while it was being opened.",
                posixCode: errno
            )
        }
        do {
            var descriptorStatus = stat()
            guard Darwin.fstat(descriptor, &descriptorStatus) == 0,
                  Self.isRegularFile(descriptorStatus.st_mode),
                  try Self.identity(of: descriptorStatus)
                    == Self.identity(of: lookupStatus),
                  descriptorStatus.st_size == lookupStatus.st_size
            else {
                throw Self.error(
                    .artifactChanged,
                    "A result artifact changed while it was being verified.",
                    posixCode: errno
                )
            }
            return descriptor
        } catch {
            Darwin.close(descriptor)
            throw error
        }
    }

    private func openVerifiedRegularFile(
        parentDescriptor: Int32,
        name: String,
        expectedIdentity: ArtifactFileIdentity,
        expectedSize: Int64
    ) throws -> Int32 {
        guard Self.validLeafName(name), expectedSize >= 0 else {
            throw Self.error(
                .artifactMismatch,
                "The selected artifact has invalid verification evidence."
            )
        }
        let descriptor = Self.openatLeaf(
            parentDescriptor,
            name: name,
            flags: O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC
        )
        guard descriptor >= 0 else {
            throw Self.error(
                .artifactChanged,
                "The selected artifact is no longer available.",
                posixCode: errno
            )
        }
        do {
            var status = stat()
            guard Darwin.fstat(descriptor, &status) == 0,
                  Self.isRegularFile(status.st_mode),
                  status.st_size >= 0,
                  Int64(status.st_size) == expectedSize,
                  try Self.identity(of: status) == expectedIdentity
            else {
                throw Self.error(
                    .artifactChanged,
                    "The selected artifact changed after the result was verified.",
                    posixCode: errno
                )
            }
            return descriptor
        } catch {
            Darwin.close(descriptor)
            throw error
        }
    }

    private func reconcile(
        actual: [VerifiedJobArtifact],
        expected: [PendingArtifactDescriptor]
    ) throws {
        guard actual.count == expected.count else {
            throw Self.error(
                .artifactMismatch,
                "The files in the completed job folder did not match the engine result."
            )
        }
        for (artifact, descriptor) in zip(actual, expected) {
            guard artifact.name == descriptor.name,
                  artifact.sizeBytes == descriptor.sizeBytes,
                  artifact.identity == descriptor.identity
            else {
                throw Self.error(
                    .artifactMismatch,
                    "A completed result artifact did not match the engine evidence."
                )
            }
        }
    }

    // MARK: - POSIX helpers

    private static func validatedEntryName(
        _ entry: UnsafeMutablePointer<dirent>
    ) throws -> String {
        let length = Int(entry.pointee.d_namlen)
        var tuple = entry.pointee.d_name
        let data = withUnsafeBytes(of: &tuple) { bytes in
            Data(bytes.prefix(length))
        }
        guard length > 0,
              length <= maximumArtifactNameBytes,
              data.count == length,
              let name = String(data: data, encoding: .utf8),
              !name.contains("\0")
        else {
            throw error(
                .invalidEntryName,
                "The job folder contains an invalid UTF-8 entry name."
            )
        }
        return name
    }

    private static func validLeafName(_ name: String) -> Bool {
        !name.isEmpty
            && name != "."
            && name != ".."
            && name.utf8.count <= maximumArtifactNameBytes
            && !name.contains("/")
            && !name.contains("\0")
    }

    private static func identity(of status: stat) throws -> ArtifactFileIdentity {
        guard status.st_dev >= 0 else {
            throw error(
                .systemFailure,
                "A filesystem identity could not be represented safely."
            )
        }
        return ArtifactFileIdentity(
            device: UInt64(status.st_dev),
            inode: UInt64(status.st_ino)
        )
    }

    private static func isDirectory(_ mode: mode_t) -> Bool {
        (mode & S_IFMT) == S_IFDIR
    }

    private static func isRegularFile(_ mode: mode_t) -> Bool {
        (mode & S_IFMT) == S_IFREG
    }

    private static func isSymbolicLink(_ mode: mode_t) -> Bool {
        (mode & S_IFMT) == S_IFLNK
    }

    private static func openPath(_ path: String, flags: Int32) -> Int32 {
        path.withCString { pointer in
            Darwin.open(pointer, flags)
        }
    }

    private static func openatLeaf(
        _ parentDescriptor: Int32,
        name: String,
        flags: Int32
    ) -> Int32 {
        name.withCString { pointer in
            Darwin.openat(parentDescriptor, pointer, flags)
        }
    }

    private static func lstatPath(
        _ path: String,
        into status: UnsafeMutablePointer<stat>
    ) -> Int32 {
        path.withCString { pointer in
            Darwin.lstat(pointer, status)
        }
    }

    private static func fstatatLeaf(
        _ parentDescriptor: Int32,
        name: String,
        into status: UnsafeMutablePointer<stat>,
        flags: Int32
    ) -> Int32 {
        name.withCString { pointer in
            Darwin.fstatat(parentDescriptor, pointer, status, flags)
        }
    }

    private static func error(
        _ code: JobArtifactAccessError.Code,
        _ message: String,
        posixCode: Int32? = nil
    ) -> JobArtifactAccessError {
        JobArtifactAccessError(
            code: code,
            message: message,
            posixCode: posixCode
        )
    }
}
