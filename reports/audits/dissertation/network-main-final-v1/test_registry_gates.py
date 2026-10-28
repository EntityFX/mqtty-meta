import copy,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from registry_gates import validate_registry
class FinalRegistryTests(unittest.TestCase):
 def setUp(self):
  files=('summary.json','histograms.json','publications.json','delivery-receipts.json','transmissions.json','network-evidence.json')
  self.plan={'jobs':[{'id':'m1','kind':'matrix'},{'id':'m2','kind':'matrix'},{'id':'r1','kind':'replay','replayOf':'m1'},{'id':'r2','kind':'replay','replayOf':'m2'}]}
  def row(key):return {'id':key,'returnCode':0,'technicalError':None,'scientificSha256':dict.fromkeys(files,'a'*64),'summary':{'sourceCompleted':10}}
  self.matrix={'rows':[row('m1'),row('m2')]};self.replay={'rows':[row('r1'),row('r2')]}
 def test_complete_registry_passes(self):self.assertEqual(validate_registry(self.plan,self.matrix,self.replay),2)
 def test_missing_replay_rejected(self):
  self.replay['rows'].pop()
  with self.assertRaises(ValueError):validate_registry(self.plan,self.matrix,self.replay)
 def test_incomplete_matrix_rejected(self):
  self.matrix['rows'].pop()
  with self.assertRaises(ValueError):validate_registry(self.plan,self.matrix,self.replay)
 def test_wrong_scientific_sha_rejected(self):
  self.replay['rows'][0]['scientificSha256']['summary.json']='b'*64
  with self.assertRaises(ValueError):validate_registry(self.plan,self.matrix,self.replay)
 def test_line_without_optional_transit_file_passes(self):
  self.matrix['rows'][0]['scientificSha256'].pop('network-evidence.json')
  self.replay['rows'][0]['scientificSha256'].pop('network-evidence.json')
  self.assertEqual(validate_registry(self.plan,self.matrix,self.replay),2)
 def test_missing_required_scientific_file_rejected(self):
  self.matrix['rows'][0]['scientificSha256'].pop('summary.json')
  self.replay['rows'][0]['scientificSha256'].pop('summary.json')
  with self.assertRaises(ValueError):validate_registry(self.plan,self.matrix,self.replay)
 def test_star_missing_transit_file_rejected(self):
  self.plan['jobs'][0]['id']='matrix.star.single.q0.r1000.w10.s42'
  self.matrix['rows'][0]['id']=self.plan['jobs'][0]['id']
  self.plan['jobs'][2]['replayOf']=self.plan['jobs'][0]['id']
  self.matrix['rows'][0]['scientificSha256'].pop('network-evidence.json')
  self.replay['rows'][0]['scientificSha256'].pop('network-evidence.json')
  with self.assertRaises(ValueError):validate_registry(self.plan,self.matrix,self.replay)
if __name__=='__main__':unittest.main()
