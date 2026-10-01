import re
import unittest
from pathlib import Path


class WorkflowTests(unittest.TestCase):
    def test_workflow_only_verifies_offline_with_read_permissions(self):
        text = Path('.github/workflows/watch-mofa.yml').read_text()
        self.assertNotRegex(text, r'\b(?:schedule|monitor|preview|persist_state):')
        self.assertNotIn('contents: write', text)
        self.assertNotIn('src.mofa_watch', text)
        self.assertNotIn('git push', text)
        self.assertIn('contents: read', text)
        self.assertEqual(re.findall(r'^  (\w+):$', text.split('jobs:')[1], re.M), ['verify'])
        self.assertIn("python -m unittest discover -s tests -p 'test_*.py'", text)
