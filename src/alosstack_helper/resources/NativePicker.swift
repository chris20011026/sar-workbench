import AppKit
import Foundation
import Darwin

// Launched as an application by LaunchServices, never through AppleScript.
// Request/result files live in a private, per-request directory owned by the
// caller. No script, executable, or arbitrary response path is accepted.
struct PickerRequest: Decodable {
    let mode: String
    let start: String
    let token: String
    let parent_pid: Int32
}

func loadRequest(_ path: String) throws -> (PickerRequest, URL) {
    let url = URL(fileURLWithPath: path).standardizedFileURL
    let manager = FileManager.default
    let info = try manager.attributesOfItem(atPath: url.path)
    let parent = try manager.attributesOfItem(atPath: url.deletingLastPathComponent().path)
    guard url.lastPathComponent == "request.json", info[.type] as? FileAttributeType == .typeRegular,
          (info[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          (parent[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
          ((parent[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o077 == 0,
          ((info[.size] as? NSNumber)?.intValue ?? 0) < 65536 else {
        throw NSError(domain: "SARFilePicker", code: 1, userInfo: [NSLocalizedDescriptionKey: "Invalid private request file"])
    }
    let data = try Data(contentsOf: url)
    let request = try JSONDecoder().decode(PickerRequest.self, from: data)
    guard ["file", "dir", "newdir"].contains(request.mode), request.start.hasPrefix("/"),
          request.token.count == 64, request.token.allSatisfy({ $0.isHexDigit }), request.parent_pid > 1 else {
        throw NSError(domain: "SARFilePicker", code: 2, userInfo: [NSLocalizedDescriptionKey: "Invalid picker request"])
    }
    return (request, url)
}

final class PickerDelegate: NSObject, NSApplicationDelegate {
    let request: PickerRequest
    let requestURL: URL
    var panel: NSOpenPanel?
    var timer: Timer?
    var finished = false
    let began = Date()
    init(_ request: PickerRequest, _ url: URL) { self.request = request; self.requestURL = url }
    func write(_ name: String, _ values: [String: Any]) {
        var payload = values
        payload["token"] = request.token
        payload["pid"] = getpid()
        payload["parent_pid"] = request.parent_pid
        if let data = try? JSONSerialization.data(withJSONObject: payload) {
            try? data.write(to: requestURL.deletingLastPathComponent().appendingPathComponent(name), options: .atomic)
        }
    }
    func finish(_ values: [String: Any]) {
        guard !finished else { return }
        finished = true; timer?.invalidate()
        write("response.json", values)
        panel?.orderOut(nil)
        NSApp.terminate(nil)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu(), item = NSMenuItem(), submenu = NSMenu()
        submenu.addItem(withTitle: "取消選擇", action: #selector(cancel), keyEquivalent: "q")
        menu.addItem(item); item.submenu = submenu; NSApp.mainMenu = menu
        let picker = NSOpenPanel(); panel = picker
        picker.title = "SAR 研究工作台 · 選擇位置"
        picker.message = request.mode == "file" ? "選擇要使用的檔案" : "選擇要使用的資料夾"
        picker.prompt = "選擇"
        picker.canChooseFiles = request.mode == "file"
        picker.canChooseDirectories = request.mode != "file"
        picker.canCreateDirectories = request.mode != "file"
        picker.allowsMultipleSelection = false
        picker.directoryURL = URL(fileURLWithPath: request.start, isDirectory: true)
        picker.begin { [weak self] response in
            guard let self = self else { return }
            if response == .OK, let url = picker.url { self.finish(["cancelled": false, "path": url.path]) }
            else { self.finish(["cancelled": true]) }
        }
        picker.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            guard let self = self, !self.finished else { return }
            let manager = FileManager.default
            if !manager.fileExists(atPath: self.requestURL.path) ||
                manager.fileExists(atPath: self.requestURL.deletingLastPathComponent().appendingPathComponent("cancel").path) ||
                (kill(self.request.parent_pid, 0) != 0 && errno == ESRCH) {
                self.finish(["cancelled": true]); return
            }
            if Date().timeIntervalSince(self.began) > 600 { self.finish(["error": "原生選擇視窗等待逾時。"]); return }
            if picker.isVisible { self.write("ready.json", ["phase": "visible", "application_active": NSApp.isActive, "key_window": picker.isKeyWindow]) }
            else if Date().timeIntervalSince(self.began) > 10 { self.finish(["error": "macOS 未能顯示原生選擇視窗。"]) }
        }
    }
    @objc func cancel() { finish(["cancelled": true]) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if !finished { finished = true; write("response.json", ["cancelled": true]); timer?.invalidate() }
        return .terminateNow
    }
}

let arguments = CommandLine.arguments
guard arguments.count == 3, ["--request", "--check-request"].contains(arguments[1]) else {
    fputs("A private request is required.\n", stderr); exit(2)
}
do {
    let (request, url) = try loadRequest(arguments[2])
    if arguments[1] == "--check-request" { print("Request validated; no dialog opened."); exit(0) }
    let delegate = PickerDelegate(request, url)
    // The launch acknowledgment precedes AppKit so the caller can stop a helper
    // even if the desktop session prevents application initialization.
    delegate.write("ready.json", ["phase": "launching"])
    // LaunchServices apps are outside the caller's process group. This
    // background watchdog can exit our own app even if AppKit blocks before
    // presenting a window; the caller never needs to signal a PID from JSON.
    let watchdog = DispatchSource.makeTimerSource(queue: .global(qos: .utility))
    let deadline = Date().addingTimeInterval(605)
    watchdog.schedule(deadline: .now(), repeating: .milliseconds(250))
    watchdog.setEventHandler {
        let manager = FileManager.default
        if !manager.fileExists(atPath: url.path) ||
            manager.fileExists(atPath: url.deletingLastPathComponent().appendingPathComponent("cancel").path) ||
            Date() > deadline || (kill(request.parent_pid, 0) != 0 && errno == ESRCH) {
            _exit(0)
        }
    }
    watchdog.resume()
    let app = NSApplication.shared
    app.delegate = delegate
    withExtendedLifetime((watchdog, delegate)) { app.run() }
} catch {
    fputs("\(error.localizedDescription)\n", stderr); exit(2)
}
