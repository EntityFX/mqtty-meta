from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import network_review
from network_study_verification import verify_loaded_relay, verify_loaded_run

import network_study_inputs as inputs
from network_study_process import execute, rss_bytes
import network_runner_test_fixture as fixture
import run_network_study as runner


class NetworkRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.mount = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch('network_study_inputs.Path.is_mount', return_value=True).start()
        patch('network_study_inputs.shutil.disk_usage', return_value=type('Disk', (), {'free': 1024**4})()).start()

    def staged(self, seeds=(42,)):
        program, runtime = fixture.program(self.mount, seeds), fixture.runtime(self.mount)
        root = self.mount / 'study'
        def command(args, **kwargs):
            if args[0] == 'git':
                return '' if 'status' in args else 'synthetic-test-revision\n'
            return 'Synthetic test host, not a .NET runtime\n'
        with patch('network_study_inputs.shutil.which', return_value=sys.executable), patch('network_study_inputs.subprocess.check_output', side_effect=command):
            inputs.stage(program, runtime, root, self.mount, Path(__file__).resolve().parents[2])
        original = runner.execute
        def fast(*args, **kwargs):
            return original(*args, **kwargs, sample_seconds=.02)
        patch('run_network_study.execute', side_effect=fast).start()
        return root

    def decision(self, root):
        path = self.mount / 'decision.json'
        inputs.write(path, {'decision':'accept','executionManifestSha256':inputs.sha(root/'execution-manifest.json'),
            'pilotResultsSha256':inputs.sha(root/'results-pilot.json'),'reviewer':'synthetic unit test',
            'reason':'Exercise the explicit phase gate; not scientific acceptance'})
        return path

    def test_phase_gate_resume_and_exact_replay(self):
        root = self.staged()
        with self.assertRaises(ValueError): runner.run(root, 'matrix')
        self.assertEqual(runner.run(root, 'pilot')['verified'], 1)
        with self.assertRaises(ValueError): runner.run(root, 'pilot')
        self.assertTrue(runner.run(root, 'pilot', resume=True)['reverifiedOnly'])
        self.assertEqual(len(list((root/'runs/pilot.0').glob('attempt-*'))), 1)
        runner.run(root, 'matrix', decision_path=self.decision(root))
        runner.run(root, 'replay')
        a=inputs.read(root/'rows/matrix.0.attempt-001.json');b=inputs.read(root/'rows/replay.0.attempt-001.json')
        self.assertEqual(a['scientificSha256'],b['scientificSha256'])
        self.assertTrue((root/'summary-replay.csv').exists())

    def test_failure_stops_next_jobs_and_inspected_retry_retains_first_attempt(self):
        root=self.staged((99,42))
        with self.assertRaisesRegex(RuntimeError,'exit code 2'): runner.run(root,'pilot')
        old=(root/'rows/pilot.0.attempt-001.json').read_bytes()
        self.assertFalse((root/'runs/pilot.1').exists())
        with self.assertRaisesRegex(ValueError,'explicit inspected retry'): runner.run(root,'pilot',resume=True)
        runner.run(root,'pilot',resume=True,retry_job='pilot.0',retry_reason='Synthetic transient failure inspected')
        self.assertEqual((root/'rows/pilot.0.attempt-001.json').read_bytes(),old)
        self.assertTrue((root/'runs/pilot.0/attempt-002/output/summary.json').exists())
        self.assertIn('inspected',inputs.read(root/'runs/pilot.0/attempt-002/command.json')['retryReason'])

    def test_model_loss_is_warning_and_does_not_stop_phase(self):
        root=self.staged((100,42));runner.run(root,'pilot')
        row=inputs.read(root/'rows/pilot.0.attempt-001.json')
        self.assertIsNone(row['technicalError']);self.assertIn('deliveryLoss',row['warnings'])
        self.assertEqual(inputs.read(root/'progress.json')['warnings'],1)

    def test_loaded_relay_does_not_assume_every_source_reaches_helper(self):
        root=self.staged((100,));runner.run(root,'pilot')
        out=root/'runs/pilot.0/attempt-001/output';o=inputs.read(out/'run-config.json')
        o.update(mode='relay',brokers=2);inputs.write(out/'run-config.json',o)
        seals=inputs.read(out/'sha256.json');seals['run-config.json']=inputs.sha(out/'run-config.json');inputs.write(out/'sha256.json',seals)
        with self.assertRaises(AssertionError):network_review.evidence.verify_run(out)
        self.assertEqual(verify_loaded_run(out)['deliveryLoss'],1)
        pubs={1:{'id':1,'origin':0}}
        valid={'role':'forward','publicationId':1,'site':0,'target':1,'topic':'federation/routing/0'}
        verify_loaded_relay(o,pubs,[valid])
        with self.assertRaises(AssertionError):verify_loaded_relay(o,pubs,[valid,valid])
        wrong=valid.copy();wrong['site']=1
        with self.assertRaises(AssertionError):verify_loaded_relay(o,pubs,[wrong])

    def test_resume_rejects_corrupt_raw_even_if_seal_is_rewritten(self):
        root=self.staged();runner.run(root,'pilot')
        out=root/'runs/pilot.0/attempt-001/output';s=inputs.read(out/'summary.json');s['uniqueDuringDrain']=0
        inputs.write(out/'summary.json',s)
        seals=inputs.read(out/'sha256.json');seals['summary.json']=inputs.sha(out/'summary.json');inputs.write(out/'sha256.json',seals)
        with self.assertRaises(AssertionError):runner.run(root,'pilot',resume=True)

    def test_resume_recovers_durable_index_without_rerunning_simulation(self):
        root=self.staged();runner.run(root,'pilot')
        index=root/'rows/pilot.0.attempt-001.json';expected=index.read_bytes();index.unlink()
        runner.run(root,'pilot',resume=True)
        self.assertEqual(index.read_bytes(),expected)
        self.assertEqual(len(list((root/'runs/pilot.0').glob('attempt-*'))),1)

    def test_unfinished_attempt_never_restarts_silently(self):
        root=self.staged();(root/'runs/pilot.0/attempt-001').mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError,'Unfinished attempt'):runner.run(root,'pilot',resume=True)

    def test_inspected_interruption_requires_dead_child_and_retains_unknown_exit_code(self):
        root=self.staged();attempt=root/'runs/pilot.0/attempt-001';attempt.mkdir(parents=True)
        inputs.write(attempt/'command.json',{'job':'pilot.0','executionManifestSha256':inputs.sha(root/'execution-manifest.json')})
        with patch('run_network_study.processes_in_attempt',return_value=[123]):
            with self.assertRaisesRegex(RuntimeError,'still uses'):runner.recover_interruption(root,'pilot.0','unit killed externally')
        with patch('run_network_study.processes_in_attempt',return_value=[]):
            result=runner.recover_interruption(root,'pilot.0','unit killed externally')
        self.assertFalse(result['retryExecuted'])
        row=inputs.read(attempt/'result.json');self.assertIsNone(row['returnCode']);self.assertTrue(row['exitCodeUnavailable'])
        self.assertEqual(inputs.read(root/'progress.json')['status'],'failed')
        runner.run(root,'pilot',resume=True,retry_job='pilot.0',retry_reason='Interruption inspected')
        self.assertEqual(inputs.read(attempt/'result.json'),row)

    def test_snapshot_rejects_mutation_additional_runtime_and_path_escape(self):
        root=self.staged();config=next((root/'program/configs').glob('*.json'));raw=config.read_bytes()
        config.write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError,'Snapshot changed'):inputs.verify_snapshot(root)
        config.write_bytes(raw);(root/'runtime/extra.dll').write_text('injected')
        with self.assertRaisesRegex(ValueError,'Unexpected'):inputs.verify_snapshot(root)
        for name in ('../escape','C:/escape','a\\escape','/absolute'):
            with self.assertRaises(ValueError):inputs.contained(root,name)

    def test_mount_loss_low_space_and_budget_stop_owned_child(self):
        root=self.mount/'study';root.mkdir();guard=inputs.StorageGuard(self.mount,root,5*1024**3)
        script=self.mount/'child.py';script.write_text('import time;print("started",flush=True);time.sleep(20)')
        for reason,timeout,rss,budget in [('jobTimeout',.05,1024**3,1024**3),('jobRssLimit',10,1,1024**3),('studyDiskBudget',10,1024**3,0)]:
            attempt=root/reason;attempt.mkdir()
            row=execute([sys.executable,str(script)],attempt,guard,timeout,rss,budget,lambda _:None,sample_seconds=.02)
            self.assertIn(reason,row['technicalError']);self.assertLess(row['processWallSeconds'],5)
        with patch('network_study_inputs.Path.is_mount',return_value=False):
            with self.assertRaises(RuntimeError):guard.save(root/'unsafe.json',{})
        self.assertFalse((root/'unsafe.json').exists())
        with patch('network_study_inputs.shutil.disk_usage',return_value=type('Disk',(),{'free':1})()):
            with self.assertRaisesRegex(RuntimeError,'reserve'):guard.check()

    def test_mount_loss_during_process_and_growing_logs_stop_child(self):
        root=self.mount/'study';root.mkdir();guard=inputs.StorageGuard(self.mount,root,5*1024**3)
        script=self.mount/'child.py';script.write_text('import time;print("enough output",flush=True);time.sleep(20)')
        attempt=root/'lost';attempt.mkdir();calls=[]
        def lost():
            calls.append(1)
            if len(calls)>1:raise RuntimeError('Evidence mount missing')
        with patch.object(guard,'check',side_effect=lost):
            row=execute([sys.executable,str(script)],attempt,guard,10,1024**3,1024**3,lambda _:None,sample_seconds=.02)
        self.assertIn('mount missing',row['technicalError']);self.assertIsNotNone(row['returnCode'])
        attempt=root/'budget';attempt.mkdir()
        row=execute([sys.executable,str(script)],attempt,guard,10,1024**3,1,lambda _:None,sample_seconds=.02)
        self.assertIn('studyDiskBudget',row['technicalError']);self.assertLess(row['processWallSeconds'],5)

    def test_staging_rejects_dirty_sources_and_existing_output(self):
        program,runtime=fixture.program(self.mount),fixture.runtime(self.mount)
        root=self.mount/'study'
        with patch('network_study_inputs.subprocess.check_output',return_value=' M modified.py\n'):
            with self.assertRaisesRegex(ValueError,'clean'):inputs.stage(program,runtime,root,self.mount,Path.cwd())
        self.assertFalse(root.exists());root.mkdir()
        with self.assertRaisesRegex(ValueError,'already exists'):inputs.stage(program,runtime,root,self.mount,Path.cwd())

    def test_conflicting_lock_and_running_dependency_are_rejected(self):
        root=self.staged()
        with inputs.lock(root/'run.lock'):
            with self.assertRaises(OSError):runner.run(root,'pilot')
        active=subprocess.CompletedProcess([],0,'LoadState=loaded\nActiveState=active\nResult=success\n','')
        with patch('run_network_study.subprocess.run',return_value=active):
            with self.assertRaisesRegex(RuntimeError,'Dependency'):runner.isolation(['busy.service'])

    def test_wrong_decision_and_changed_phase_result_cannot_unlock_matrix(self):
        root=self.staged();runner.run(root,'pilot');decision=self.decision(root)
        d=inputs.read(decision);d['pilotResultsSha256']='0'*64;inputs.write(decision,d)
        with self.assertRaises(ValueError):runner.run(root,'matrix',decision_path=decision)
        result=inputs.read(root/'results-pilot.json');result['rows']=[];inputs.write(root/'results-pilot.json',result)
        with self.assertRaisesRegex(ValueError,'Phase result'):runner.run(root,'pilot',resume=True)


if __name__=='__main__':
    unittest.main()
