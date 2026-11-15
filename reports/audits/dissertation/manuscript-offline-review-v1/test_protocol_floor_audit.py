import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from protocol_floor_audit import count_floor
class FloorTests(unittest.TestCase):
 def test_weighted_counts_and_boundary(self):
  x=count_floor([[59,2],[60,3],[61,5]],6,10);self.assertEqual(x['belowFloor'],2);self.assertEqual(x['atFloor'],3);self.assertEqual(x['zeroResidual'],5);self.assertEqual(x['total'],10);self.assertEqual(x['belowFraction'],.2)
 def test_no_clipping(self):self.assertEqual(count_floor([[85,8]],6,10)['belowFloor'],0)
 def test_empty_rejected(self):
  with self.assertRaises(ValueError):count_floor([],6,10)
 def test_negative_count_rejected(self):
  with self.assertRaises(ValueError):count_floor([[30,-1]],6,10)
if __name__=='__main__':unittest.main()
