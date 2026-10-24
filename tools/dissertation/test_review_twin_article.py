import json
import unittest
from review_twin_article import ROOT, ARTICLE, verify


class ArticleReviewChecks(unittest.TestCase):
    def setUp(self):
        self.text = ARTICLE.read_text(encoding='utf-8')
        self.accuracy = json.loads((ROOT/'reports/audits/dissertation/accuracy-analysis-v1/analysis.json').read_bytes())
        self.sensitivity = json.loads((ROOT/'reports/audits/dissertation/sensitivity-final-v1/analysis/analysis.json').read_bytes())

    def test_actual_tables_match_sources(self):
        self.assertEqual(len(verify(self.text, self.accuracy, self.sensitivity)), 4)

    def test_corrupted_accuracy_cell_is_detected(self):
        text = self.text.replace('| Aedes | 58 | 14 |', '| Aedes | 59 | 14 |')
        with self.assertRaisesRegex(ValueError, 'accuracy row'):
            verify(text, self.accuracy, self.sensitivity)

    def test_corrupted_context_is_detected(self):
        text = self.text.replace('| ActiveMQ | 10 | 2 | 30/43 |', '| ActiveMQ | 10 | 2 | 31/43 |')
        with self.assertRaisesRegex(ValueError, 'context row'):
            verify(text, self.accuracy, self.sensitivity)

    def test_loss_of_independence_caveat_is_detected(self):
        with self.assertRaisesRegex(ValueError, 'limitation'):
            verify(self.text.replace('не установила слепую независимость','установила независимость'), self.accuracy, self.sensitivity)


if __name__ == '__main__':
    unittest.main()
