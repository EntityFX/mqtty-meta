import unittest
from verify_forwarding_properties import execute_order, explore, connected_graphs


class ForwardingPropertiesTests(unittest.TestCase):
    def test_line_and_ring_exact_exports(self):
        for edges,expected in [([(0,1),(1,2),(2,3)],3), ([(0,1),(1,2),(2,3),(3,0)],5)]:
            outcomes,_=explore(4,edges,0,4)
            self.assertEqual(outcomes,{(15,expected,expected-3,0)})

    def test_diameter_limit_can_miss_recipient_after_long_first_copy(self):
        edges=[(0,1),(0,2),(1,2),(2,3)]
        result=execute_order(4,edges,0,2,[(None,0),(0,1),(1,2),(0,2)])
        self.assertEqual(result,(7,3,1,2))

    def test_delivery_limit_does_not_imply_exact_export_count(self):
        edges=[(a,b) for a in range(4) for b in range(a+1,4)]
        result=execute_order(4,edges,0,3,[(None,0),(0,1),(1,2),(2,3)])
        self.assertEqual(result[0],15)
        self.assertEqual(result[1],7)
        self.assertGreater(result[3],0)
        outcomes,_=explore(4,edges,0,4)
        self.assertEqual(outcomes,{(15,9,6,0)})

    def test_graph_enumerator_has_known_small_counts(self):
        self.assertEqual([len(list(connected_graphs(n))) for n in range(1,5)],[1,1,4,38])


if __name__=='__main__':unittest.main()
