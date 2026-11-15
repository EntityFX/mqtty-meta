from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parent))
from queue_window_analysis import delivery_window


class WindowTests(unittest.TestCase):
    def test_warmup_duplicates_and_four_receivers(self):
        pubs=[dict(id=1,tick=0,measurement=False),dict(id=2,tick=10,measurement=True),dict(id=3,tick=20,measurement=True)]
        receipts=[dict(publicationId=1,receiver=0,tick=10),dict(publicationId=2,receiver=0,tick=15),
                  dict(publicationId=2,receiver=0,tick=16),dict(publicationId=3,receiver=1,tick=25)]
        result=delivery_window(pubs,receipts,[0,1,2,3],10,30,1)
        self.assertEqual(result['expectedPairs'],8)
        self.assertEqual(result['uniquePairs'],2)
        self.assertEqual(result['duplicateReceipts'],1)
        self.assertEqual(result['notObservedFraction'],.75)

    def test_late_delivery_excluded_and_cutoff_inclusive(self):
        pubs=[dict(id=2,tick=10,measurement=True)]
        receipts=[dict(publicationId=2,receiver=0,tick=20),dict(publicationId=2,receiver=1,tick=21)]
        result=delivery_window(pubs,receipts,[0,1],10,20,1)
        self.assertEqual(result['uniquePairs'],1)
        self.assertEqual(result['curve'][-1]['uniquePairs'],1)

    def test_delivery_before_creation_rejected(self):
        with self.assertRaises(ValueError):
            delivery_window([dict(id=2,tick=10,measurement=True)],
                            [dict(publicationId=2,receiver=0,tick=9)],[0],10,20,1)


if __name__=='__main__':unittest.main()
