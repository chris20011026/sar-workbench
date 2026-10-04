import subprocess
import unittest
from unittest.mock import patch
from alosstack_helper.plot_check import check_plotting

class PlotCheckTests(unittest.TestCase):
    def test_loader_failure_stops_before_other_tools(self):
        result=subprocess.CompletedProcess(['mdx'], -6, '', 'incompatible architecture')
        with patch('alosstack_helper.plot_check.subprocess.run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'incompatible architecture'):
                check_plotting()
            self.assertEqual(run.call_count, 1)

    def test_zero_exit_without_output_is_failure(self):
        result=subprocess.CompletedProcess(['mdx'], 0, 'STOP', '')
        with patch('alosstack_helper.plot_check.subprocess.run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'mdx'):
                check_plotting()
