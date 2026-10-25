"""Known event sequences for the saved subscriber-connection audit."""
import unittest

from audit_twin_connections import subscriber_coverage


class SubscriberCoverageTests(unittest.TestCase):
    def event(self, state, time, client='subscriber'):
        return dict(client=client, state=state, elapsedSeconds=time)

    def test_connection_spans_entire_window(self):
        result = subscriber_coverage([self.event('connected', .1), self.event('disconnected', 4)], 1, 3)
        self.assertEqual('continuousConnected', result['status'])

    def test_disconnect_before_window_is_detected(self):
        result = subscriber_coverage([self.event('connected', .1), self.event('disconnected', .4)], 1, 3)
        self.assertEqual('disconnectedAtStart', result['status'])

    def test_reconnect_does_not_erase_interruption(self):
        result = subscriber_coverage([self.event('connected', .1), self.event('disconnected', 2), self.event('connected', 2.5)], 1, 3)
        self.assertEqual('interrupted', result['status'])

    def test_missing_subscriber_events_are_unobserved(self):
        result = subscriber_coverage([self.event('connected', .1, 'publisher-0')], 1, 3)
        self.assertEqual('unobserved', result['status'])

    def test_disconnect_at_right_boundary_is_outside_window(self):
        result = subscriber_coverage([self.event('connected', .1), self.event('disconnected', 3)], 1, 3)
        self.assertEqual('continuousConnected', result['status'])


if __name__ == '__main__':
    unittest.main()
