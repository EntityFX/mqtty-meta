import copy
import unittest
from audit_connection_snapshot_order import audit


class SnapshotOrderTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'elapsedSeconds':0,'phase':'start','client':'a','connected':False},
                   {'elapsedSeconds':0,'phase':'start','client':'b','connected':False},
                   {'elapsedSeconds':1,'phase':'changed','client':'a','connected':True}]

    def test_allows_only_other_client_order_within_same_snapshot(self):
        self.assertTrue(audit(self.rows,[self.rows[1],self.rows[0],self.rows[2]])['sameOrderedSnapshotsAndPerClientEvents'])

    def test_rejects_changed_time_state_missing_or_duplicate_events(self):
        for change in ('time','state','missing','duplicate'):
            rows=copy.deepcopy(self.rows)
            if change=='time':rows[0]['elapsedSeconds']=.01
            if change=='state':rows[0]['connected']=True
            if change=='missing':rows.pop()
            if change=='duplicate':rows.insert(0,copy.deepcopy(rows[0]))
            with self.subTest(change=change):self.assertFalse(audit(self.rows,rows)['sameOrderedSnapshotsAndPerClientEvents'])

    def test_rejects_per_client_order_changes_at_same_time(self):
        a={'elapsedSeconds':0,'phase':'change','client':'a','connected':False}
        b={**a,'connected':True}
        self.assertFalse(audit([a,b],[b,a])['sameOrderedSnapshotsAndPerClientEvents'])


if __name__=='__main__':unittest.main()
