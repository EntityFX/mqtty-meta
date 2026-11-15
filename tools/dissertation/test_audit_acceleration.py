import pathlib
import unittest

import audit_acceleration as audit


class HistoricalAccelerationAuditTests(unittest.TestCase):
    def test_decimal_fraction_is_not_assumed_centiseconds(self):
        self.assertAlmostEqual(audit.seconds('01', '02', '5'), 62.5)
        self.assertAlmostEqual(audit.seconds('00', '03', '19'), 3.19)

    def test_actual_table_preserves_slowdown_and_platform_boundaries(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        rows = audit.table_rows((root / 'diss/Dissertation/chapter4.tex').read_text(encoding='utf-8'))
        self.assertEqual(len(rows), 32)
        xeon = next(r for r in rows if r['machine'] == 'Xeon E5-2680v3' and r['workload'] == 'B=4; N=2; C=50')
        self.assertAlmostEqual(xeon['ratio'], 7.2 / 4.9)
        elbrus = [r for r in rows if r['machine'] == 'Elbrus 8C v4']
        self.assertAlmostEqual(max(r['ratio'] for r in elbrus), 1689 / 585.7)
        self.assertEqual(sum(r['ratio'] < 1 for r in rows), 1)

    def test_truncated_table_fails_instead_of_partial_acceptance(self):
        with self.assertRaises(ValueError):
            audit.table_rows(r'\label{tab:AllCompare} B=4; N=2; C=3 & 00:00,5 & 00:00,5 \hline \label{tab:FinalCompare}')


if __name__ == '__main__':
    unittest.main()
