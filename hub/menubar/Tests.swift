import Foundation

@main
enum OrchestraTasksTests {
    static func main() async throws {
        let payload = #"{"id":"task-1","short_id":"A1","label":"Native app","repository":"/tmp/Orchestra","preparation_status":"adopted","disposition":"open","stage":"implementation","status":"active","summary":"Phase 2/3 · implementation","blocker":"","next_action":"Continue","updated_at":"2026-08-12T10:00:00Z","stop_requested_at":null,"capabilities":{"edit":false,"note":true,"archive":false,"restore_archive":false,"trash":false,"restore_trash":false,"request_stop":true,"withdraw_stop":false,"reopen":false,"purge":false}}"#.data(using: .utf8)!
        let task = try JSONDecoder().decode(OrchestraTask.self, from: payload)
        precondition(task.displayID == "A1")
        precondition(task.title == "Native app")
        precondition(task.repositoryName == "Orchestra")
        precondition(task.capabilities.requestStop)
        precondition(TaskSection.active.contains(task))
        precondition(!TaskSection.archived.contains(task))

        let observationPayload = #"{"id":"task-1","short_id":"A1","label":"Wrong title","repository":"/tmp/Orchestra","worktree":"/tmp/worktrees/a-very-long-worktree-name","branch":"orchestra/native-app","preparation_status":"active","disposition":"open","stage":"review","status":"active","summary":"Review 1","updated_at":"2026-08-12T10:05:00Z","stop_requested_at":"wrong","stale":true,"initiative":{"id":"initiative-1","title":"Desktop","brief":"Native clients"},"capabilities":{"purge":true}}"#.data(using: .utf8)!
        let observation = try JSONDecoder().decode(OrchestraTask.self, from: observationPayload)
        var merged = task
        merged.mergeObservation(observation)
        precondition(merged.title == "Native app")
        precondition(merged.summary == "Review 1")
        precondition(merged.worktree == "/tmp/worktrees/a-very-long-worktree-name")
        precondition(merged.worktreeLabel == "a-very-…ree-name")
        precondition(merged.branch == "orchestra/native-app")
        precondition(merged.stale)
        precondition(merged.initiative?.title == "Desktop")
        precondition(merged.stopRequestedAt == nil)
        precondition(merged.capabilities.requestStop)
        precondition(!merged.capabilities.purge)

        let trashedPayload = #"{"id":"task-2","short_id":"A2","title":"Discardable","preparation_status":"draft","disposition":"trashed","updated_at":"2026-08-12T10:00:00Z","capabilities":{"purge":true}}"#.data(using: .utf8)!
        let trashed = try JSONDecoder().decode(OrchestraTask.self, from: trashedPayload)
        precondition(TaskSection.trash.contains(trashed))
        precondition(trashed.effectiveStatus == "trashed")
        precondition(trashed.capabilities.purge)

        let cancelledPayload = #"{"id":"task-3","short_id":"A3","title":"Paused","preparation_status":"cancelled","disposition":"open","status":"active","updated_at":"2026-08-12T10:00:00Z"}"#.data(using: .utf8)!
        let cancelled = try JSONDecoder().decode(OrchestraTask.self, from: cancelledPayload)
        precondition(cancelled.effectiveStatus == "cancelled")

        let legacyPayload = #"{"id":"legacy-uuid","title":"Legacy"}"#.data(using: .utf8)!
        let legacy = try JSONDecoder().decode(OrchestraTask.self, from: legacyPayload)
        precondition(legacy.shortID == nil)
        precondition(legacy.displayID.isEmpty)
        precondition(legacy.id == "legacy-uuid")

        let mainCheckoutPayload = #"{"id":"task-main","label":"Main checkout","repository":"/tmp/Orchestra","worktree":"/tmp/Orchestra"}"#.data(using: .utf8)!
        let mainCheckout = try JSONDecoder().decode(OrchestraTask.self, from: mainCheckoutPayload)
        precondition(mainCheckout.worktreeLabel == nil)
        let groups = repositoryTaskGroups(
            [merged, mainCheckout],
            names: ["/tmp/Orchestra": "NeniTPV"]
        )
        precondition(groups.count == 1)
        precondition(groups[0].name == "NeniTPV")
        precondition(groups[0].tasks.count == 2)
        let noRepositoryGroups = repositoryTaskGroups(
            [legacy],
            names: ["": "Unlocalized server label"]
        )
        precondition(noRepositoryGroups[0].name == legacy.repositoryName)
        precondition(noRepositoryGroups[0].name != "Unlocalized server label")

        let warningPayload = #"{"status":"ok","warning":"Private documents remain"}"#.data(using: .utf8)!
        let warning = try JSONDecoder().decode(ControlResponse.self, from: warningPayload)
        precondition(warning.warning == "Private documents remain")

        let files = FileManager.default
        let temporary = files.temporaryDirectory.appendingPathComponent(
            "orchestra-task-client-tests-\(UUID().uuidString)",
            isDirectory: true
        )
        try files.createDirectory(at: temporary, withIntermediateDirectories: true)
        defer { try? files.removeItem(at: temporary) }
        let helper = temporary.appendingPathComponent("large_response.py")
        let helperBody = """
        import json
        import sys

        sys.stderr.write("warning" * 20000)
        sys.stderr.flush()
        tasks = [{"id": f"task-{index}", "brief": "x" * 8192} for index in range(24)]
        json.dump({"status": "ok", "tasks": tasks}, sys.stdout)
        """
        try helperBody.write(to: helper, atomically: true, encoding: .utf8)
        let missing = temporary.appendingPathComponent("missing.py")
        let installed = try TaskControlClient.resolveHelper(shared: helper, bundled: missing, explicitlyConfigured: false)
        precondition(installed == helper)
        let fallback = try TaskControlClient.resolveHelper(shared: missing, bundled: helper, explicitlyConfigured: false)
        precondition(fallback == helper)
        do {
            _ = try TaskControlClient.resolveHelper(shared: missing, bundled: helper, explicitlyConfigured: true)
            preconditionFailure("Explicit missing helper must block")
        } catch ClientError.helperMissing {}
        let client = TaskControlClient(
            helper: helper,
            python: URL(fileURLWithPath: "/usr/bin/python3")
        )
        let largeResponse = try await client.list()
        precondition(largeResponse.count == 24)
        print("Orchestra Tasks model tests passed")
    }
}
