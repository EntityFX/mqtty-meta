import base64
import importlib.util
import json
import pathlib
import tempfile
import unittest


class QuantileEstimandTests(unittest.TestCase):
    def test_pooled_quantile_is_not_average_of_repeat_quantiles(self):
        path = pathlib.Path(__file__).with_name('write-service-budget-report.py')
        spec = importlib.util.spec_from_file_location('service_report', path)
        report = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(report)
        histogram = {'points': [{'messageBytes': 16, 'qos': 1, 'publishers': 64,
            'repeats': [{'bins': [[1000, 100]]}, {'bins': [[1000, 100]]}, {'bins': [[2000, 100]]}]}]}
        with tempfile.TemporaryDirectory() as temporary:
            profile = pathlib.Path(temporary) / 'profile.json'
            profile.write_text(json.dumps({'histogramsBase64': base64.b64encode(json.dumps(histogram).encode()).decode()}))
            result = report.quantile_estimands(profile)[(16, 1, 64)]
        self.assertEqual([1, 1, 2], result['repeatP99Ms'])
        self.assertAlmostEqual(4 / 3, result['meanRepeatP99Ms'])
        self.assertEqual(2, result['pooledP99Ms'])


if __name__ == '__main__':
    unittest.main()
