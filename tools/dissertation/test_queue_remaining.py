from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parent))
import run_queue_remaining as remaining


class AdmissionTests(unittest.TestCase):
    def test_only4096_from1024(self):
        base=dict(queueCapacity=1024,qos=2,offeredRps=5000,measurementSeconds=10)
        remaining.admit(base,dict(base,queueCapacity=4096),526.544,1800)
        with self.assertRaises(ValueError):remaining.admit(base,dict(base,queueCapacity=4096,qos=1),526.544,1800)

    def test_known_cost_prevents_short_timeout(self):
        base=dict(queueCapacity=1024)
        with self.assertRaises(ValueError):remaining.admit(base,dict(queueCapacity=4096),526.544,240)

    def test_missing_success_is_not_reusable(self):
        with self.assertRaises(ValueError):remaining.require_success({'returnCode':-9,'technicalError':'timeout'})
        remaining.require_success({'returnCode':0,'technicalError':None})


if __name__=='__main__':unittest.main()
