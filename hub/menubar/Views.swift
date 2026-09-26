import AppKit
import SwiftUI

struct StatusMark: View {
    let task: OrchestraTask
    var color: Color {
        if task.blocker?.isEmpty == false { return .red }
        if task.stopRequestedAt != nil { return .orange }
        if task.stale { return .yellow }
        switch task.effectiveStatus {
        case "adopted", "active": return .blue
        case "ready": return .green
        case "cancelled", "completed", "archived", "trashed": return .secondary
        default: return .yellow
        }
    }
    var body: some View {
        Circle().fill(color).frame(width: 7, height: 7).accessibilityHidden(true)
    }
}

struct SidebarView: View {
    @ObservedObject var store: TaskStore
    var body: some View {
        List(TaskSection.allCases, selection: $store.selectedSection) { section in
            Label {
                HStack {
                    Text(section.title)
                    Spacer()
                    let count = store.count(section)
                    if count > 0 { Text("\(count)").foregroundStyle(.secondary).monospacedDigit() }
                }
            } icon: { Image(systemName: section.symbol) }
            .tag(section)
        }
        .listStyle(.sidebar)
        .navigationTitle("Orchestra")
        .onChange(of: store.selectedSection) {
            store.selectFirstVisibleTask()
        }
    }
}

struct TaskRow: View {
    let task: OrchestraTask
    var body: some View {
        HStack(spacing: 7) {
            StatusMark(task: task)
            if let worktree = task.worktreeLabel {
                Text("[\(worktree)]")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .help(task.worktree ?? worktree)
            }
            if !task.displayID.isEmpty {
                Text(task.displayID).font(.caption.monospaced().weight(.semibold))
            }
            Text(task.title).fontWeight(.medium).lineLimit(1)
            if let initiative = task.initiative?.title, !initiative.isEmpty {
                Text("[\(initiative)]").font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
    }
}

struct TaskListView: View {
    @ObservedObject var store: TaskStore
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(store.selectedSection.title).font(.title2.weight(.semibold))
                Text("\(store.visibleTasks.count)").foregroundStyle(.secondary).monospacedDigit()
                Spacer()
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            Divider()
            if store.visibleTasks.isEmpty {
                ContentUnavailableView(
                    "No tasks",
                    systemImage: store.selectedSection.symbol,
                    description: Text("Tasks in this section will appear here.")
                )
            } else {
                List(selection: $store.selectedID) {
                    ForEach(store.visibleTaskGroups) { group in
                        Section(group.name) {
                            ForEach(group.tasks) { task in
                                TaskRow(task: task).tag(task.id)
                            }
                        }
                    }
                }
                .listStyle(.inset)
            }
        }
        .searchable(text: $store.search, placement: .toolbar, prompt: "Search tasks")
    }
}

struct TaskDetailView: View {
    @ObservedObject var store: TaskStore
    @State private var presentation: Presentation?

    enum DestructiveAction {
        case trash, purge
    }
    private enum Presentation: Identifiable {
        case edit(OrchestraTask)
        case note(OrchestraTask)
        case destructive(OrchestraTask, DestructiveAction)

        var id: String {
            switch self {
            case .edit(let task): return "edit-\(task.id)"
            case .note(let task): return "note-\(task.id)"
            case .destructive(let task, let action):
                return "\(String(describing: action))-\(task.id)"
            }
        }
    }

