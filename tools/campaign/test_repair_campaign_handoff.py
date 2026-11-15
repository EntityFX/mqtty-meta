import importlib.util
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('repair', pathlib.Path(__file__).with_name('repair-campaign-handoff.py'))
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class RepairHandoffTests(unittest.TestCase):
    def setup_tree(self, root):
        old, new, patches = root / 'old/controller', root / 'new/controller', root / 'patches'
        write = repair.write
        plan = {'rawControllerRoot': str(old.parent/'raw'), 'deadlineUtc': '2026-10-19T05:00:00+00:00',
                'programAmendmentNote': '', 'simulatorCli': 'unchanged-simulator', 'benchmarkCli': 'unchanged-measurement-cli'}
        write(old/'series.json', plan)
        write(old/'frozen-files.json', {})
        write(old/'inputs/Aedes-calibration.json', {'exact': 'input'})
        write(old/'environment.json', {'simulatorCommit': 'same-model'})
        write(old/'brokers/Aedes/broker-calibration.v3.json', {'exact': 'frozen-profile'})
        profile_sha = repair.sha(old/'brokers/Aedes/broker-calibration.v3.json')
        stage = old/'brokers/Aedes/simulations/fidelity-2s-overnight-v1'
        write(stage/'index.json', {'runs': [{'key': 'm16.q0.p1.s42', 'directory': '/original/evidence/run'}]})
        write(stage/'m16.q0.p1.s42.json', {'brokerProfilePath': '/original/profile'})
        write(stage/'m16.q0.p1.s43.json', {'unfinished': True})
        write(old.parent/'raw/ActiveMQ-calibration/attempts/key/attempt-01/result.json', {'status': 'success', 'key': {'key': 'partial'}})
        write(old.parent/'raw/Mosquitto-validation/attempts/key/attempt-01/started.json', {'status': 'started'})
        for name in ('compact-campaign.py', 'start-repaired-controller.py', 'run.py', 'BrokerStand.ps1', 'RemoteNativeStand.ps1'):
            write(patches/name, {'revision': 'fixed'})
        write(patches/'manifest.json', {'revision': 'fixed'})
        state = {'status': 'running', 'completed': ['Aedes/fidelity'], 'profiles': {'Aedes': profile_sha},
                 'stageFailures': {'Mosquitto/validation': 'old archive error'},
                 'brokers': {'Aedes': {'status': 'этап завершён'}, 'ActiveMQ': {'status': 'failed'}}}
        return old, new, patches, state

    def test_preserves_profiles_indices_inputs_deadline_and_original_failure_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, patches, state = self.setup_tree(pathlib.Path(tmp))
            plan = repair.prepare(old, new, patches, state)
            self.assertEqual('2026-10-19T05:00:00+00:00', plan['deadlineUtc'])
            self.assertEqual('unchanged-simulator', plan['simulatorCli'])
            self.assertEqual('unchanged-measurement-cli', plan['benchmarkCli'])
            self.assertEqual(repair.sha(old/'brokers/Aedes/broker-calibration.v3.json'), repair.sha(new/'brokers/Aedes/broker-calibration.v3.json'))
            self.assertEqual(repair.read(old/'brokers/Aedes/simulations/fidelity-2s-overnight-v1/index.json'), repair.read(new/'brokers/Aedes/simulations/fidelity-2s-overnight-v1/index.json'))
            self.assertIn('Mosquitto/validation', repair.read(new/'source-state.json')['stageFailures'])
            self.assertNotIn('stageFailures', repair.read(new/'state.json'))
            self.assertIn('stageFailures', state)
            self.assertTrue((new.parent/'raw/Mosquitto-validation/attempts/key/attempt-01/started.json').exists())
            self.assertFalse((new/'brokers/Aedes/simulations/fidelity-2s-overnight-v1/m16.q0.p1.s43.json').exists())
            self.assertTrue((old/'brokers/Aedes/simulations/fidelity-2s-overnight-v1/m16.q0.p1.s43.json').exists())

    def test_partial_activemq_matrix_is_not_merged_into_repaired_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, patches, state = self.setup_tree(pathlib.Path(tmp))
            repair.prepare(old, new, patches, state)
            self.assertTrue((old.parent/'raw/ActiveMQ-calibration').exists())
            self.assertFalse((new.parent/'raw/ActiveMQ-calibration').exists())
            self.assertEqual(1, len(repair.read(new/'repair-transition.json')['excludedIncompleteMatrices']))

    def test_frozen_file_drift_prevents_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, patches, state = self.setup_tree(pathlib.Path(tmp))
            repair.write(old/'frozen-files.json', {str(old/'inputs/Aedes-calibration.json'): 'wrong-sha'})
            with self.assertRaisesRegex(RuntimeError, 'Source frozen'):
                repair.prepare(old, new, patches, state)
            self.assertFalse(new.exists())

    def test_incomplete_completed_matrix_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new, patches, state = self.setup_tree(pathlib.Path(tmp))
            state['completed'].append('Mosquitto/validation')
            with self.assertRaisesRegex(RuntimeError, 'not whole and sealed'):
                repair.prepare(old, new, patches, state)


if __name__ == '__main__':
    unittest.main()
