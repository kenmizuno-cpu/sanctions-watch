import unittest,re
from pathlib import Path
class WorkflowTests(unittest.TestCase):
 def jobs(self):
  text=Path('.github/workflows/watch-mofa.yml').read_text(); starts=list(re.finditer(r'^  (verify|preview|monitor):$',text,re.M));return {m[1]:text[m.end():starts[i+1].start() if i+1<len(starts) else len(text)] for i,m in enumerate(starts)}
 def test_pr_path_has_no_live_fetch_or_write_token(self):
  jobs=self.jobs(); self.assertIn('contents: read',jobs['verify']); self.assertNotIn('python -m src.mofa_watch',jobs['verify']); self.assertIn("github.ref == 'refs/heads/main'",jobs['monitor']); self.assertIn('contents: read',jobs['preview'])
 def test_schedule_needs_explicit_enable(self):
  self.assertIn("vars.MOFA_MONITOR_ENABLED == 'true'",self.jobs()['monitor'])
 def test_data_commit_allowlist_excludes_master(self):
  job=self.jobs()['monitor']; self.assertNotIn('git add -A data/',job); self.assertNotIn('data/master',job); self.assertIn('data/review/mofa_document_queue.csv',job);self.assertNotIn('git push --force',job)
 def test_failure_keeps_evidence_before_exit(self):
  job=self.jobs()['monitor']; self.assertIn('continue-on-error: true',job);self.assertIn('if: always()',job);self.assertLess(job.index('Save evidence'),job.index('Propagate result'))
