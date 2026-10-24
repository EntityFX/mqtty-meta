import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from qos1_linux_diagnostic import validate_diagnostic_config, selected_job, sha

class DrainExtensionTests(unittest.TestCase):
    def test_explicit_q2_identity(self):
        self.assertEqual(selected_job({'jobId':'pilot.star.federation.q2.r5000.w10.s42'}),'pilot.star.federation.q2.r5000.w10.s42')
    def test_reject_unplanned_identity(self):
        with self.assertRaises(ValueError): selected_job({'jobId':'unplanned'})
    def check_config(self, update, declared=10, accepted=True):
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/'original.json'; candidate=Path(folder)/'candidate.json'
            baseline={'drainTimeoutSeconds':5,'qos':1,'offeredRps':5000}
            original.write_text(json.dumps(baseline),encoding='utf8')
            candidate.write_text(json.dumps(dict(baseline,**update)),encoding='utf8')
            manifest={'configSha256':sha(candidate)}
            if declared is not None: manifest['drainExtensionSeconds']=declared
            if accepted: validate_diagnostic_config(original,candidate,manifest)
            else:
                with self.assertRaises(ValueError): validate_diagnostic_config(original,candidate,manifest)
    def test_exact_legacy(self): self.check_config({},None)
    def test_declared_extension(self): self.check_config({'drainTimeoutSeconds':10})
    def test_undeclared_change(self): self.check_config({'drainTimeoutSeconds':10},None,False)
    def test_qos_change(self): self.check_config({'drainTimeoutSeconds':10,'qos':0},accepted=False)
    def test_rate_change(self): self.check_config({'drainTimeoutSeconds':10,'offeredRps':100},accepted=False)
    def test_unbounded_extension(self): self.check_config({'drainTimeoutSeconds':31},31,False)
    def test_shorter_drain(self): self.check_config({'drainTimeoutSeconds':4},4,False)
if __name__=='__main__': unittest.main()
