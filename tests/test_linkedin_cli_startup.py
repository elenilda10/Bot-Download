import os
from pathlib import Path
import subprocess
import sys
import unittest


class LinkedInCliStartup(unittest.TestCase):
    def test_module_starts_without_loading_bot_config(self):
        root = Path(__file__).resolve().parents[1]
        code = """
import runpy, sys
sys.argv = ['linkedin_browser', '--help']
try:
    runpy.run_module('services.platforms.linkedin_browser', run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
assert 'config' not in sys.modules
assert 'services.download.queue' not in sys.modules
"""
        env = os.environ.copy()
        env.pop("BOT_TOKEN", None)
        result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                                capture_output=True, text=True, check=True)
        self.assertIn("--output", result.stdout)
