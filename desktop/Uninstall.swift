import Foundation

// Keep locks open from preview through the reversible filesystem operation.
// Never follow a runtime symlink or remove the whole workbench home.
final class UninstallPlan {
    let runtime: URL
    private var descriptors: [Int32] = []
    deinit { for fd in descriptors { close(fd) } }

    init(home: URL, state: URL) throws {
        let fm = FileManager.default
        runtime = home.appendingPathComponent("runtime").standardizedFileURL
        guard runtime.resolvingSymlinksInPath() == runtime,
              fm.fileExists(atPath: runtime.path),
              runtime.pathComponents.count > 3 else {
            throw SetupFailure(message: "找不到可安全辨識的工具資料夾，或路徑含符號連結；未移動任何檔案。")
        }
        for path in [runtime.appendingPathComponent("desktop.lock"), runtime.appendingPathComponent("install.lock"), runtime.appendingPathComponent("usage.lock"), state.appendingPathComponent("server.lock")] {
            if path.resolvingSymlinksInPath() != path { throw SetupFailure(message: "鎖定檔含符號連結，請先檢查安裝位置。") }
            if path.deletingLastPathComponent() == state && !fm.fileExists(atPath: state.path) { continue }
            let fd = open(path.path, O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
            guard fd >= 0 else { throw SetupFailure(message: "無法鎖定安裝位置，未移動工具。") }
            descriptors.append(fd)
            guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
                throw SetupFailure(message: "工作台或安裝仍在執行。請先結束所有 SAR 工作台，再重新開啟 App 解除安裝。")
            }
        }
        let owner = runtime.appendingPathComponent("workbench-owner.json")
        var recognized = false
        if let data = try? Data(contentsOf: owner), let value = try? JSONSerialization.jsonObject(with: data) as? [String:String] {
            recognized = value["product"] == "sar-workbench" && value["runtime"] == runtime.path
        }
        // Migration for beta 1: require its bootstrap provenance and a prefix
        // inside this runtime, rather than adopting an arbitrary Conda folder.
        if !recognized, let data = try? Data(contentsOf: runtime.appendingPathComponent("bootstrap-ready.json")),
           let value = try? JSONSerialization.jsonObject(with: data) as? [String:String],
           let prefix = value["prefix"], value["sha256"]?.count == 64 {
            let p = URL(fileURLWithPath: prefix).standardizedFileURL
            recognized = p.deletingLastPathComponent() == runtime.appendingPathComponent("bootstrap") && p.lastPathComponent.hasPrefix("miniforge-") && p.resolvingSymlinksInPath() == p
        }
        guard recognized else { throw SetupFailure(message: "這個 runtime 沒有工作台安裝紀錄，為保護既有環境，未移動任何檔案。") }
        let allowed: Set<String> = ["workbench-owner.json", "bootstrap-ready.json", "desktop.lock", "install.lock", "usage.lock", "bootstrap", "bootstrap.log", "miniforge", "downloads", "download.log", "envs", "packages", "cache", "condarc", "reports", "ready.json", "install-state.json"]
        let children = try fm.contentsOfDirectory(at: runtime, includingPropertiesForKeys: nil)
        for child in children {
            let n = child.lastPathComponent
            guard allowed.contains(n) || (n.hasPrefix("install-") && n.hasSuffix(".log")) || n == ".DS_Store" else {
                throw SetupFailure(message: "工具資料夾含非預期項目 \(n)，請先確認是否有研究資料；未移動工具。")
            }
        }
        let registry = state.appendingPathComponent("projects.json")
        if fm.fileExists(atPath: registry.path) {
            let value = try JSONSerialization.jsonObject(with: Data(contentsOf: registry))
            guard let projects = value as? [String:String] else { throw SetupFailure(message: "無法讀取專案索引；請先檢查，未移動工具。") }
            for (_, path) in projects {
                let p = URL(fileURLWithPath: path).resolvingSymlinksInPath().path
                if p == runtime.path || p.hasPrefix(runtime.path + "/") {
                    throw SetupFailure(message: "有專案放在 runtime 裡：\(p)。請先移出研究資料，未移動工具。")
                }
                let meta = URL(fileURLWithPath: path).appendingPathComponent(".helper/state.json")
                if let data = try? Data(contentsOf: meta), let s = try? JSONSerialization.jsonObject(with: data) as? [String:Any], s["status"] as? String == "running" {
                    throw SetupFailure(message: "專案仍標示運算中，請先回到工作台檢查並停止工作。")
                }
                let analyses = URL(fileURLWithPath: path).appendingPathComponent("analyses")
                if let entries = try? fm.contentsOfDirectory(at: analyses, includingPropertiesForKeys: nil) {
                    for entry in entries {
                        if let pid = try? String(contentsOf: entry.appendingPathComponent("worker.pid"), encoding: .utf8), let id = Int32(pid.trimmingCharacters(in: .whitespacesAndNewlines)), id > 0, kill(id, 0) == 0 {
                            throw SetupFailure(message: "仍有背景分析程序，請先停止工作後再解除安裝。")
                        }
                    }
                }
            }
        }
    }
}
