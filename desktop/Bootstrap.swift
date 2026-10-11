import Foundation
import CryptoKit

struct SetupFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

final class Bootstrap {
    let home: URL
    let resources: URL
    var report: (String) -> Void = { _ in }
    private var lockFD: Int32 = -1
    init(home: URL, resources: URL) { self.home = home; self.resources = resources }
    deinit { if lockFD >= 0 { close(lockFD) } }
    var runtime: URL { home.appendingPathComponent("runtime") }
    var python: URL { runtime.appendingPathComponent("miniforge/bin/python") }

    func cleanEnvironment() -> [String: String] {
        let prefixes = ["PYTHON", "CONDA", "MAMBA", "DYLD_", "ISCE", "GDAL", "PROJ", "FONTCONFIG", "MAGICK_", "_INSAR_ARM_"]
        var env = ProcessInfo.processInfo.environment.filter { key, _ in !prefixes.contains(where: key.hasPrefix) }
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["CONDA_REGISTER_ENVS"] = "false"
        env["CONDA_NUMBER_CHANNEL_NOTICES"] = "0"
        env["XDG_CACHE_HOME"] = runtime.appendingPathComponent("cache").path
        env["SAR_WORKBENCH_HOME"] = home.path
        env["PYTHONPATH"] = resources.appendingPathComponent("src").path
        return env
    }

    func acquireLock() throws {
        try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        lockFD = open(runtime.appendingPathComponent("desktop.lock").path, O_CREAT | O_RDWR, 0o600)
        guard lockFD >= 0 && flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
            throw SetupFailure(message: "另一個啟動器正在使用此資料夾。請回到原視窗。")
        }
    }

    func checkPlatform() throws {
        #if !arch(arm64)
        throw SetupFailure(message: "此安裝檔需要 Apple Silicon Mac。")
        #endif
        guard ProcessInfo.processInfo.isOperatingSystemAtLeast(OperatingSystemVersion(majorVersion: 14, minorVersion: 5, patchVersion: 0)) else {
            throw SetupFailure(message: "需要 macOS 14.5 或更新。")
        }
        guard home.path.range(of: "^/[A-Za-z0-9_./-]+$", options: .regularExpression) != nil else {
            throw SetupFailure(message: "請使用不含中文或空白的工具存放路徑。可按「選擇位置」更換；專案顯示名稱仍可使用中文。")
        }
    }

    func sha256(_ file: URL) throws -> String {
        let stream = try FileHandle(forReadingFrom: file)
        defer { try? stream.close() }
        var hash = SHA256()
        while let chunk = try stream.read(upToCount: 1_048_576), !chunk.isEmpty { hash.update(data: chunk) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    func prepare() throws {
        try checkPlatform()
        if lockFD < 0 { try acquireLock() }
        let manifestURL = resources.appendingPathComponent("src/alosstack_helper/resources/runtime.json")
        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL)) as! [String: Any]
        let mini = manifest["miniforge"] as! [String: String]
        let marker = runtime.appendingPathComponent("bootstrap-ready.json")
        if FileManager.default.isExecutableFile(atPath: python.path),
           let data = try? Data(contentsOf: marker),
           let old = try? JSONSerialization.jsonObject(with: data) as? [String: String],
           old["sha256"] == mini["sha256"] { return }
        if FileManager.default.fileExists(atPath: runtime.appendingPathComponent("miniforge").path) {
            throw SetupFailure(message: "工具資料夾已存在但未完成驗證。請選一個新的存放位置；既有資料會保留。")
        }
        let free = try FileManager.default.attributesOfFileSystem(forPath: runtime.path)[.systemFreeSize] as? NSNumber
        guard (free?.uint64Value ?? 0) >= 12 * 1024 * 1024 * 1024 else {
            throw SetupFailure(message: "可用空間不足，安裝工具與快取至少需要 12 GB。研究資料另外計算。")
        }
        let cache = runtime.appendingPathComponent("downloads")
        try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
        let installer = cache.appendingPathComponent("Miniforge-\(mini["version"]!)-arm64.sh")
        if (try? sha256(installer)) != mini["sha256"] {
            report("正在下載啟動工具。首次使用需要網路，請稍候…")
            let file = cache.appendingPathComponent(UUID().uuidString + ".download")
            let downloadLog = runtime.appendingPathComponent("download.log")
            FileManager.default.createFile(atPath: downloadLog.path, contents: nil)
            let output = try FileHandle(forWritingTo: downloadLog)
            let download = Process()
            download.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
            download.arguments = ["--fail", "--location", "--retry", "2", "--connect-timeout", "20",
                                  "--max-time", "900", "--proto", "=https", "--proto-redir", "=https",
                                  "--output", file.path, mini["url"]!]
            download.environment = cleanEnvironment()
            download.standardOutput = output; download.standardError = output
            try download.run(); download.waitUntilExit(); try output.close()
            guard download.terminationStatus == 0 else {
                try? FileManager.default.removeItem(at: file)
                throw SetupFailure(message: "下載未完成，請檢查網路後重試。已保留 download.log 供檢查。")
            }
            guard try sha256(file) == mini["sha256"] else {
                try? FileManager.default.removeItem(at: file)
                throw SetupFailure(message: "下載檔案校驗失敗，已停止安裝。請重試。")
            }
            if FileManager.default.fileExists(atPath: installer.path) { try FileManager.default.removeItem(at: installer) }
            try FileManager.default.moveItem(at: file, to: installer)
        }
        report("下載已驗證，正在準備工作台的啟動環境…")
        let parent = runtime.appendingPathComponent("bootstrap")
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        let prefix = parent.appendingPathComponent("miniforge-" + UUID().uuidString.lowercased())
        let log = runtime.appendingPathComponent("bootstrap.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        let out = try FileHandle(forWritingTo: log)
        defer { try? out.close() }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = [installer.path, "-b", "-p", prefix.path]
        task.environment = cleanEnvironment()
        task.standardOutput = out; task.standardError = out
        try task.run(); task.waitUntilExit()
        guard task.terminationStatus == 0 && FileManager.default.isExecutableFile(atPath: prefix.appendingPathComponent("bin/python").path) else {
            throw SetupFailure(message: "啟動工具安裝未完成。詳細紀錄：\(log.path)。可直接重試，原資料會保留。")
        }
        try FileManager.default.createSymbolicLink(at: runtime.appendingPathComponent("miniforge"), withDestinationURL: prefix)
        try JSONSerialization.data(withJSONObject: ["sha256": mini["sha256"]!, "prefix": prefix.path]).write(to: marker, options: .atomic)
        report("啟動工具已準備好。")
    }
}
