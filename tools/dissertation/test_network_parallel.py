from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import tempfile
import json
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import run_network_parallel as parallel
from test_network_runner import NetworkRunnerTests
from network_study_inputs import read,write
from network_study_process import execute

class ParallelBudgetTests(unittest.TestCase):
    def setUp(self):
        self.base=NetworkRunnerTests(methodName='runTest');self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.patch=patch.object(parallel,'memory_available',return_value=32*1024**3);self.patch.start();self.addCleanup(self.patch.stop)
    def staged(self,seeds=(42,43,44)):
        root=self.base.staged(seeds)
        m=read(root/'execution-manifest.json');m['jobRssLimitBytes']=1024**3;write(root/'execution-manifest.json',m)
        return root
    def test_parallel_delivery_and_reverify_no_rerun(self):
        root=self.staged();r=parallel.run(root,'pilot',workers=2,wall_seconds=30,cleanup_seconds=1)
        self.assertEqual(r['verified'],3)
        old=(root/'results-pilot.json').read_bytes()
        r=parallel.run(root,'pilot',workers=2,wall_seconds=30,cleanup_seconds=1,resume=True)
        self.assertTrue(r['reverifiedOnly']);self.assertEqual(old,(root/'results-pilot.json').read_bytes())
        self.assertEqual(len(list((root/'runs').glob('*/attempt-*'))),3)
    def test_failure_stops_new_work(self):
        root=self.staged((99,42))
        self.assertEqual(parallel.run(root,'pilot',workers=1,wall_seconds=30,cleanup_seconds=1)['status'],'failed')
        self.assertFalse((root/'runs/pilot.1').exists())
    def test_matrix_gate_not_bypassed(self):
        root=self.staged()
        with self.assertRaises(ValueError):parallel.run(root,'matrix',workers=2,wall_seconds=30,cleanup_seconds=1)
    def test_memory_admission(self):
        root=self.staged()
        with patch.object(parallel,'memory_available',return_value=1024):
            with self.assertRaisesRegex(RuntimeError,'RAM'):parallel.run(root,'pilot',workers=4)
    def test_resource_increase_keeps_original_manifest_and_resumes_without_rerun(self):
        root=self.staged((42,));original=(root/'execution-manifest.json').read_bytes()
        policy=root/'resource-policy.json'
        write(policy,{'executionManifestSha256':hashlib.sha256(original).hexdigest(),
            'controllerSha256':parallel.sha(Path(parallel.__file__)),'workers':2,
            'jobRssLimitBytes':4*1024**3,'reason':'Inspected RSS failure; resource-only increase'})
        result=parallel.run(root,'pilot',workers=2,wall_seconds=30,cleanup_seconds=1,resource_decision=policy)
        self.assertEqual(result['verified'],1)
        parallel.run(root,'pilot',workers=2,wall_seconds=30,cleanup_seconds=1,resource_decision=policy,resume=True)
        self.assertEqual(original,(root/'execution-manifest.json').read_bytes())
        self.assertEqual(len(list((root/'runs').glob('*/attempt-*'))),1)
        with self.assertRaisesRegex(ValueError,'worker count'):
            parallel.run(root,'pilot',workers=4,resource_decision=policy)
    def test_resource_decision_rejects_wrong_execution(self):
        root=self.staged();policy=root/'resource-policy.json'
        write(policy,{'executionManifestSha256':'wrong','reason':'test'})
        with self.assertRaisesRegex(ValueError,'original execution'):
            parallel.run(root,'pilot',resource_decision=policy)
    def test_explicit_retry_keeps_failed_attempt(self):
        root=self.staged((99,))
        parallel.run(root,'pilot',workers=1,wall_seconds=30,cleanup_seconds=1)
        receipt=next((root/'runs').glob('*/attempt-001/result.json'));original=receipt.read_bytes()
        row=read(receipt)
        with self.assertRaisesRegex(ValueError,'Inspect failed'):
            parallel.run(root,'pilot',workers=1,wall_seconds=30,cleanup_seconds=1,resume=True)
        parallel.run(root,'pilot',workers=1,wall_seconds=30,cleanup_seconds=1,resume=True,
            retry_job=row['id'],retry_reason='Inspected synthetic failure')
        self.assertEqual(original,receipt.read_bytes())
        self.assertEqual(len(list((root/'runs').glob('*/attempt-*'))),2)
    def test_deadline_is_shared_and_not_reset(self):
        root=self.staged();guard=SimpleNamespace(save=lambda p,v:write(p,v))
        parallel.deadline(root,guard,100);saved=read(root/'wall-budget.json')
        parallel.deadline(root,guard,100);self.assertEqual(saved,read(root/'wall-budget.json'))
        with self.assertRaises(ValueError):parallel.deadline(root,guard,101)
        with patch.object(parallel.time,'time',return_value=saved['deadlineEpoch']+1):
            with self.assertRaises(RuntimeError):parallel.deadline(root,guard,100)
    def test_three_hours_is_upper_limit(self):
        root=self.staged()
        with self.assertRaises(ValueError):parallel.deadline(root,None,10801)
    def test_budget_cancels_owned_process(self):
        with tempfile.TemporaryDirectory() as folder:
            result=execute([sys.executable,'-c','import time;time.sleep(10)'],Path(folder),
                SimpleNamespace(check=lambda:None),10,1024**3,1024**2,lambda s:None,
                sample_seconds=.01,cancel_requested=lambda:True)
            self.assertIn('executionBudgetExhausted',result['technicalError'])
            self.assertIsNotNone(result['returnCode'])
            self.assertLess(result['processWallSeconds'],3)
    def test_preserved_pilot_gate_binds_inputs_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'study';(root/'program').mkdir(parents=True)
            config=Path(folder)/'source.json';write(config,{'drainTimeoutSeconds':5,'qos':1})
            output=Path(folder)/'output';output.mkdir();write(output/'summary.json',{});write(output/'sha256.json',{})
            write(root/'program/config.json',{'drainTimeoutSeconds':30,'qos':1})
            h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            point={'id':'pilot.test','originalOutputAbsolute':str(output),'originalConfigAbsolute':str(config),
              'originalConfigSha256':h(config),'originalSummarySha256':h(output/'summary.json'),
              'outputSealSha256':h(output/'sha256.json'),'warnings':[]}
            registry=root/'program/registry.json';write(registry,{'points':[point],'discrepancies':[]})
            manifest={'mountRoot':folder,'files':{'program/registry.json':h(registry)}}
            decision={'decision':'accept','executionManifestSha256':'m','pilotRegistrySha256':h(registry),
                'reviewer':'synthetic test','reason':'test only'}
            plan={'jobs':[{'id':'pilot.test','kind':'pilot','config':'config.json'}]}
            summary={'status':'completed','tickMilliseconds':.1,'phases':[{'name':'drain','startTick':0,'endTick':10}]}
            with patch.object(parallel.runner,'verify_output',return_value=(summary,[])):
                self.assertEqual(parallel.preserved_pilot_decision(root,manifest,'m',plan,registry,json.dumps(decision).encode()),decision)
                write(root/'program/config.json',{'drainTimeoutSeconds':30,'qos':2})
                with self.assertRaises(ValueError):parallel.preserved_pilot_decision(root,manifest,'m',plan,registry,json.dumps(decision).encode())
    def test_unrelated_cgroup_is_rejected(self):
        values='RuntimeMaxUSec=3h\nMemoryMax=10737418240\nMemorySwapMax=0\nControlGroup=/bounded/other\nMainPID=1\n'
        with patch.object(parallel.subprocess,'check_output',return_value=values), patch.object(Path,'read_text',return_value='0::/actual/controller'):
            with self.assertRaisesRegex(ValueError,'actually belong'):parallel.check_service('test.service')
    def test_additional_session_keeps_expired_original_and_completed_attempts(self):
        root=self.staged((42,))
        parallel.run(root,'pilot',workers=1,wall_seconds=30,cleanup_seconds=1)
        original=read(root/'wall-budget.json');original['deadlineEpoch']=0
        write(root/'wall-budget.json',original)
        before=(root/'wall-budget.json').read_bytes()
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        manifest_sha=digest(root/'execution-manifest.json')
        resource=root/'resource.json'
        write(resource,dict(executionManifestSha256=manifest_sha,controllerSha256=digest(Path(parallel.__file__)),
                            workers=1,jobRssLimitBytes=1024**3,reason='Explicit continuation'))
        policy=root/'session.json'
        import network_additional_session
        receipt=next((root/'rows').glob('*.json'))
        write(policy,dict(sessionId='test-v1',executionManifestSha256=manifest_sha,
                          sessionHelperSha256=digest(Path(network_additional_session.__file__)),
                          controllerSha256=digest(Path(parallel.__file__)),workers=1,wallLimitSeconds=21600,
                          originalBudgetSha256=digest(root/'wall-budget.json'),
                          originalReceipts={receipt.relative_to(root).as_posix():digest(receipt)},
                          allowedJobIds=['pilot.s42'],retryReasons={},reason='Inspected test continuation'))
        result=parallel.run(root,'pilot',workers=1,resume=True,resource_decision=resource,session_decision=policy)
        self.assertTrue(result['reverifiedOnly'])
        self.assertEqual(before,(root/'wall-budget.json').read_bytes())
        self.assertEqual(len(list((root/'runs').glob('*/attempt-*'))),1)
if __name__=='__main__':unittest.main()