    var body: some View {
        Group {
            if let task = store.selectedTask {
                ScrollView {
                    VStack(alignment: .leading, spacing: 22) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 7) {
                            if !task.displayID.isEmpty {
                                Text(task.displayID).font(.callout.monospaced()).foregroundStyle(.secondary)
                            }
                            Text(task.title).font(.title.weight(.semibold)).textSelection(.enabled)
                            HStack(spacing: 8) {
                                StatusMark(task: task)
                                Text(localizedStatus(task.effectiveStatus)).font(.callout)
                                Text("·").foregroundStyle(.tertiary)
                                Text(task.repositoryName).font(.callout).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Menu {
                            if task.capabilities.edit { Button("Edit draft") { presentation = .edit(task) } }
                            if task.capabilities.note { Button("Add note") { presentation = .note(task) } }
                            Divider()
                            if task.capabilities.archive { Button("Archive") { run(task, "archive") } }
                            if task.capabilities.restoreArchive { Button("Restore archive") { run(task, "restore") } }
                            if task.capabilities.trash { Button("Move to Trash", role: .destructive) { presentation = .destructive(task, .trash) } }
                            if task.capabilities.restoreTrash { Button("Restore from Trash") { run(task, "restore-trash") } }
                            if task.capabilities.purge { Button("Delete permanently…", role: .destructive) { presentation = .destructive(task, .purge) } }
                        } label: { Image(systemName: "ellipsis.circle").font(.title2) }
                        .menuStyle(.borderlessButton)
                    }

                    if let blocker = task.blocker, !blocker.isEmpty {
                        DetailCallout(title: "Needs attention", text: blocker, symbol: "exclamationmark.triangle.fill", color: .red)
                    }
                    if let summary = task.summary, !summary.isEmpty {
                        DetailSection(title: "Current progress", text: summary)
                    }
                    if let next = task.nextAction, !next.isEmpty {
                        DetailSection(title: "Next action", text: next)
                    }
                    if let brief = task.brief, !brief.isEmpty {
                        DetailSection(title: "Brief", text: brief)
                    }
                    if let repository = task.repository, !repository.isEmpty {
                        VStack(alignment: .leading, spacing: 7) {
                            Text("Repository").font(.headline)
                            Text(repository).font(.callout.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                        }
                    }
                    if let worktree = task.worktree, !worktree.isEmpty {
                        DetailSection(title: "Worktree", text: worktree)
                    }
                    if let branch = task.branch, !branch.isEmpty {
                        DetailSection(title: "Branch", text: branch)
                    }
                    if let stage = task.stage, !stage.isEmpty {
                        DetailSection(title: "Stage", text: stage)
                    }
                    if let initiative = task.initiative?.title, !initiative.isEmpty {
                        DetailSection(title: "Initiative", text: initiative)
                    }
                    Divider()
                    HStack {
                        if task.preparationStatus == "ready" {
                            Button("Copy start instruction") {
                                store.copyStartInstruction(task)
                            }.buttonStyle(.borderedProminent)
                        }
                        if task.capabilities.requestStop {
                            Button("Request safe stop") { run(task, "request-stop") }.buttonStyle(.bordered)
                        }
                        if task.capabilities.withdrawStop {
                            Button("Withdraw stop request") { run(task, "withdraw-stop") }.buttonStyle(.bordered)
                        }
                        if task.capabilities.reopen {
                            Button("Reopen task") { run(task, "reopen") }.buttonStyle(.borderedProminent)
                        }
                        Spacer()
                        Text(task.updatedAt).font(.caption).foregroundStyle(.tertiary)
                    }
                }
                    .padding(28)
                    .frame(maxWidth: 720, alignment: .leading)
                }
            } else {
                ContentUnavailableView("Select a task", systemImage: "checklist", description: Text("Choose a task to see its details and available actions."))
            }
        }
        .sheet(item: $presentation) { current in
            switch current {
            case .edit(let task): TaskEditor(store: store, task: task)
            case .note(let task): NoteEditor(store: store, task: task)
            case .destructive(let task, let action):
                DestructiveConfirmation(store: store, task: task, action: action)
            }
        }
    }

    private func run(_ task: OrchestraTask, _ command: String) {
        Task { await store.perform(["task", command, "--task", task.id]) }
    }
}

struct DetailSection: View {
    let title: LocalizedStringKey
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.headline)
            Text(text).font(.body).foregroundStyle(.secondary).textSelection(.enabled)
        }
    }
}

struct DetailCallout: View {
    let title: LocalizedStringKey
    let text: String
    let symbol: String
    let color: Color
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol).foregroundStyle(color)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).fontWeight(.semibold)
                Text(text).foregroundStyle(.secondary)
            }
        }
        .padding(12).background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct NewTaskButton: View {
    @ObservedObject var store: TaskStore
    @State private var isPresented = false
    var body: some View {
        Button { isPresented = true } label: { Label("New task", systemImage: "plus") }
            .sheet(isPresented: $isPresented) { TaskEditor(store: store, task: nil) }
    }
}

struct TaskEditor: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var store: TaskStore
    let task: OrchestraTask?
    @State private var title: String
    @State private var brief: String
    @State private var repository: String

    init(store: TaskStore, task: OrchestraTask?) {
        self.store = store; self.task = task
        _title = State(initialValue: task?.title ?? "")
        _brief = State(initialValue: task?.brief ?? "")
        _repository = State(initialValue: task?.repository ?? "")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(task == nil ? "New task" : "Edit draft").font(.title2.weight(.semibold))
            Form {
                TextField("Title", text: $title)
                TextField("Repository", text: $repository)
                LabeledContent("Brief") { TextEditor(text: $brief).frame(height: 120) }
            }.formStyle(.grouped)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(task == nil ? "Create" : "Save") {
                    Task {
                        if let task { await store.update(task, title: title, brief: brief, repository: repository) }
                        else { await store.create(title: title, brief: brief, repository: repository) }
                        dismiss()
                    }
                }
                .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty || brief.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }.padding(24).frame(width: 520)
    }
}

