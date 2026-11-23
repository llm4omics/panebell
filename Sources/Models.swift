import Foundation

struct Project: Codable, Equatable {
    let name: String
    let session: String
    let path: String
    func matches(_ other: Project) -> Bool { session == other.session && path == other.path }
}

struct Pane: Decodable {
    let key, session, path, name, target: String
    var project: Project { Project(name: name, session: session, path: path) }
}

struct Notice: Codable {
    let id, state, name, summary, session, path, pane: String
    var project: Project? { session.isEmpty || path.isEmpty ? nil : Project(name: name, session: session, path: path) }
}

struct Snapshot: Decodable {
    let events: [Notice]
    let panes: [Pane]
}

struct Settings: Decodable {
    var sshHost: String?
    var projects: [Project]?
}

struct OpenResult: Decodable {
    let choices: [Pane]?
    let pane: Pane?
}

struct LocalState: Codable {
    var recent: [Project] = []
    var pinned: [Project] = []
    var cached: [Notice] = []
    var pendingReads: [String] = []
    var client = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased().prefix(16))
    mutating func visit(_ project: Project) {
        recent.removeAll(where: project.matches)
        recent.insert(project, at: 0)
        recent = Array(recent.prefix(8))
    }
    mutating func pin(_ p: Project) {
        if pinned.contains(where: p.matches) { pinned.removeAll(where: p.matches) } else { pinned.append(p) }
    }
    mutating func read(_ n: Notice) {
        guard n.state == "complete" else { return }
        cached.removeAll { $0.id == n.id }
        if !pendingReads.contains(n.id) { pendingReads.append(n.id) }
    }
}
