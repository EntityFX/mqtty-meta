import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from short_gate import make_pair,validate_pair,model_progress
class ShortGateTests(unittest.TestCase):
 def setUp(self):self.source={'queueCapacity':1024,'warmupSeconds':1,'measurementSeconds':10,'drainTimeoutSeconds':30,'seed':42,'qos':2,'network':{'queueCapacity':100000}}
 def test_common_reduction_does_not_mutate_source(self):
  original=copy.deepcopy(self.source);a,b=make_pair(self.source);self.assertEqual(self.source,original);self.assertEqual((a['warmupSeconds'],a['measurementSeconds'],a['drainTimeoutSeconds']),(.25,2,10));validate_pair(a,b)
 def test_second_factor_rejected(self):
  a,b=make_pair(self.source);b['seed']=43
  with self.assertRaises(ValueError):validate_pair(a,b)
 def test_wrong_common_window_rejected(self):
  a,b=make_pair(self.source);a['measurementSeconds']=b['measurementSeconds']=4
  with self.assertRaises(ValueError):validate_pair(a,b)
 def test_nested_network_change_rejected(self):
  a,b=make_pair(self.source);b['network']['queueCapacity']=123
  with self.assertRaises(ValueError):validate_pair(a,b)
 def test_first_finished_job_visible_before_second(self):
  p=model_progress([{'queueCapacity':1024,'technicalError':None,'returnCode':0}]);self.assertEqual(p['finishedModels'],1);self.assertEqual(p['technicalErrors'],0);self.assertEqual(p['total'],2)
 def test_timeout_visible(self):self.assertEqual(model_progress([{'technicalError':'TimeoutError','returnCode':-9}])['technicalErrors'],1)
if __name__=='__main__':unittest.main()
