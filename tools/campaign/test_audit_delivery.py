import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('audit', pathlib.Path(__file__).with_name('audit-delivery.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def archive(self, root, tamper=False, miscount=False):
        result = {'identity': {}, 'key': {}, 'observation': {'drainReason': 'quietPeriod', 'drainSeconds': 1,
            'measurement': {'attemptedPublishes': 2, 'completedPublishes': 2, 'failedPublishes': 0,
            'uniqueDeliveries': 2 if miscount else 1, 'duplicateDeliveries': 2, 'unexpectedDeliveries': 2,
            'deliveredAfterFailedPublish': 0, 'completedIdsDelivered': 1, 'publishLatencyMs': {'p99Ms': .2}}}}
        events = [{'event': 'attempt', 'id': key, 'publisher': 0, 'sequence': index} for index, key in enumerate(('a', 'b'))]
        events += [{'event': 'completed', 'id': key, 'latencyMs': latency, 'elapsedSeconds': 1}
                   for key, latency in (('a', .1), ('b', .2))]
        files = {'result.json': json.dumps(result).encode(),
                 'publishes.ndjson': '\n'.join(json.dumps(event) for event in events).encode(),
                 'deliveries.ndjson': '\n'.join(json.dumps({'id': key, 'elapsedSeconds': 2}) for key in ('a', 'a', 'unknown', 'unknown')).encode()}
        seals = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        if tamper:
            files['deliveries.ndjson'] += b'\n'
        path = pathlib.Path(root) / 'evidence.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr('sha256.json', json.dumps(seals))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_recount_distinguishes_loss_duplicates_and_unknown_ids(self):
        with tempfile.TemporaryDirectory() as root:
            path, digest = self.archive(root)
            value = audit.audit_archive(path, digest)
            self.assertEqual({}, value['differences'])
            self.assertEqual(.5, value['lossRate'])
            self.assertEqual(0, value['unsettled'])
            self.assertEqual(2, value['recounted']['duplicateDeliveries'])
            self.assertEqual(2, value['recounted']['unexpectedDeliveries'])

    def test_archive_and_raw_seals_are_both_required(self):
        with tempfile.TemporaryDirectory() as root:
            path, digest = self.archive(root, tamper=True)
            with self.assertRaisesRegex(ValueError, 'Raw seal'):
                audit.audit_archive(path, digest)
            with self.assertRaisesRegex(ValueError, 'Archive SHA'):
                audit.audit_archive(path, '0' * 64)

    def test_recorded_summary_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as root:
            path, digest = self.archive(root, miscount=True)
            self.assertIn('uniqueDeliveries', audit.audit_archive(path, digest)['differences'])


if __name__ == '__main__':
    unittest.main()
