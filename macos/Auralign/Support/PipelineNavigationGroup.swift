// SPDX-FileCopyrightText: 2026 Matthew Penkala
//
// SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import Foundation

/// Task-oriented grouping over the unchanged Python/catalog section IDs.
/// Presentation may evolve without renaming or migrating any engine key.
enum PipelineNavigationGroup: String, CaseIterable, Identifiable {
    case sourceAudio
    case recognition
    case speakers
    case refinement
    case delivery

    var id: String { rawValue }

    var title: String {
        switch self {
        case .sourceAudio:
            return "Source & Audio"
        case .recognition:
            return "Recognition"
        case .speakers:
            return "Speakers"
        case .refinement:
            return "Refinement"
        case .delivery:
            return "Delivery"
        }
    }

    var symbol: String {
        switch self {
        case .sourceAudio:
            return "waveform"
        case .recognition:
            return "text.quote"
        case .speakers:
            return "person.2.wave.2"
        case .refinement:
            return "slider.horizontal.3"
        case .delivery:
            return "square.and.arrow.up"
        }
    }

    var sectionIDs: [String] {
        switch self {
        case .sourceAudio:
            return ["general", "audio"]
        case .recognition:
            return ["asr"]
        case .speakers:
            return ["diarization", "speakers", "identity"]
        case .refinement:
            return ["alignment", "qa_redecode", "cleanup"]
        case .delivery:
            return ["subtitles"]
        }
    }

    static func containing(sectionID: String) -> PipelineNavigationGroup? {
        allCases.first { $0.sectionIDs.contains(sectionID) }
    }

    func firstAvailableSection(in catalog: FieldCatalog) -> String? {
        sectionIDs.first { catalog.section($0) != nil }
    }
}
