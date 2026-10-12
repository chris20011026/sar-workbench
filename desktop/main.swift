import AppKit
import Foundation

let resources = Bundle.main.resourceURL!.appendingPathComponent("workbench")
let arguments = CommandLine.arguments
func argument(_ name: String) -> String? {
    guard let i = arguments.firstIndex(of: name), arguments.count > i + 1 else { return nil }
    return arguments[i + 1]
}
let initialHome = URL(fileURLWithPath: argument("--home") ?? ProcessInfo.processInfo.environment["SAR_WORKBENCH_HOME"] ?? UserDefaults.standard.string(forKey: "workbenchHome") ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("sar-workbench").path)

// CI uses the exact native bootstrap shipped to users, with PATH containing
// only macOS utilities. No Homebrew, Python or pre-existing Conda is needed.
if arguments.contains("--bootstrap-only") {
    let bootstrap = Bootstrap(home: initialHome, resources: resources)
    bootstrap.report = { print($0) }
    do { try bootstrap.prepare(); print(bootstrap.python.path); exit(0) }
    catch { fputs(error.localizedDescription + "\n", stderr); exit(1) }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var text: NSTextField!
    var location: NSTextField!
    var button: NSButton!
    var choose: NSButton!
    var uninstall: NSButton!
    var progress: NSProgressIndicator!
    var bootstrap: Bootstrap!
    var home = initialHome
    var task: Process?
    var running = false
    var url: URL?
    var activity: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu(); let item = NSMenuItem(); menu.addItem(item)
        let appMenu = NSMenu(); appMenu.addItem(withTitle: "結束 SAR 工作台", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu; NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 640, height: 510),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "SAR 研究工作台"; window.center(); window.delegate = self
        let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 36),
                                     stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -36),
                                     stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 36)])
        let title = NSTextField(labelWithString: "從這裡，開始 SAR 研究。")
        title.font = .systemFont(ofSize: 27, weight: .semibold); stack.addArrangedSubview(title)
        text = NSTextField(wrappingLabelWithString: "第一次開啟會下載專用的啟動工具，再以瀏覽器引導你安裝科學運算環境。無須預先安裝 Python 或 Conda。需要網路及至少 12 GB 可用空間。")
        text.font = .systemFont(ofSize: 15); stack.addArrangedSubview(text)
        location = NSTextField(wrappingLabelWithString: "工具存放位置：\(home.path)")
        location.textColor = .secondaryLabelColor; stack.addArrangedSubview(location)
        choose = NSButton(title: "選擇位置…", target: self, action: #selector(changeLocation)); stack.addArrangedSubview(choose)
        progress = NSProgressIndicator(); progress.style = .bar; progress.isIndeterminate = true
        progress.isDisplayedWhenStopped = false; progress.widthAnchor.constraint(equalToConstant: 560).isActive = true
        stack.addArrangedSubview(progress)
        button = NSButton(title: "準備並開啟工作台", target: self, action: #selector(start))
        button.bezelStyle = .rounded; button.keyEquivalent = "\r"; stack.addArrangedSubview(button)
        uninstall = NSButton(title: "解除安裝運算工具…", target: self, action: #selector(removeTools)); stack.addArrangedSubview(uninstall)
        let note = NSTextField(wrappingLabelWithString: "程式在本機運作。按上方按鈕開啟瀏覽器。解除安裝只移動專用工具與快取，保留研究資料；運作中請先結束 App 再重新開啟。")
        note.textColor = .secondaryLabelColor; stack.addArrangedSubview(note)
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }

    @objc func removeTools() {
        guard !running else { return }
        do {
            let state = URL(fileURLWithPath: argument("--state-dir") ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/share/alosstack-helper").path)
            let plan = try UninstallPlan(home: home, state: state)
            let alert = NSAlert(); alert.messageText = "解除安裝工作台的運算工具？"
            alert.informativeText = "將移至垃圾桶：\n\(plan.runtime.path)\n\n包含專用 ISCE2／MintPy／SNAPHU、啟動環境、下載快取與驗證紀錄。\n\n保留專案、匯入資料、DEM／WBD、分析成果與外部 conda 環境。使用這組工具的專案需重新安裝後才能繼續运算。請先停止自行在終端機使用這組專用環境的工作。\n\n清空垃圾桶前仍可復原。"
            alert.addButton(withTitle: "取消"); alert.addButton(withTitle: "移至垃圾桶")
            guard alert.runModal() == .alertSecondButtonReturn else { return }
            var destination: NSURL?
            try FileManager.default.trashItem(at: plan.runtime, resultingItemURL: &destination)
            withExtendedLifetime(plan) {}
            text.stringValue = "運算工具已移至垃圾桶，研究資料保留。可按「準備並開啟工作台」重新安裝；若不再使用，可把此 App 移到垃圾桶。清空垃圾桶才會釋放空間。"
        } catch { text.stringValue = error.localizedDescription }
    }

    @objc func changeLocation() {
        let picker = NSOpenPanel(); picker.canChooseDirectories = true; picker.canChooseFiles = false
        picker.canCreateDirectories = true; picker.message = "選擇父資料夾，將在其中建立 sar-workbench。路徑請使用英文、數字或底線。"
        if picker.runModal() == .OK, let folder = picker.url {
            home = folder.appendingPathComponent("sar-workbench")
            UserDefaults.standard.set(home.path, forKey: "workbenchHome")
            location.stringValue = "工具存放位置：\(home.path)"
        }
    }

    @objc func start() {
        if let url = url { NSWorkspace.shared.open(url); return }
        guard !running else { return }
        running = true; button.isEnabled = false; choose.isEnabled = false; uninstall.isEnabled = false; progress.startAnimation(nil)
        activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled], reason: "準備 SAR 工作台")
        bootstrap = Bootstrap(home: home, resources: resources)
        bootstrap.report = { message in DispatchQueue.main.async { self.text.stringValue = message } }
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try self.bootstrap.prepare()
                try self.startServer()
            } catch {
                DispatchQueue.main.async {
                    self.text.stringValue = error.localizedDescription
                    self.running = false; self.button.title = "重試"; self.button.isEnabled = true
                    self.choose.isEnabled = true; self.uninstall.isEnabled = true; self.progress.stopAnimation(nil); self.bootstrap = nil
                    if let activity = self.activity { ProcessInfo.processInfo.endActivity(activity); self.activity = nil }
                }
            }
        }
    }

    func startServer() throws {
        let session = home.appendingPathComponent("desktop-session.json")
        let log = home.appendingPathComponent("desktop.log")
        // Server creates a fresh handshake; stale files cannot open another port.
        if FileManager.default.fileExists(atPath: session.path) { try FileManager.default.removeItem(at: session) }
        FileManager.default.createFile(atPath: log.path, contents: nil)
        let output = try FileHandle(forWritingTo: log)
        let child = Process(); child.executableURL = bootstrap.python
        child.environment = bootstrap.cleanEnvironment()
        let port = argument("--port") ?? "8765"
        child.arguments = ["-s", "-m", "alosstack_helper.server", "--no-browser", "--port", port,
                           "--desktop-session", session.path]
        if let state = argument("--state-dir") { child.arguments! += ["--state-dir", state] }
        child.standardOutput = output; child.standardError = output
        try child.run(); task = child
        for _ in 0..<100 {
            if !child.isRunning { break }
            if let data = try? Data(contentsOf: session),
               let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let address = value["url"] as? String, let readyURL = URL(string: address),
               value["pid"] as? Int == Int(child.processIdentifier) {
                DispatchQueue.main.async {
                    self.url = readyURL; self.text.stringValue = "工作台已開啟。請在瀏覽器完成初次設定；執行運算時請保持此 App 開啟。"
                    self.button.title = "開啟工作台"; self.button.isEnabled = true; self.progress.stopAnimation(nil)
                    NSWorkspace.shared.open(readyURL)
                }
                return
            }
            Thread.sleep(forTimeInterval: 0.1)
        }
        if child.isRunning { child.terminate() }
        throw SetupFailure(message: "工作台無法啟動，可能已有另一個工作台使用 8765 連接埠。請先關閉舊服務後重試。紀錄：\(log.path)")
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if running && url == nil {
            let alert = NSAlert(); alert.messageText = "啟動工具仍在準備中"
            alert.informativeText = "請等此步驟完成後再結束，以免中斷安裝。若網路失敗，畫面會顯示重試方式。"
            alert.addButton(withTitle: "繼續等待"); alert.runModal()
            return .terminateCancel
        }
        if running {
            let alert = NSAlert(); alert.messageText = "結束工作台？"
            alert.informativeText = "請先確認網頁中的安裝與運算都已完成。結束 App 會停止本機服務；如果仍有工作，請先在網頁停止該工作。"
            alert.addButton(withTitle: "繼續使用"); alert.addButton(withTitle: "結束工作台")
            if alert.runModal() != .alertSecondButtonReturn { return .terminateCancel }
        }
        task?.terminate()
        if let activity = activity { ProcessInfo.processInfo.endActivity(activity) }
        return .terminateNow
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }
}

let app = NSApplication.shared
let delegate = AppDelegate(); app.delegate = delegate
app.run()
