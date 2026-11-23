import AppKit
import UserNotifications
import Foundation

final class Action: NSObject {
    let run: () -> Void
    init(_ run: @escaping () -> Void) { self.run = run }
}

final class Menu: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var status: NSStatusItem!
    private var settings = Settings()
    private var state = LocalState()
    private var panes: [Pane] = []
    private var problem: String?
    private var syncing = false
    private var validConfig = false
    private var focusGeneration = 0
    private var sendingReads = Set<String>()
    private var tracking = false
    private var timer: Timer?
    private var demoWindow: NSWindow?
    private let demo: Bool
    private let prefs = UserDefaults.standard
    private let configURL: URL = ProcessInfo.processInfo.environment["PANEBELL_CONFIG"].map { URL(fileURLWithPath: $0) }
        ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/panebell/config.json")
    private let helper = Bundle.main.resourceURL?.appendingPathComponent("panebell.py")
    private var stateKey: String { "PaneBell.v1." + (settings.sshHost ?? "") }
    init(demo: Bool) { self.demo = demo }

    func applicationDidFinishLaunching(_ notification: Notification) {
        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if demo {
            let notice = Notice(id: "sample-run", state: "waiting", name: "Paper Planes", summary: "Check the fictional colour list for duplicate entries?", session: "", path: "", pane: "")
            state.cached = [notice]
        } else {
            do {
                settings = try JSONDecoder().decode(Settings.self, from: Data(contentsOf: configURL))
                validConfig = true
            } catch { problem = "Config: " + error.localizedDescription }
            if validConfig, let data = prefs.data(forKey: stateKey), let saved = try? JSONDecoder().decode(LocalState.self, from: data) {
                state = saved
            }
            persist()
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert]) { _, _ in }
            timer = Timer.scheduledTimer(withTimeInterval: 8, repeats: true) { [weak self] _ in self?.refresh() }
        }
        render()
        refresh()
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard !demo, validConfig else { return }
        for url in urls where url.scheme == "panebell" && url.host == "focus" {
            let parts = url.pathComponents
            guard parts.count == 3, parts[1] == state.client else { continue }
            focusGeneration += 1
            let generation = focusGeneration
            request(["action":"focused","client":parts[1],"session":parts[2]]) { result in
                guard generation == self.focusGeneration, let data = try? result.get(),
                      let response = try? JSONDecoder().decode(OpenResult.self, from: data),
                      let pane = response.pane else { return }
                self.state.visit(pane.project); self.persist(); self.render()
            }
        }
    }

    private func persist() {
        if !demo && validConfig, let data = try? JSONEncoder().encode(state) { prefs.set(data, forKey: stateKey) }
    }

    private func request(_ args: [String: String], then: @escaping (Result<Data, NSError>) -> Void) {
        guard let helper else { return }
        DispatchQueue.global(qos: .utility).async {
            do {
                let task = Process(), input = Pipe(), output = Pipe()
                let errorURL = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                guard FileManager.default.createFile(atPath: errorURL.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
                    throw CocoaError(.fileWriteUnknown)
                }
                defer { try? FileManager.default.removeItem(at: errorURL) }
                let errors = try FileHandle(forWritingTo: errorURL)
                defer { try? errors.close() }
                task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                task.arguments = [helper.path, "request"]
                task.standardInput = input
                task.standardOutput = output
                task.standardError = errors
                try task.run()
                input.fileHandleForWriting.write(try JSONEncoder().encode(args))
                input.fileHandleForWriting.closeFile()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                task.waitUntilExit()
                if task.terminationStatus != 0 {
                    throw NSError(domain: "PaneBell", code: Int(task.terminationStatus),
                                  userInfo: [NSLocalizedDescriptionKey: String(decoding: (try Data(contentsOf: errorURL)).prefix(2000), as: UTF8.self)])
                }
                DispatchQueue.main.async { then(.success(data)) }
            } catch { let err = error as NSError; DispatchQueue.main.async { then(.failure(err)) } }
        }
    }

    private func refresh() {
        guard !demo, validConfig, !syncing else { return }
        syncing = true
        request(["action": "snapshot"]) { result in
            defer { self.syncing = false; self.render() }
            do {
                let snap = try JSONDecoder().decode(Snapshot.self, from: result.get())
                let previous = Set(self.state.cached.map { $0.state + "/" + $0.id })
                self.panes = snap.panes
                self.state.cached = snap.events.filter { !self.state.pendingReads.contains($0.id) }
                self.problem = nil
                self.persist()
                let fresh = self.state.cached.filter { !previous.contains($0.state + "/" + $0.id) }
                if let first = fresh.first {
                    let note = UNMutableNotificationContent()
                    note.title = fresh.count == 1 ? first.name : "PaneBell · \(fresh.count) updates"
                    note.body = first.state == "waiting" ? "Waiting for your attention" : "Completed · open PaneBell to read"
                    let banner = UNNotificationRequest(identifier: first.state + "/" + first.id, content: note, trigger: nil)
                    UNUserNotificationCenter.current().add(banner, withCompletionHandler: nil)
                }
                for id in self.state.pendingReads where self.sendingReads.insert(id).inserted {
                    self.request(["action":"read", "id":id]) { answer in
                        self.sendingReads.remove(id)
                        if case .success = answer { self.state.pendingReads.removeAll { $0 == id }; self.persist() }
                    }
                }
            } catch { self.problem = error.localizedDescription }
        }
    }

    private func item(_ title: String, _ menu: NSMenu, _ run: @escaping () -> Void) {
        let entry = NSMenuItem(title: title, action: #selector(performItem(_:)), keyEquivalent: "")
        entry.target = self
        entry.representedObject = Action(run)
        menu.addItem(entry)
    }
    @objc private func performItem(_ sender: NSMenuItem) { (sender.representedObject as? Action)?.run() }
    private func sub(_ title: String, _ parent: NSMenu) -> NSMenu {
        let m = NSMenu(title: title)
        let entry = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        entry.submenu = m
        parent.addItem(entry)
        return m
    }
    func menuWillOpen(_ menu: NSMenu) { tracking = true }
    func menuDidClose(_ menu: NSMenu) { tracking = false; DispatchQueue.main.async { self.render() } }

    private func render() {
        guard let button = status?.button else { return }
        let count = state.cached.count
        button.title = (demo ? "PaneBell Demo" : "PaneBell") + (count > 0 ? " · \(count)" : "") + (problem == nil ? "" : " !")
        button.contentTintColor = count > 0 ? .systemOrange : nil
        guard !tracking else { return }
        let menu = NSMenu()
        menu.delegate = self
        if let problem { item("Connection or settings…", menu) { self.message("PaneBell", problem) } }
        for (kind,label) in [("waiting","Waiting for you"),("complete","Unread completions")] {
            let notices = state.cached.filter { $0.state == kind }
            if !notices.isEmpty {
                let group = sub("\(label) (\(notices.count))", menu)
                for notice in notices { item(notice.name, group) { self.inspect(notice) } }
            }
        }
        if demo {
            item("Open Paper Planes", menu) { self.showDemo() }
        } else {
            for (title, projects) in [("Pinned",state.pinned),("Recently opened",state.recent),("Projects",settings.projects ?? [])] {
                let group = sub(title, menu)
                if projects.isEmpty { group.addItem(withTitle: "None yet", action: nil, keyEquivalent: "") }
                for p in projects { self.projectEntry(p, nil, group) }
            }
            let live = sub("Live windows (\(panes.count))", menu)
            for pane in panes { self.projectEntry(pane.project, pane, live) }
            menu.addItem(.separator())
            item("Refresh", menu) { self.refresh() }
            item("Open settings…", menu) { NSWorkspace.shared.open(self.configURL) }
        }
        item("Quit", menu) { NSApp.terminate(nil) }
        status.menu = menu
    }

    private func projectEntry(_ p: Project, _ pane: Pane?, _ parent: NSMenu) {
        let group = sub(p.name + " · " + (pane?.target ?? p.session), parent)
        group.addItem(withTitle: p.path, action: nil, keyEquivalent: "")
        item("Open window", group) { self.open(p, exact: pane?.key) }
        item("Open side by side", group) { self.open(p, exact: pane?.key, separate: true) }
        item(state.pinned.contains(where: p.matches) ? "Unpin" : "Pin to menu", group) {
            self.state.pin(p); self.persist(); self.render()
        }
        let actions = (settings.sshHost ?? "").isEmpty ? [("Reveal in Finder","finder"),("Open in VS Code","editor")] : [("Open in VS Code","editor")]
        for (label, action) in actions {
            item(label, group) {
                self.request(["action":action, "path":p.path]) { result in
                    switch result {
                    case .success: self.state.visit(p); self.persist(); self.render()
                    case .failure(let error): self.message(label, error.localizedDescription)
                    }
                }
            }
        }
        item("Copy project path", group) {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(p.path, forType: .string)
        }
    }

    private func message(_ title: String, _ text: String) {
        let alert = NSAlert(); alert.messageText = title; alert.informativeText = text
        NSApp.activate(ignoringOtherApps: true); alert.runModal()
    }

    private func open(_ p: Project, exact: String? = nil, choice: String? = nil, separate: Bool = false,
                      client: String? = nil, canUnlock: Bool = true) {
        let client = client ?? (separate ? String(UUID().uuidString.lowercased().prefix(8)) : state.client)
        var args = ["action":"open", "client":client, "session":p.session, "path":p.path]
        args["exact"] = exact; args["choice"] = choice
        request(args) { result in
            do {
                let response = try JSONDecoder().decode(OpenResult.self, from: result.get())
                if let choices = response.choices, !choices.isEmpty {
                    let alert = NSAlert(), picker = NSPopUpButton(frame: NSRect(x:0,y:0,width:370,height:30))
                    for pane in choices { picker.addItem(withTitle: pane.target + " · " + pane.name); picker.lastItem?.toolTip = pane.path }
                    alert.messageText = "Choose a window for " + p.name
                    alert.accessoryView = picker; alert.addButton(withTitle:"Open"); alert.addButton(withTitle:"Cancel")
                    NSApp.activate(ignoringOtherApps: true)
                    if alert.runModal() == .alertFirstButtonReturn {
                        self.open(p, choice: choices[picker.indexOfSelectedItem].key, client: client, canUnlock: canUnlock)
                    }
                } else if let pane = response.pane {
                    self.request(["action":"terminal","client":client,"key":pane.key,"session":pane.session]) { answer in
                        switch answer {
                        case .success: self.state.visit(p); self.persist(); self.render()
                        case .failure(let err): self.message("Terminal could not open", err.localizedDescription)
                        }
                    }
                }
            } catch let err as NSError {
                if canUnlock && err.code == 255 {
                    let alert = NSAlert(); alert.messageText = "SSH connection failed"; alert.informativeText = err.localizedDescription
                    alert.addButton(withTitle: "Log in and retry"); alert.addButton(withTitle: "Cancel")
                    NSApp.activate(ignoringOtherApps: true)
                    if alert.runModal() == .alertFirstButtonReturn {
                        self.request(["action":"terminal-unlock"]) { reply in
                            switch reply {
                            case .success: self.open(p, exact: exact, choice: choice, client: client, canUnlock: false)
                            case .failure(let error): self.message("SSH login", error.localizedDescription)
                            }
                        }
                    }
                } else { self.message("Could not open project", err.localizedDescription) }
            }
        }
    }

    private func inspect(_ n: Notice) {
        if demo && n.state == "waiting" { showDemo(); return }
        let alert = NSAlert(); alert.messageText = n.name; alert.informativeText = n.summary
        if n.state == "complete" { alert.addButton(withTitle: "Mark as read") }
        if n.project != nil { alert.addButton(withTitle: n.pane.isEmpty ? "Open project" : "Open original window") }
        alert.addButton(withTitle: "Close")
        NSApp.activate(ignoringOtherApps: true)
        let index = alert.runModal().rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
        if n.state == "complete", index == 0 { state.read(n); persist(); render(); refresh() }
        else if let project = n.project, index == (n.state == "complete" ? 1 : 0) {
            open(project, exact: n.pane.isEmpty ? nil : n.pane)
        }
    }

    private func showDemo() {
        if let w = demoWindow { NSApp.activate(ignoringOtherApps:true); w.makeKeyAndOrderFront(nil); return }
        let w = NSWindow(contentRect: NSRect(x:0,y:0,width:420,height:145), styleMask:[.titled,.closable], backing:.buffered, defer:false)
        w.title = "Paper Planes · Demo"; w.isReleasedWhenClosed = false
        let label = NSTextField(wrappingLabelWithString:"Colour list: teal, amber, teal. Allow the duplicate check?")
        label.frame = NSRect(x:20,y:62,width:380,height:60)
        let b = NSButton(title:"Allow check",target:self,action:#selector(approveDemo(_:)))
        b.frame = NSRect(x:20,y:15,width:130,height:32)
        w.contentView?.addSubview(label); w.contentView?.addSubview(b)
        demoWindow = w; w.center(); NSApp.activate(ignoringOtherApps:true); w.makeKeyAndOrderFront(nil)
    }
    @objc private func approveDemo(_ button: NSButton) {
        let colors = ["teal","amber","teal"]
        let summary = "\(colors.count) entries, \(colors.count - Set(colors).count) duplicate."
        state.cached = [Notice(id:"sample-run", state:"complete", name:"Paper Planes", summary:summary, session:"", path:"", pane:"")]
        button.isEnabled = false
        (demoWindow?.contentView?.subviews.compactMap { $0 as? NSTextField }.first)?.stringValue = summary
        render()
    }
    func testDemo() -> Bool {
        applicationDidFinishLaunching(Notification(name:NSApplication.didFinishLaunchingNotification))
        guard state.cached.first?.state == "waiting" else { return false }
        approveDemo(NSButton())
        guard let n = state.cached.first, n.summary == "3 entries, 1 duplicate.", status.menu?.items.first?.title == "Unread completions (1)" else { return false }
        state.read(n); render()
        return state.cached.isEmpty
    }
}

@main
struct Main {
    static func main() {
        let args = CommandLine.arguments
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let menu = Menu(demo: args.contains("--demo") || args.contains("--self-test"))
        if args.contains("--self-test") { exit(menu.testDemo() ? 0 : 1) }
        app.delegate = menu
        withExtendedLifetime(menu) { app.run() }
    }
}
