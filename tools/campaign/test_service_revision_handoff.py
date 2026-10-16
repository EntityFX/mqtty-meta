import importlib.util
import pathlib
import tempfile
from unittest import mock
import unittest

spec = importlib.util.spec_from_file_location('handoff', pathlib.Path(__file__).with_name('service-revision-handoff.py'))
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class ServiceHandoffTests(unittest.TestCase):
    def test_no_admission_while_any_workload_is_active(self):
        self.assertFalse(handoff.idle({'status': 'running', 'active': {'tag': 'matrix'}}))
        self.assertFalse(handoff.idle({'status': 'running', 'activeSimulations': [{'pid': 42}]}))
        self.assertFalse(handoff.idle({'status': 'failed'}))
        self.assertTrue(handoff.idle({'status': 'running', 'activeSimulations': []}))

    def test_native_reuse_requires_exact_whole_matrix_and_preserves_failed_attempts(self):
        rows = [{'status': 'success', 'key': {'key': f'Aedes.m{size}.q{qos}.p{n}.r{repeat}'}}
                for size in (16, 256) for qos in (0, 1, 2) for n in (1, 16, 64, 128) for repeat in (1, 2, 3)]
        self.assertTrue(handoff.full_native(rows, 'Aedes'))
        self.assertFalse(handoff.full_native(rows[:-1], 'Aedes'))
        self.assertFalse(handoff.full_native(rows + [rows[0]], 'Aedes'))
        self.assertFalse(handoff.full_native(rows, 'Mosquitto'))
        self.assertTrue(handoff.full_native(rows + [{'status': 'failed', 'key': rows[0]['key']}], 'Aedes'))

    def test_adoption_preserves_old_identity_and_profile_but_restarts_partial_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = pathlib.Path(temporary)
            old, new = mount / 'old/controller', mount / 'new/controller'
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                handoff.write(path, value)
            runtime = mount / 'calibrator/calibrator.dll'
            runtime.parent.mkdir()
            runtime.write_bytes(b'pinned calibrator')
            common = {'archiveMount': str(mount), 'deadlineUtc': '2026-10-17T05:00:00+00:00',
                'calibratorCli': str(runtime), 'calibratorRepository': 'frozen-calibrator',
                'calibratorCliSha256': handoff.sha(runtime)}
            write(old / 'series.json', common | {'rawControllerRoot': str(old.parent / 'raw')})
            write(new / 'series.json', common | {'rawControllerRoot': str(new.parent / 'raw'),
                'calibratorRepository': 'new-simulator', 'modelRevisionNote': '', 'programAmendmentNote': ''})
            write(new / 'state.json', {'status': 'prepared', 'completed': [], 'brokers': {}})
            write(new / 'environment.json', {})
            write(new / 'frozen-files.json', {})
            for broker in handoff.BROKERS:
                for root in (old, new):
                    write(root / 'inputs' / (broker + '-stand.json'), {'broker': broker})
                    for stage in ('calibration', 'validation'):
                        write(root / 'inputs' / f'{broker}-{stage}.json', {'measurementSeconds': 20, 'telemetryCaptureReserveSeconds': 3})
            raw = old.parent / 'raw/Aedes-calibration'
            identity = {'configSha256': handoff.sha(old / 'inputs/Aedes-calibration.json'),
                'standSha256': handoff.sha(old / 'inputs/Aedes-stand.json')}
            write(raw / 'campaign.json', identity)
            archive = mount / 'sealed-archive.zip'
            archive.write_bytes(b'sealed fixture bytes')
            for size in (16, 256):
                for qos in (0, 1, 2):
                    for publishers in (1, 16, 64, 128):
                        for repeat in (1, 2, 3):
                            key = f'Aedes.m{size}.q{qos}.p{publishers}.r{repeat}'
                            attempt = raw / 'attempts' / key / 'attempt-1'
                            write(attempt / 'result.json', {'status': 'success', 'key': {'key': key}, 'identity': identity})
                            write(attempt / 'archive.json', {'archivePath': str(archive), 'sha256': handoff.sha(archive)})
            write(old.parent / 'raw/Aedes-validation/attempts/key/attempt-1/result.json',
                  {'status': 'success', 'key': {'key': 'Aedes.m16.q0.p1.r1'}})
            observations = old / 'brokers/Aedes/calibration/broker-observations.v3.json'
            write(observations, {'runCount': 72})
            write(observations.with_name('archive-verification.json'), {'verified': True, 'originalSha256': handoff.sha(observations)})
            profile = old / 'brokers/Aedes/broker-calibration.v3.json'
            profile.write_bytes(b'frozen profile fixture')
            write(old / 'state.json', {'completed': ['Aedes/calibration'], 'profiles': {'Aedes': handoff.sha(profile)}})
            before = {str(p): handoff.sha(p) for p in old.parent.rglob('*') if p.is_file()}
            with mock.patch.object(handoff, 'storage'):
                origins = handoff.adopt(old, new)
            self.assertEqual(before, {str(p): handoff.sha(p) for p in old.parent.rglob('*') if p.is_file()})
            self.assertEqual(3, handoff.read(new / 'inputs/Aedes-calibration.json')['telemetryCaptureReserveSeconds'])
            self.assertEqual(18, handoff.read(new / 'inputs/Aedes-validation.json')['telemetryCaptureReserveSeconds'])
            self.assertEqual(20, handoff.read(new / 'inputs/Aedes-validation.json')['measurementSeconds'])
            self.assertEqual(handoff.sha(profile), handoff.sha(new / 'brokers/Aedes/broker-calibration.v3.json'))
            self.assertFalse((new.parent / 'raw/Aedes-validation').exists())
            self.assertIn('Aedes/calibration', handoff.read(new / 'state.json')['completed'])
            self.assertNotIn('Aedes/validation', handoff.read(new / 'state.json')['completed'])
            self.assertEqual('frozen-calibrator', handoff.read(new / 'series.json')['calibratorRepository'])


if __name__ == '__main__':
    unittest.main()
