import subprocess
import unittest
from unittest.mock import patch

from alosstack_helper.native_picker import choose_native

class NativePickerTests(unittest.TestCase):
    @patch('alosstack_helper.native_picker.sys.platform', 'darwin')
    @patch('alosstack_helper.native_picker.subprocess.run')
    def test_folder_selected_and_paths_passed_as_arguments(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '/Volumes/My NAS/資料/\n', '')
        result = choose_native('dir', '/tmp')
        self.assertEqual(result['path'], '/Volumes/My NAS/資料')
        args = run.call_args.args[0]
        self.assertEqual(args[-2:], ['/tmp', 'dir'])
        self.assertNotIn('/Volumes/My NAS', args[2])

    @patch('alosstack_helper.native_picker.sys.platform', 'darwin')
    @patch('alosstack_helper.native_picker.subprocess.run')
    def test_cancel_does_not_trigger_error(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, '', 'User canceled. (-128)')
        self.assertTrue(choose_native('file')['cancelled'])

    @patch('alosstack_helper.native_picker.sys.platform', 'linux')
    def test_other_platform_uses_browser_fallback(self):
        self.assertFalse(choose_native('file')['supported'])

    @patch('alosstack_helper.native_picker.sys.platform', 'darwin')
    @patch('alosstack_helper.native_picker.subprocess.run')
    def test_failure_releases_dialog_lock(self, run):
        run.side_effect = subprocess.TimeoutExpired('osascript', 600)
        with self.assertRaises(RuntimeError): choose_native('file')
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 0, '/tmp/a.xml\n', '')
        self.assertEqual(choose_native('file')['path'], '/tmp/a.xml')
