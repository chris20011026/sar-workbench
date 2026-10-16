"""Authenticated ALOS batch HTTP routes, using temporary state and fake jobs.

No Conda process, native picker, SAR conversion, or satellite-data calculation
is launched. Events model waiting UI/launch operations without timing sleeps.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from alosstack_helper.engine import Engine
from alosstack_helper.server import make_server


class AlosPreparationAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.runtime = patch.dict(os.environ, {"SAR_WORKBENCH_HOME": str(self.base / "managed")})
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.defaults = patch("alosstack_helper.server.defaults", return_value=dict(
            home=str(self.base), env="", template="", environments=[]))
        self.defaults.start()
        self.addCleanup(self.defaults.stop)
        self.engine = Engine(self.base / "registry")
        self.server = make_server(self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.manager = self.server.alos_batches
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        with urllib.request.urlopen(self.url, timeout=5) as response:
            self.html = response.read().decode()
        self.token = re.search(r'name="api-token" content="([^"]+)"', self.html)[1]
        self.key = "a" * 32

    def tearDown(self):
        self.engine.active = None
        self.manager.active = None
        self.server.nisar_jobs.active = None
        self.server.conversion_jobs.active = None
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, data=None, headers=None, timeout=5):
        merged = {"X-Token": self.token, "Content-Type": "application/json"}
        merged.update(headers or {})
        return urllib.request.urlopen(urllib.request.Request(
            self.url + path, headers=merged,
            data=None if data is None else json.dumps(data).encode()), timeout=timeout)

    def value(self, path, data=None, headers=None, timeout=5):
        with self.request(path, data, headers, timeout) as response:
            return json.load(response)

    def rejected(self, path, data=None, headers=None, code=400):
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request(path, data, headers)
        self.assertEqual(failure.exception.code, code)
        error = json.loads(failure.exception.read())
        failure.exception.close()
        self.assertIn("error", error)
        return error["error"]

    def seed_job(self):
        state = dict(id=self.key, status="ready", stage="Synthetic metadata fixture", created=1,
                     config=dict(name="Synthetic ALOS preparation", source=str(self.base / "source"),
                                 environment="/synthetic/not-executed", root="", tool=""),
                     progress=dict(items=[]), attempts=[], error="", report=dict(products=[]),
                     processing_unlocked=False)
        self.manager.jobs[self.key] = state
        self.manager.save(state)
        return state

    def background_request(self, path, data):
        results = queue.Queue()
        def call():
            try:
                results.put((self.value(path, data), None))
            except BaseException as error:
                results.put((None, error))
        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        return thread, results

    def test_routes_require_token_same_origin_and_exact_loopback_host(self):
        gets = ["/api/alos-preparation", "/api/alos-preparation?job=" + self.key,
                "/api/alos-preparation-artifact?job=" + self.key + "&file=report.json"]
        posts = ["/api/alos-preparation-" + name for name in ("scan", "run", "coverage", "review", "stop")]
        with ExitStack() as stack:
            methods = [stack.enter_context(patch.object(self.manager, name)) for name in
                       ("listing", "get", "artifact", "create", "start", "coverage", "review", "stop")]
            for path, data in [(p, None) for p in gets] + [(p, {}) for p in posts]:
                for headers in ({"X-Token": ""}, {"X-Token": "wrong"}, {"Origin": "https://other.example"},
                                {"Origin": "http://localhost:" + str(self.server.server_port)},
                                {"Host": "unrelated.example"}):
                    with self.subTest(path=path, headers=headers):
                        self.rejected(path, data, headers, code=403)
            for method in methods:
                method.assert_not_called()
        self.assertEqual(self.manager.jobs, {})
        self.assertFalse(self.manager.home.exists())

    def test_catalogue_and_job_detail_remain_nonprocessing(self):
        self.assertEqual(self.value("/api/alos-preparation", headers={"Origin": self.url}), dict(jobs=[], busy=False))
        state = self.seed_job()
        catalogue = self.value("/api/alos-preparation")
        self.assertFalse(catalogue["busy"])
        self.assertEqual([p["id"] for p in catalogue["jobs"]], [self.key])
        detail = self.value("/api/alos-preparation?job=" + self.key)
        self.assertEqual(detail["report"], state["report"])
        self.assertFalse(detail["processing_unlocked"])
        self.rejected("/api/alos-preparation?job=" + "b" * 32)
        self.rejected("/api/alos-preparation?job=../state")
        self.assertIsNone(self.engine.active)
        self.assertIsNone(self.manager.process)

    def test_static_assets_load_without_data_token_but_reject_foreign_host(self):
        for name in ("alos-preparation.js", "alos-preparation.css"):
            self.assertIn("/" + name, self.html)
            with self.request("/" + name, headers={"X-Token": ""}) as response:
                self.assertGreater(len(response.read()), 50)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.rejected("/" + name, headers={"Host": "other.example"}, code=403)

    def test_scan_run_coverage_and_review_delegate_exact_payload_without_starting_workers(self):
        body = dict(id=self.key, source="/synthetic/source", environment="/synthetic/env",
                    product_ids=["one"], root="/synthetic/output", reviewed=True,
                    left_ids=["one"], right_ids=["two"], revision="source-revision",
                    outcome="pending", notes="", reviewer="")
        for route, method in (("scan", "create"), ("run", "start"), ("coverage", "coverage"), ("review", "review")):
            with self.subTest(route=route), patch.object(self.manager, method, return_value={"status": "synthetic"}) as handler:
                self.assertEqual(self.value("/api/alos-preparation-" + route, body), {"status": "synthetic"})
                if method == "create":
                    handler.assert_called_once_with(body)
                else:
                    handler.assert_called_once_with(self.key, body)
        self.assertFalse(self.manager.home.exists())
        self.assertIsNone(self.manager.process)

    def test_other_heavy_work_blocks_all_batch_entrypoints_before_delegation(self):
        routes = ["/api/alos-preparation-" + name for name in ("scan", "run", "coverage", "review")]
        blockers = [
            ("isce_or_research", lambda: patch.object(self.engine, "active", "busy")),
            ("nisar", lambda: patch.object(self.server.nisar_jobs, "active", "busy")),
            ("single_conversion", lambda: patch.object(self.server.conversion_jobs, "active", "busy")),
            ("migration", lambda: patch.object(self.server.migrations, "busy", return_value="busy")),
            ("environment_setup", lambda: patch.object(self.server.onboarding.runtime, "status", return_value={"status": "running"})),
            ("data_preparation", lambda: patch.dict(self.server.onboarding.jobs, {"busy": {"status": "running"}})),
        ]
        with ExitStack() as stack:
            methods = [stack.enter_context(patch.object(self.manager, name)) for name in ("create", "start", "coverage", "review")]
            for label, blocker in blockers:
                with blocker():
                    for path in routes:
                        with self.subTest(blocker=label, path=path):
                            error = self.rejected(path, {"id": self.key})
                            self.assertTrue(any(word in error for word in ("運算", "搬移")))
            for method in methods:
                method.assert_not_called()

    def test_active_batch_blocks_other_heavy_launches_and_mutations(self):
        routes = ["/api/action", "/api/analysis-create", "/api/analysis-invert", "/api/create", "/api/setup-start",
                  "/api/demo-start", "/api/terrain-start", "/api/import-start", "/api/nisar-create", "/api/nisar-run",
                  "/api/alos4-conversion-create", "/api/alos4-conversion-run", "/api/environment-check",
                  "/api/comparison-start", "/api/research-report-start", "/api/legacy-preview", "/api/legacy-create",
                  "/api/migration-plan", "/api/migration-copy", "/api/migration-switch"]
        with patch.object(self.manager, "active", self.key), patch.object(self.engine, "launch") as launch:
            for path in routes:
                with self.subTest(path=path):
                    self.assertIn("ALOS 批次", self.rejected(path, {}))
            launch.assert_not_called()
        self.assertEqual(self.manager.jobs, {})

    def test_report_download_allowlist_mime_and_symlink_protection(self):
        self.seed_job()
        folder = self.manager.folder(self.key)
        base = "/api/alos-preparation-artifact?job=" + self.key + "&file="
        with self.request(base + "report.json") as response:
            self.assertEqual(response.headers["Content-Type"], "application/json")
            exported = json.load(response)
            self.assertEqual(exported["id"], self.key)
            self.assertEqual(exported["config"]["name"], "Synthetic ALOS preparation")
            self.assertFalse(exported["processing_unlocked"])
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with self.request(base + "report.html") as response:
            self.assertEqual(response.headers["Content-Type"], "text/html")
            self.assertIn(b"Synthetic ALOS preparation", response.read())
        for name in ("../state.json", "state.json", "run.log", "/etc/passwd", "report.json/other", "..\\state.json"):
            with self.subTest(name=name):
                self.rejected(base + urllib.parse.quote(name, safe=""))
        self.rejected(base.replace(self.key, "b" * 32) + "report.json")
        (folder / "report.json").unlink()
        (folder / "report.json").symlink_to(folder / "state.json")
        self.rejected(base + "report.json")
        (folder / "report.json").unlink()
        (folder / "report.html").unlink()
        # Missing generated reports may be safely regenerated from job state.
        with self.request(base + "report.html") as response:
            self.assertIn(b"Synthetic ALOS preparation", response.read())
        outside = self.base / "outside"
        outside.mkdir()
        secret = b'{"private":"must not be served or overwritten"}'
        (outside / "report.json").write_bytes(secret)
        folder.rename(folder.with_name("saved-fixture"))
        folder.symlink_to(outside, target_is_directory=True)
        self.rejected(base + "report.json")
        self.assertEqual((outside / "report.json").read_bytes(), secret)

    def test_stop_requires_the_active_batch_identity(self):
        with patch.object(self.manager, "active", self.key), patch.object(self.manager, "stop") as stop:
            for data in ({}, {"id": "b" * 32}):
                self.assertIn("不是目前", self.rejected("/api/alos-preparation-stop", data))
            stop.assert_not_called()
            self.assertEqual(self.value("/api/alos-preparation-stop", {"id": self.key}), {"ok": True})
            stop.assert_called_once_with()

    def test_native_picker_wait_does_not_block_active_batch_stop(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        def picker(mode, start):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("Synthetic picker was not released")
            return {"supported": True, "cancelled": True}
        with patch("alosstack_helper.server.choose_native", side_effect=picker), \
                patch.object(self.manager, "active", self.key), patch.object(self.manager, "stop", side_effect=stopped.set):
            thread, results = self.background_request("/api/pick", {"mode": "dir", "start": str(self.base)})
            try:
                self.assertTrue(entered.wait(2), "picker route did not start")
                self.assertEqual(self.value("/api/alos-preparation-stop", {"id": self.key}, timeout=2), {"ok": True})
                self.assertTrue(stopped.is_set())
                self.assertFalse(release.is_set(), "stop must finish before the picker closes")
            finally:
                release.set()
                thread.join(timeout=5)
            value, error = results.get(timeout=1)
            if error:
                raise error
            self.assertTrue(value["cancelled"])

    def test_launch_operation_lock_does_not_block_stop_route(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        def starting(data):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("Synthetic launch was not released")
            return {"id": self.key, "status": "checking"}
        with patch.object(self.manager, "create", side_effect=starting), patch.object(self.manager, "stop", side_effect=stopped.set):
            thread, results = self.background_request("/api/alos-preparation-scan", {})
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.value("/api/alos-preparation-stop", {"id": self.key}, timeout=2), {"ok": True})
                self.assertTrue(stopped.is_set())
                self.assertFalse(release.is_set())
            finally:
                release.set()
                thread.join(timeout=5)
            value, error = results.get(timeout=1)
            if error:
                raise error
            self.assertEqual(value["status"], "checking")

    def test_coverage_wait_can_be_stopped_through_http_without_launching_a_process(self):
        state = self.seed_job()
        state["report"]["products"] = [dict(id="one"), dict(id="two")]
        entered, release = threading.Event(), threading.Event()
        class FakeProcess:
            pid = 999999999
            def poll(self):
                return -15 if release.is_set() else None
            def wait(self, timeout=None):
                entered.set()
                if not release.wait(min(timeout or 4, 4)):
                    raise subprocess.TimeoutExpired("synthetic process", timeout)
                return -15
        fake = FakeProcess()
        def signal_fake(pid, sig):
            self.assertEqual(pid, fake.pid)
            self.assertIn(sig, (signal.SIGTERM, signal.SIGKILL))
            release.set()
        with patch.object(self.manager, "_command", return_value=["synthetic-not-executed"]), \
                patch("alosstack_helper.alos_preparation.subprocess.Popen", return_value=fake) as spawn, \
                patch("alosstack_helper.nisar_jobs.os.killpg", side_effect=signal_fake) as kill:
            thread, results = self.background_request("/api/alos-preparation-coverage", {
                "id": self.key, "left_ids": ["one"], "right_ids": ["two"]})
            try:
                self.assertTrue(entered.wait(2), "coverage did not reach the synthetic wait")
                self.assertEqual(self.manager.active, self.key)
                self.assertEqual(self.value("/api/alos-preparation-stop", {"id": self.key}, timeout=2), {"ok": True})
                self.assertTrue(self.manager.cancel.is_set())
                kill.assert_called()
            finally:
                release.set()
                thread.join(timeout=5)
            _, error = results.get(timeout=1)
            self.assertIsInstance(error, urllib.error.HTTPError)
            self.assertEqual(error.code, 400)
            self.assertIn("停止", json.loads(error.read())["error"])
            error.close()
            spawn.assert_called_once()
        self.assertIsNone(self.manager.active)
        self.assertIsNone(self.manager.process)
        self.assertNotIn("pid", self.manager.jobs[self.key])
        self.assertFalse(self.manager.get(self.key)["processing_unlocked"])

    def test_concurrent_batch_and_nisar_launches_are_serialized(self):
        entered, release = threading.Event(), threading.Event()
        def starting(data):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("Synthetic launch was not released")
            self.manager.active = self.key
            return {"id": self.key, "status": "checking"}
        with patch.object(self.manager, "create", side_effect=starting), patch.object(self.server.nisar_jobs, "create") as nisar:
            first, first_result = self.background_request("/api/alos-preparation-scan", {})
            second = None
            try:
                self.assertTrue(entered.wait(2))
                second, second_result = self.background_request("/api/nisar-create", {})
                release.set()
                first.join(timeout=5)
                second.join(timeout=5)
                value, error = first_result.get(timeout=1)
                if error:
                    raise error
                self.assertEqual(value["status"], "checking")
                _, error = second_result.get(timeout=1)
                self.assertIsInstance(error, urllib.error.HTTPError)
                self.assertEqual(error.code, 400)
                self.assertIn("ALOS 批次", json.loads(error.read())["error"])
                error.close()
                nisar.assert_not_called()
            finally:
                release.set()
                first.join(timeout=5)
                if second:
                    second.join(timeout=5)
                self.manager.active = None


if __name__ == "__main__":
    unittest.main()
