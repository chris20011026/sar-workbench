"""Native picker tests never build or open a real user interface."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from alosstack_helper import native_picker as picker


@patch('alosstack_helper.native_picker.sys.platform', 'darwin')
class NativePickerTests(unittest.TestCase):
    def setUp(self):
        self.builder = patch.object(picker, '_ensure_helper', return_value=Path('/fake/SARFilePicker.app'))
        self.viewer = patch.object(picker, '_show_helper')
        self.build = self.builder.start()
        self.show = self.viewer.start()
        self.addCleanup(self.builder.stop)
        self.addCleanup(self.viewer.stop)

    def test_folder_selected_and_paths_passed_as_data(self):
        self.show.return_value = {'cancelled': False, 'path': '/Volumes/My NAS/資料/'}
        result = picker.choose_native('dir', '/tmp')
        self.assertEqual(result['path'], '/Volumes/My NAS/資料')
        self.assertEqual(self.show.call_args.args, (Path('/fake/SARFilePicker.app'), 'dir', Path('/tmp').resolve()))

    def test_cancel_does_not_select_or_trigger_error(self):
        self.show.return_value = {'cancelled': True}
        self.assertEqual(picker.choose_native('file'), {'supported': True, 'cancelled': True})

    def test_other_platform_uses_browser_fallback(self):
        with patch.object(picker.sys, 'platform', 'linux'):
            self.assertFalse(picker.choose_native('file')['supported'])
        self.build.assert_not_called()
        self.show.assert_not_called()

    def test_failure_releases_dialog_lock(self):
        self.show.side_effect = RuntimeError('原生視窗未能顯示')
        with self.assertRaises(RuntimeError):
            picker.choose_native('file')
        self.show.side_effect = None
        self.show.return_value = {'cancelled': False, 'path': '/tmp/a.xml'}
        self.assertEqual(picker.choose_native('file')['path'], '/tmp/a.xml')

    def test_scientific_environment_cannot_inject_gui_libraries(self):
        with patch.dict('os.environ', {'DYLD_LIBRARY_PATH': '/science/lib',
                                      'PYTHONHOME': '/science', 'CONDA_PREFIX': '/science',
                                      'HOME': '/Users/example'}):
            env = picker._native_environment()
        self.assertNotIn('DYLD_LIBRARY_PATH', env)
        self.assertNotIn('PYTHONHOME', env)
        self.assertNotIn('CONDA_PREFIX', env)
        self.assertEqual(env['HOME'], '/Users/example')
        self.assertEqual(env['PATH'], '/usr/bin:/bin:/usr/sbin:/sbin')

    def test_starting_at_a_file_uses_its_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'quoted " $ `資料.xml'
            source.write_text('data')
            self.show.return_value = {'cancelled': True}
            picker.choose_native('file', str(source))
            self.assertEqual(self.show.call_args.args[2], source.parent.resolve())

    def test_busy_dialog_reports_instead_of_starting_second(self):
        with picker._lock:
            with self.assertRaisesRegex(ValueError, '已有'):
                picker.choose_native('dir')
        self.build.assert_not_called()

    def test_invalid_mode_cannot_launch(self):
        with self.assertRaises(ValueError):
            picker.choose_native('delete')
        self.build.assert_not_called()

    def test_malformed_result_rejected_without_path(self):
        for value in ({}, [], {'cancelled': False, 'path': 'relative/path'},
                      {'cancelled': False, 'path': 0}, {'cancelled': 'false'}):
            with self.subTest(value=value):
                self.show.return_value = value
                with self.assertRaises(RuntimeError):
                    picker.choose_native('file')


class NativePickerProtocolTests(unittest.TestCase):
    def test_launchservices_response_is_bound_to_private_request(self):
        def launch(args, **kwargs):
            self.assertEqual(args[:4], ['/usr/bin/open', '-n', '-W', '/fake/picker.app'])
            self.assertNotEqual(kwargs['stdout'], subprocess.PIPE)
            request = Path(args[-1])
            payload = json.loads(request.read_text())
            self.assertEqual(payload['start'], '/Volumes/資料 with "quotes"')
            self.assertEqual(request.parent.stat().st_mode & 0o077, 0)
            result = dict(token=payload['token'], parent_pid=os.getpid(), pid=12345,
                          cancelled=False, path='/Volumes/資料/example')
            (request.parent/'response.json').write_text(json.dumps(result))
            return Mock(wait=Mock(return_value=0))
        with patch.object(picker.subprocess, 'Popen', side_effect=launch):
            result = picker._show_helper(Path('/fake/picker.app'), 'dir', '/Volumes/資料 with "quotes"')
        self.assertEqual(result['path'], '/Volumes/資料/example')

    def test_unshown_application_has_a_short_bounded_timeout(self):
        process = Mock(pid=23456)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('open', 2), 0]
        def launch(args, **kwargs):
            request = Path(args[-1]);payload = json.loads(request.read_text())
            (request.parent/'ready.json').write_text(json.dumps(dict(token=payload['token'], parent_pid=os.getpid(), pid=12345, phase='launching')))
            return process
        with patch.object(picker.subprocess, 'Popen', side_effect=launch), \
             patch.object(picker.time, 'monotonic', side_effect=[0, 21]), \
             patch.object(picker.os, 'kill') as kill, patch.object(picker.os, 'killpg') as killpg:
            with self.assertRaisesRegex(RuntimeError, '未能顯示'):
                picker._show_helper(Path('/fake/picker.app'), 'dir', '/tmp')
        kill.assert_not_called()
        killpg.assert_called_once_with(23456, signal.SIGTERM)
        self.assertEqual(process.wait.call_args_list[0].kwargs['timeout'], 2)

    def test_forged_reply_rejected_without_signalling_unknown_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = Path(tmp)/'response.json'
            response.write_text(json.dumps(dict(token='wrong', parent_pid=os.getpid(), pid=999)))
            with self.assertRaisesRegex(RuntimeError, '驗證失敗'):
                picker._read_reply(response, 'expected')

    def test_compiler_timeout_never_waits_on_inherited_pipe(self):
        process = Mock(pid=23456)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('compiler', 120), 0]
        with patch.object(picker.subprocess, 'Popen', return_value=process) as popen, patch.object(picker.os, 'killpg'):
            with self.assertRaisesRegex(RuntimeError, '準備逾時'):
                picker._bounded_command(['fake-compiler'])
        self.assertNotEqual(popen.call_args.kwargs['stdout'], subprocess.PIPE)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_download_app_uses_bundled_picker_without_compiler(self):
        with tempfile.TemporaryDirectory() as temp:
            resources = Path(temp);binary = resources/'SARFilePicker.app/Contents/MacOS/SARFilePicker'
            binary.parent.mkdir(parents=True);binary.write_bytes(b'packaged executable')
            with patch.object(picker, 'RESOURCES', resources), patch.object(picker, 'build_native_helper') as builder:
                self.assertEqual(picker._ensure_helper(), resources/'SARFilePicker.app')
                builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
