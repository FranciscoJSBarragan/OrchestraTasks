import AppKit
import Foundation

enum ClientError: LocalizedError {
    case invalidResponse(String)
    case helperMissing
    var errorDescription: String? {
        switch self {
        case .invalidResponse(let reason): return reason
        case .helperMissing: return String(localized: "Task Control helper was not found")
        }
    }
}

struct HubClient {
    let port: Int
    func summary() async throws -> HubResponse {
        let url = URL(string: "http://127.0.0.1:\(port)/v1/summary")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw ClientError.invalidResponse(String(localized: "Hub is unavailable"))
        }
        return try JSONDecoder().decode(HubResponse.self, from: data)
    }
}

private final class ProcessCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var output = Data()
    private var errors = Data()

    func setOutput(_ data: Data) {
        lock.lock()
        output = data
        lock.unlock()
    }

    func setErrors(_ data: Data) {
        lock.lock()
        errors = data
        lock.unlock()
    }

    func values() -> (Data, Data) {
        lock.lock()
        defer { lock.unlock() }
        return (output, errors)
    }
}

struct TaskControlClient: Sendable {
    let helper: URL
    let python: URL

    static func installed() throws -> TaskControlClient {
        let files = FileManager.default
        let environment = ProcessInfo.processInfo.environment
        let sharedRoot = environment["ORCHESTRA_TASKS_HOME"].map {
            URL(fileURLWithPath: $0, isDirectory: true)
        } ?? files.homeDirectoryForCurrentUser.appendingPathComponent(".orchestra-tasks", isDirectory: true)
        let shared = sharedRoot.appendingPathComponent("scripts/task_control.py")
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("scripts/task_control.py")
        let helper = try resolveHelper(shared: shared, bundled: bundled,
                                       explicitlyConfigured: environment["ORCHESTRA_TASKS_HOME"] != nil)
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        guard let path = candidates.first(where: {
            files.isExecutableFile(atPath: $0) && supportsRequiredPython($0)
        }) else {
            throw ClientError.invalidResponse(String(localized: "Python 3.11 or newer is unavailable"))
        }
        return TaskControlClient(helper: helper, python: URL(fileURLWithPath: path))
    }

    static func resolveHelper(shared: URL, bundled: URL?, explicitlyConfigured: Bool) throws -> URL {
        let files = FileManager.default
        if explicitlyConfigured || files.fileExists(atPath: shared.path) {
            guard files.isReadableFile(atPath: shared.path) else { throw ClientError.helperMissing }
            return shared
        }
        guard let bundled, files.isReadableFile(atPath: bundled.path) else { throw ClientError.helperMissing }
        return bundled
    }

    private static func supportsRequiredPython(_ path: String) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = ["-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    func run(_ arguments: [String]) async throws -> ControlResponse {
        try await withCheckedThrowingContinuation { continuation in
            let process = Process()
            let output = Pipe()
            let errors = Pipe()
            let capture = ProcessCapture()
            let readers = DispatchGroup()
            let queue = DispatchQueue.global(qos: .utility)
            process.executableURL = python
            process.arguments = [helper.path] + arguments
            process.environment = {
                var environment = ProcessInfo.processInfo.environment
                environment["PYTHONDONTWRITEBYTECODE"] = "1"
                return environment
            }()
            process.standardOutput = output
            process.standardError = errors

            do {
                try process.run()
            } catch {
                continuation.resume(throwing: error)
                return
            }
            output.fileHandleForWriting.closeFile()
            errors.fileHandleForWriting.closeFile()

            readers.enter()
            queue.async {
                capture.setOutput(output.fileHandleForReading.readDataToEndOfFile())
                readers.leave()
            }
            readers.enter()
            queue.async {
                capture.setErrors(errors.fileHandleForReading.readDataToEndOfFile())
                readers.leave()
            }
            queue.async {
                process.waitUntilExit()
                readers.wait()
                let (data, errorData) = capture.values()
                do {
                    let response = try JSONDecoder().decode(ControlResponse.self, from: data)
                    guard process.terminationStatus == 0, response.status == "ok" else {
                        throw ClientError.invalidResponse(response.reason ?? String(localized: "Operation failed"))
                    }
                    continuation.resume(returning: response)
                } catch {
                    let detail = String(data: errorData, encoding: .utf8)
                    continuation.resume(throwing: detail?.isEmpty == false
                        ? ClientError.invalidResponse(detail!) : error)
                }
            }
        }
    }

    func list() async throws -> [OrchestraTask] {
        try await run(["task", "list", "--include-archived", "--include-trashed"]).tasks ?? []
    }
}

func readHubPort() -> Int {
    let files = FileManager.default
    let root = ProcessInfo.processInfo.environment["ORCHESTRA_HOME"].map {
        URL(fileURLWithPath: $0, isDirectory: true)
    } ?? files.homeDirectoryForCurrentUser.appendingPathComponent(".orchestra", isDirectory: true)
    let file = root.appendingPathComponent("hub.toml")
    guard let content = try? String(contentsOf: file, encoding: .utf8),
          let regex = try? NSRegularExpression(pattern: #"(?m)^port\s*=\s*(\d+)\s*$"#),
          let match = regex.firstMatch(in: content, range: NSRange(content.startIndex..., in: content)),
          let range = Range(match.range(at: 1), in: content),
          let port = Int(content[range]), (1...65535).contains(port) else { return 7343 }
    return port
}
