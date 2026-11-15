import json
import unittest
from review_twin_article import ROOT, ARTICLE, verify, verify_native_model


class ArticleReviewChecks(unittest.TestCase):
    def setUp(self):
        self.text = ARTICLE.read_text(encoding='utf-8')
        self.accuracy = json.loads((ROOT/'reports/audits/dissertation/accuracy-analysis-v1/analysis.json').read_bytes())
        self.sensitivity = json.loads((ROOT/'reports/audits/dissertation/sensitivity-final-v1/analysis/analysis.json').read_bytes())

    def test_actual_tables_match_sources(self):
        self.assertEqual(len(verify(self.text, self.accuracy, self.sensitivity)), 4)

    def test_corrupted_accuracy_cell_is_detected(self):
        text = self.text.replace('| Aedes | 18 | 3 | 3 |', '| Aedes | 19 | 3 | 3 |')
        with self.assertRaisesRegex(ValueError, 'accuracy row'):
            verify(text, self.accuracy, self.sensitivity)

    def test_corrupted_condition_count_is_detected(self):
        text = self.text.replace('| ActiveMQ | 9 | 2 | 13 |', '| ActiveMQ | 10 | 2 | 13 |')
        with self.assertRaisesRegex(ValueError, 'accuracy row'):
            verify(text, self.accuracy, self.sensitivity)

    def test_loss_of_independence_caveat_is_detected(self):
        with self.assertRaisesRegex(ValueError, 'limitation'):
            verify(self.text.replace('независимость контрольной серии от разработки модели не подтверждена','независимость подтверждена'), self.accuracy, self.sensitivity)

    def test_native_model_table_uses_actual_native_repetitions(self):
        models=json.loads((ROOT/'reports/data/model-evidence.json').read_bytes())
        native=json.loads((ROOT/'reports/data/native-evidence.json').read_bytes())
        self.assertEqual(verify_native_model(self.text,models,native),16)
        with self.assertRaisesRegex(ValueError,'native/model'):
            verify_native_model(self.text.replace('| 53228.383 | 53185.000 |','| 53228.383 | 53228.383 |'),models,native)


if __name__ == '__main__':
    unittest.main()