struct NoteEditor: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var store: TaskStore
    let task: OrchestraTask
    @State private var bodyText = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Add note to \(task.displayID.isEmpty ? task.title : task.displayID)")
                .font(.title2.weight(.semibold))
            TextEditor(text: $bodyText).frame(height: 150).border(.separator)
            HStack { Spacer(); Button("Cancel") { dismiss() }; Button("Add note") {
                Task { await store.addNote(task, body: bodyText); dismiss() }
            }.buttonStyle(.borderedProminent).disabled(bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
        }.padding(24).frame(width: 480)
    }
}

struct DestructiveConfirmation: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject var store: TaskStore
    let task: OrchestraTask
    let action: TaskDetailView.DestructiveAction
    @State private var confirmation = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(action == .purge ? "Delete permanently?" : "Move task to Trash?").font(.title2.weight(.semibold))
            Text(action == .purge
                 ? "This cannot be undone. Type \(task.displayID) to confirm."
                 : "The task can be restored later. Git repositories, worktrees, commits and chats are not changed.")
                .foregroundStyle(.secondary)
            if action == .purge { TextField(task.displayID, text: $confirmation) }
            HStack { Spacer(); Button("Cancel") { dismiss() }; Button(action == .purge ? "Delete permanently" : "Move to Trash", role: .destructive) {
                Task {
                    let args = action == .purge
                        ? ["task", "purge", "--task", task.id, "--confirm", confirmation]
                        : ["task", "trash", "--task", task.id]
                    await store.perform(args); dismiss()
                }
            }.disabled(action == .purge && confirmation.uppercased() != task.displayID.uppercased()) }
        }.padding(24).frame(width: 430)
    }
}

func localizedStatus(_ status: String) -> String {
    switch status {
    case "adopted", "active": return String(localized: "Active")
    case "draft": return String(localized: "Draft")
    case "ready": return String(localized: "Ready")
    case "cancelled": return String(localized: "Cancelled")
    case "completed": return String(localized: "Completed")
    case "archived": return String(localized: "Archived")
    case "trashed": return String(localized: "Trash")
    default: return status.capitalized
    }
}

struct MainWindowView: View {
    @ObservedObject var store: TaskStore
    var body: some View {
        NavigationSplitView {
            SidebarView(store: store).navigationSplitViewColumnWidth(min: 190, ideal: 225, max: 260)
        } content: {
            TaskListView(store: store).navigationSplitViewColumnWidth(min: 290, ideal: 350, max: 440)
        } detail: {
            TaskDetailView(store: store)
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                if store.isRefreshing { ProgressView().controlSize(.small) }
                Button { Task { await store.refresh() } } label: { Image(systemName: "arrow.clockwise") }.help("Refresh")
                NewTaskButton(store: store)
            }
        }
        .overlay(alignment: .bottom) {
            if let error = store.errorMessage {
                Text(error).font(.callout).padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8)).padding(14)
            }
        }
        .task { await store.refresh() }
    }
}

struct MenuBarView: View {
    @Environment(\.openWindow) private var openWindow
    @ObservedObject var store: TaskStore
    var activeTasks: [OrchestraTask] { store.tasks.filter { $0.isActive }.prefix(5).map { $0 } }
    var activeGroups: [RepositoryTaskGroup] { store.groups(for: activeTasks) }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Orchestra").font(.headline)
                Spacer()
                if store.isRefreshing { ProgressView().controlSize(.small) }
            }.padding(14)
            Divider()
            if activeTasks.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "checkmark.circle").font(.title).foregroundStyle(.secondary)
                    Text("No active tasks").foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity).padding(.vertical, 28)
            } else {
                ForEach(activeGroups) { group in
                    Text(group.name)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 14).padding(.top, 10).padding(.bottom, 3)
                    ForEach(group.tasks) { task in
                        Button {
                            store.selectedID = task.id; store.selectedSection = .active; openWindow(id: "tasks")
                        } label: { TaskRow(task: task).padding(.horizontal, 12) }
                        .buttonStyle(.plain)
                    }
                }
            }
            HStack {
                Button("Open Orchestra Tasks") { openWindow(id: "tasks") }
                Spacer()
                Button { Task { await store.refresh() } } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.borderless)
                Button { NSApp.terminate(nil) } label: { Image(systemName: "power") }.buttonStyle(.borderless).help("Quit")
            }.padding(12)
        }.frame(width: 370)
    }
}
