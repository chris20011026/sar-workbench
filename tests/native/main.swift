import Foundation

func require(_ value: Bool, _ message: String) { if !value { fatalError(message) } }
let fm = FileManager.default
let base = fm.temporaryDirectory.appendingPathComponent("sar-uninstall-test-" + UUID().uuidString).resolvingSymlinksInPath()
try fm.createDirectory(at: base, withIntermediateDirectories: true)
defer { try? fm.removeItem(at: base) }
let home=base.appendingPathComponent("workbench"), state=base.appendingPathComponent("registry")
let runtime=home.appendingPathComponent("runtime")
try fm.createDirectory(at: runtime, withIntermediateDirectories: true)
try fm.createDirectory(at: state, withIntermediateDirectories: true)
func refuse(_ message: String) {
    do { let plan = try UninstallPlan(home: home, state: state); withExtendedLifetime(plan) {}; fatalError(message) }
    catch { print("PASS: " + message) }
}
refuse("unrecognized directories rejected")
try JSONSerialization.data(withJSONObject: ["product":"sar-workbench", "runtime":runtime.path]).write(to: runtime.appendingPathComponent("workbench-owner.json"))
let foreign=runtime.appendingPathComponent("research.tif");try Data("research".utf8).write(to:foreign)
refuse("unexpected research file protected");try fm.removeItem(at:foreign)
let lock=open(state.appendingPathComponent("server.lock").path,O_CREAT|O_RDWR,0o600)
require(lock>=0 && flock(lock,LOCK_EX|LOCK_NB)==0,"lock acquired")
refuse("running server blocks removal");close(lock)
try JSONSerialization.data(withJSONObject: ["unsafe":runtime.appendingPathComponent("reports/project").path]).write(to:state.appendingPathComponent("projects.json"))
refuse("registered project inside runtime protected")
try JSONSerialization.data(withJSONObject: [String:String]()).write(to:state.appendingPathComponent("projects.json"))
let outside=base.appendingPathComponent("existing-conda");try fm.createDirectory(at:outside,withIntermediateDirectories:true)
try Data("old environment".utf8).write(to:outside.appendingPathComponent("sentinel"))
try fm.createSymbolicLink(at:runtime.appendingPathComponent("miniforge"),withDestinationURL:outside)
let projects=home.appendingPathComponent("projects");try fm.createDirectory(at:projects,withIntermediateDirectories:true)
try Data("results".utf8).write(to:projects.appendingPathComponent("science"))
do {
    let plan=try UninstallPlan(home:home,state:state)
    // Simulate the directory rename made by Trash without using the user's Trash.
    let removed=base.appendingPathComponent("simulated-trash")
    try fm.moveItem(at:plan.runtime,to:removed)
    require(try String(contentsOf:outside.appendingPathComponent("sentinel")) == "old environment","external Conda unchanged")
    require(try String(contentsOf:projects.appendingPathComponent("science")) == "results","projects preserved")
    try fm.moveItem(at:removed,to:runtime)
    require(fm.fileExists(atPath:runtime.appendingPathComponent("workbench-owner.json").path),"restoration succeeds")
    withExtendedLifetime(plan) {}
}
print("PASS: reversible removal preserves external environments and projects")
try fm.moveItem(at:runtime,to:home.appendingPathComponent("saved-runtime"))
try fm.createSymbolicLink(at:runtime,withDestinationURL:outside)
refuse("runtime symlink rejected")
