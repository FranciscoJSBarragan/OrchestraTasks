import AppKit
import Foundation
import UserNotifications

@MainActor
final class TaskStore: ObservableObject {
    @Published var tasks: [OrchestraTask] = []
    @Published var selectedSection: TaskSection = .active
    @Published var selectedID: String?
    @Published var search = ""
    @Published var isRefreshing = false
    @Published var errorMessage: String?
    @Published var lastRefresh: Date?
    @Published var repositoryNames: [String: String] = [:]

    private let hub = HubClient(port: readHubPort())
    private var controlClient: TaskControlClient?
    private var fingerprints: [String: String] = [:]
    private var timer: Timer?

    init() {
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    var selectedTask: OrchestraTask? { tasks.first { $0.id == selectedID } }
    var visibleTasks: [OrchestraTask] {
        tasks.filter(selectedSection.contains).filter {
            search.isEmpty || $0.title.localizedCaseInsensitiveContains(search)
                || $0.displayID.localizedCaseInsensitiveContains(search)
                || $0.repositoryName.localizedCaseInsensitiveContains(search)
                || ($0.worktree?.localizedCaseInsensitiveContains(search) ?? false)
                || ($0.branch?.localizedCaseInsensitiveContains(search) ?? false)
        }
    }
    var visibleTaskGroups: [RepositoryTaskGroup] {
        repositoryTaskGroups(visibleTasks, names: repositoryNames)
    }
    func groups(for tasks: [OrchestraTask]) -> [RepositoryTaskGroup] {
        repositoryTaskGroups(tasks, names: repositoryNames)
    }
    func count(_ section: TaskSection) -> Int { tasks.filter(section.contains).count }
    func selectFirstVisibleTask() {
        selectedID = visibleTasks.first?.id
    }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let controlTasks = try await resolvedControlClient().list()
            let hubTasks: [OrchestraTask]
            let hubAvailable: Bool
            do {
                let summary = try await hub.summary()
                hubTasks = summary.tasks
                repositoryNames = Dictionary(
                    summary.repositories.map { ($0.path, $0.name) },
                    uniquingKeysWith: { _, latest in latest }
                )
                hubAvailable = true
            } catch {
                hubTasks = []
                hubAvailable = false
                errorMessage = String(localized: "Hub is unavailable")
            }
            let observations = Dictionary(hubTasks.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
            var merged = controlTasks
            for index in merged.indices {
                if let observed = observations[merged[index].id] { merged[index].mergeObservation(observed) }
            }
            merged.sort { $0.updatedAt > $1.updatedAt }
            notifyMaterialChanges(merged)
            tasks = merged
            if selectedID == nil || !visibleTasks.contains(where: { $0.id == selectedID }) {
                selectedID = visibleTasks.first?.id
            }
            lastRefresh = Date()
            if hubAvailable { errorMessage = nil }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func perform(_ arguments: [String]) async {
        do {
            let response = try await resolvedControlClient().run(arguments)
            await refresh()
            if let warning = response.warning { errorMessage = warning }
        } catch { errorMessage = error.localizedDescription }
    }

    func create(title: String, brief: String, repository: String) async {
        var arguments = ["task", "create", "--title", title, "--brief", brief, "--source-harness", "macos-app"]
        if !repository.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            arguments += ["--repository", repository]
        }
        await perform(arguments)
        selectedSection = .drafts
    }

    func update(_ task: OrchestraTask, title: String, brief: String, repository: String) async {
        let arguments = [
            "task", "update", "--task", task.id, "--title", title,
            "--brief", brief, "--repository", repository,
        ]
        await perform(arguments)
    }

    func addNote(_ task: OrchestraTask, body: String) async {
        await perform(["task", "note", "--task", task.id, "--body", body, "--source-harness", "macos-app"])
    }

    func copyStartInstruction(_ task: OrchestraTask) {
        let prompt = String(localized: "Start %@ with Orchestra").replacingOccurrences(of: "%@", with: task.displayID)
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(prompt, forType: .string)
    }

    private func notifyMaterialChanges(_ current: [OrchestraTask]) {
        let next = Dictionary(current.map { task in
            let value = [task.blocker ?? "", task.stopRequestedAt ?? ""].joined(separator: "|")
            return (task.id, value)
        }, uniquingKeysWith: { _, latest in latest })
        defer { fingerprints = next }
        guard !fingerprints.isEmpty else { return }
        let changed = current.filter {
            fingerprints[$0.id] != nil
                && fingerprints[$0.id] != next[$0.id]
                && ($0.blocker?.isEmpty == false || $0.stopRequestedAt != nil)
        }
        guard !changed.isEmpty else { return }
        let content = UNMutableNotificationContent()
        content.title = changed.count == 1
            ? changed[0].title : String(localized: "Orchestra tasks need attention")
        content.body = changed.count == 1
            ? (changed[0].blocker?.isEmpty == false
                ? changed[0].blocker! : String(localized: "A safe stop was requested"))
            : String(localized: "%lld tasks need attention", defaultValue: "\(changed.count) tasks need attention")
        content.categoryIdentifier = "ORCHESTRA_ATTENTION"
        if changed.count == 1 { content.userInfo = ["task_id": changed[0].id] }
        content.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }

    private func resolvedControlClient() async throws -> TaskControlClient {
        if let controlClient { return controlClient }
        let resolved = try await Task.detached(priority: .utility) {
            try TaskControlClient.installed()
        }.value
        controlClient = resolved
        return resolved
    }
}
