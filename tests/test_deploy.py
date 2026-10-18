import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "setup-pi.sh"


class DeploymentScriptTests(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)

    def test_help_does_not_require_a_pi(self):
        response = subprocess.run([str(SCRIPT), "--help"], check=True, capture_output=True, text=True)
        self.assertIn("--check", response.stdout)
        self.assertIn("--install", response.stdout)
