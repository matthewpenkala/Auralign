// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// Curated presentation metadata for configuration fields: titles, grouping,
/// help, prominence, and dependencies. Coverage against the generated engine
/// manifest is enforced by `tests/test_gui_bridge.py` and by Swift unit tests,
/// so a new engine field cannot be silently dropped from the interface.
struct FieldCatalog: Codable {
    struct ChoicePresentation: Codable, Equatable {
        var label: String
        var help: String
        var accessibilityLabel: String

        enum CodingKeys: String, CodingKey {
            case label, help
            case accessibilityLabel = "accessibility_label"
        }
    }

    struct Section: Codable, Identifiable, Equatable {
        var id: String
        var title: String
        var symbol: String
        var summary: String
    }

    struct Requirement: Codable, Equatable {
        var key: String
        var equals: JSONValue?
        var notEquals: JSONValue?

        enum CodingKeys: String, CodingKey {
            case key, equals
            case notEquals = "not_equals"
        }

        func isSatisfied(by config: JSONValue) -> Bool {
            let value = config.value(at: key) ?? .null
            if let equals { return value == equals }
            if let notEquals { return value != notEquals }
            return true
        }
    }

    enum Disposition: String, Codable {
        /// Shown directly in its section.
        case primary
        /// Shown inside the section's Advanced disclosure.
        case advanced
        /// Visible but never editable; `help` explains why.
        case readonly
    }

    struct Entry: Codable, Identifiable, Equatable {
        var key: String
        var title: String
        var help: String
        var section: String
        var disposition: Disposition
        var unit: String?
        var experimental: Bool?
        var requires: [Requirement]?
        /// False keeps a backward-compatible engine field in the audited
        /// schema while removing a non-operational choice from normal product
        /// surfaces.
        var productVisible: Bool?
        /// Optional per-choice product copy used by native pickers. The engine
        /// remains authoritative for accepted values.
        var choicePresentations: [String: ChoicePresentation]?
        /// "file" (default) or "directory" for path pickers.
        var pathStyle: String?

        enum CodingKeys: String, CodingKey {
            case key, title, help, section, disposition, unit, experimental, requires
            case productVisible = "product_visible"
            case choicePresentations = "choice_presentations"
            case pathStyle = "path_style"
        }

        var id: String { key }
        var isExperimental: Bool { experimental ?? false }
        var isDirectoryPath: Bool { pathStyle == "directory" }
        var isProductVisible: Bool { productVisible ?? true }

        func unmetRequirement(in config: JSONValue) -> Requirement? {
            requires?.first { !$0.isSatisfied(by: config) }
        }
    }

    var catalogVersion: String
    var sections: [Section]
    var fields: [Entry]

    enum CodingKeys: String, CodingKey {
        case sections, fields
        case catalogVersion = "catalog_version"
    }

    var fieldsByKey: [String: Entry] {
        Dictionary(uniqueKeysWithValues: fields.map { ($0.key, $0) })
    }

    func entries(inSection sectionID: String) -> [Entry] {
        fields.filter { $0.section == sectionID }
    }

    func section(_ id: String) -> Section? {
        sections.first { $0.id == id }
    }

    static func loadBundled() throws -> FieldCatalog {
        guard let url = Bundle.main.url(forResource: "FieldCatalog", withExtension: "json")
        else {
            throw CocoaError(.fileNoSuchFile)
        }
        return try JSONDecoder().decode(FieldCatalog.self, from: Data(contentsOf: url))
    }
}
