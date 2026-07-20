// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import AppKit

/// Shared native file-panel boundary used by toolbar/menu shortcuts and views.
/// The caller remains responsible for applying the selected URL to AppModel.
@MainActor
enum LocalFilePanel {
    static func chooseMedia() -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = "Choose local audio or video; Auralign validates its contents"
        return run(panel)
    }

    static func chooseOutputRoot() -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "Choose the folder that will hold job output folders"
        return run(panel)
    }

    static func chooseDirectory(message: String) -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = message
        return run(panel)
    }

    static func chooseDirectories(message: String) -> [URL] {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = true
        panel.message = message
        return runMany(panel)
    }

    static func chooseFile(message: String) -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = message
        return run(panel)
    }

    private static func run(_ panel: NSOpenPanel) -> URL? {
        runMany(panel).first
    }

    private static func runMany(_ panel: NSOpenPanel) -> [URL] {
        let window = NSApp.keyWindow
        let responder = window?.firstResponder
        let accepted = panel.runModal() == .OK
        let selectedURLs = accepted ? panel.urls : []
        DispatchQueue.main.async {
            window?.makeKeyAndOrderFront(nil)
            if let responder {
                window?.makeFirstResponder(responder)
            }
        }
        return selectedURLs
    }
}
