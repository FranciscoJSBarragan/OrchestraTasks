import Foundation

struct TaskCapabilities: Codable, Equatable {
    var edit = false
    var note = false
    var archive = false
    var restoreArchive = false
    var trash = false
    var restoreTrash = false
    var requestStop = false
    var withdrawStop = false
    var reopen = false
    var purge = false

    enum CodingKeys: String, CodingKey {
        case edit, note, archive, trash, reopen, purge
        case restoreArchive = "restore_archive"
        case restoreTrash = "restore_trash"
        case requestStop = "request_stop"
        case withdrawStop = "withdraw_stop"
    }

    init() {}
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        edit = try values.decodeIfPresent(Bool.self, forKey: .edit) ?? false
        note = try values.decodeIfPresent(Bool.self, forKey: .note) ?? false
        archive = try values.decodeIfPresent(Bool.self, forKey: .archive) ?? false
        restoreArchive = try values.decodeIfPresent(Bool.self, forKey: .restoreArchive) ?? false
        trash = try values.decodeIfPresent(Bool.self, forKey: .trash) ?? false
        restoreTrash = try values.decodeIfPresent(Bool.self, forKey: .restoreTrash) ?? false
        requestStop = try values.decodeIfPresent(Bool.self, forKey: .requestStop) ?? false
        withdrawStop = try values.decodeIfPresent(Bool.self, forKey: .withdrawStop) ?? false
        reopen = try values.decodeIfPresent(Bool.self, forKey: .reopen) ?? false
        purge = try values.decodeIfPresent(Bool.self, forKey: .purge) ?? false
    }
}

struct TaskInitiative: Decodable, Equatable {
    let id: String?
    let title: String
    let brief: String?
}

struct OrchestraTask: Decodable, Identifiable {
    let id: String
    var shortID: String?
    var title: String
    var brief: String?
    var repository: String?
    var worktree: String?
    var branch: String?
    var preparationStatus: String
    var disposition: String
    var stage: String?
    var status: String?
    var summary: String?
    var blocker: String?
    var nextAction: String?
    var updatedAt: String
    var stopRequestedAt: String?
    var stale: Bool
    var initiative: TaskInitiative?
    var capabilities: TaskCapabilities

    enum CodingKeys: String, CodingKey {
        case id, brief, repository, worktree, branch, disposition, stage, status, summary
        case blocker, stale, initiative, capabilities
        case shortID = "short_id"
        case title
        case label
        case preparationStatus = "preparation_status"
        case nextAction = "next_action"
        case updatedAt = "updated_at"
        case stopRequestedAt = "stop_requested_at"
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        shortID = try values.decodeIfPresent(String.self, forKey: .shortID)
        title = try values.decodeIfPresent(String.self, forKey: .title)
            ?? values.decodeIfPresent(String.self, forKey: .label) ?? id
        brief = try values.decodeIfPresent(String.self, forKey: .brief)
        repository = try values.decodeIfPresent(String.self, forKey: .repository)
        worktree = try values.decodeIfPresent(String.self, forKey: .worktree)
        branch = try values.decodeIfPresent(String.self, forKey: .branch)
        preparationStatus = try values.decodeIfPresent(String.self, forKey: .preparationStatus)
            ?? values.decodeIfPresent(String.self, forKey: .status) ?? "legacy"
        disposition = try values.decodeIfPresent(String.self, forKey: .disposition) ?? "open"
        stage = try values.decodeIfPresent(String.self, forKey: .stage)
        status = try values.decodeIfPresent(String.self, forKey: .status)
        summary = try values.decodeIfPresent(String.self, forKey: .summary)
        blocker = try values.decodeIfPresent(String.self, forKey: .blocker)
        nextAction = try values.decodeIfPresent(String.self, forKey: .nextAction)
        updatedAt = try values.decodeIfPresent(String.self, forKey: .updatedAt) ?? ""
        stopRequestedAt = try values.decodeIfPresent(String.self, forKey: .stopRequestedAt)
        stale = try values.decodeIfPresent(Bool.self, forKey: .stale) ?? false
        initiative = try values.decodeIfPresent(TaskInitiative.self, forKey: .initiative)
        capabilities = try values.decodeIfPresent(TaskCapabilities.self, forKey: .capabilities)
            ?? TaskCapabilities()
    }

