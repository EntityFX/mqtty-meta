import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from queue_config_gate import validate_pair
class QueueGateTests(unittest.TestCase):
 def setUp(self): self.a={'queueCapacity':1024,'qos':2,'offeredRps':1000};self.b={**self.a,'queueCapacity':4096}
 def test_single_factor_passes(self):validate_pair(self.a,self.b)
 def test_second_factor_rejected(self):
  self.b['qos']=1
  with self.assertRaises(ValueError):validate_pair(self.a,self.b)
 def test_wrong_capacity_rejected(self):
  self.b['queueCapacity']=8192
  with self.assertRaises(ValueError):validate_pair(self.a,self.b)
if __name__=='__main__':unittest.main()