    var displayID: String { shortID ?? "" }
    var effectiveStatus: String {
        if disposition == "trashed" { return "trashed" }
        if disposition == "archived" { return "archived" }
        if ["draft", "ready", "cancelled", "completed"].contains(preparationStatus) {
            return preparationStatus
        }
        return status ?? preparationStatus
    }
    var repositoryName: String {
        guard let repository, !repository.isEmpty else { return String(localized: "No repository") }
        return URL(fileURLWithPath: repository).lastPathComponent
    }
    var worktreeLabel: String? {
        guard let worktree, !worktree.isEmpty else { return nil }
        if let repository, !repository.isEmpty,
           URL(fileURLWithPath: worktree).standardizedFileURL.path
            == URL(fileURLWithPath: repository).standardizedFileURL.path {
            return nil
        }
        let name = URL(fileURLWithPath: worktree).lastPathComponent
        guard name.count > 16 else { return name }
        let left = name.prefix(7)
        let right = name.suffix(8)
        return "\(left)…\(right)"
    }
    var isActive: Bool { disposition == "open" && preparationStatus == "adopted" }

    mutating func mergeObservation(_ observed: OrchestraTask) {
        worktree = observed.worktree
        branch = observed.branch
        stage = observed.stage
        status = observed.status
        summary = observed.summary
        blocker = observed.blocker
        nextAction = observed.nextAction
        stale = observed.stale
        initiative = observed.initiative ?? initiative
        if observed.updatedAt > updatedAt { updatedAt = observed.updatedAt }
    }
}

struct TaskListEnvelope: Decodable { let tasks: [OrchestraTask] }
struct ControlResponse: Decodable {
    let status: String
    let reason: String?
    let warning: String?
    let task: OrchestraTask?
    let tasks: [OrchestraTask]?
}
struct HubRepository: Decodable {
    let path: String
    let name: String
}
struct HubResponse: Decodable {
    let status: String
    let tasks: [OrchestraTask]
    let repositories: [HubRepository]
}

struct RepositoryTaskGroup: Identifiable {
    let path: String
    let name: String
    let tasks: [OrchestraTask]
    var id: String { path }
}

func repositoryTaskGroups(
    _ tasks: [OrchestraTask],
    names: [String: String] = [:]
) -> [RepositoryTaskGroup] {
    Dictionary(grouping: tasks) { $0.repository ?? "" }.map { path, groupedTasks in
        RepositoryTaskGroup(
            path: path,
            name: path.isEmpty
                ? String(localized: "No repository")
                : names[path] ?? groupedTasks.first?.repositoryName ?? path,
            tasks: groupedTasks
        )
    }.sorted {
        ($0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending)
            || ($0.name.caseInsensitiveCompare($1.name) == .orderedSame && $0.path < $1.path)
    }
}

enum TaskSection: String, CaseIterable, Identifiable {
    case active, drafts, ready, cancelled, completed, archived, trash
    var id: String { rawValue }
    var title: String {
        switch self {
        case .active: return String(localized: "Active")
        case .drafts: return String(localized: "Drafts")
        case .ready: return String(localized: "Ready")
        case .cancelled: return String(localized: "Cancelled")
        case .completed: return String(localized: "Completed")
        case .archived: return String(localized: "Archived")
        case .trash: return String(localized: "Trash")
        }
    }
    var symbol: String {
        switch self {
        case .active: return "waveform.path.ecg"
        case .drafts: return "square.and.pencil"
        case .ready: return "checkmark.circle"
        case .cancelled: return "pause.circle"
        case .completed: return "checkmark.seal"
        case .archived: return "archivebox"
        case .trash: return "trash"
        }
    }
    func contains(_ task: OrchestraTask) -> Bool {
        switch self {
        case .active: return task.disposition == "open" && task.preparationStatus == "adopted"
        case .drafts: return task.disposition == "open" && task.preparationStatus == "draft"
        case .ready: return task.disposition == "open" && task.preparationStatus == "ready"
        case .cancelled: return task.disposition == "open" && task.preparationStatus == "cancelled"
        case .completed: return task.disposition == "open" && task.preparationStatus == "completed"
        case .archived: return task.disposition == "archived"
        case .trash: return task.disposition == "trashed"
        }
    }
}
