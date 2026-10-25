import json
from pathlib import Path
import tempfile
import socket
import unittest
from unittest.mock import MagicMock, patch
from run_fresh_twin_series import available_port, recovered_rows

class PortRecoveryTests(unittest.TestCase):
    def test_reuse_address_is_set_before_binding(self):
        with patch("run_fresh_twin_series.socket.socket") as factory:
            reservation = factory.return_value.__enter__.return_value
            calls = []
            reservation.setsockopt.side_effect=lambda *args:calls.append("reuse")
            reservation.bind.side_effect=lambda *args:calls.append("bind")
            available_port(33083)
            self.assertEqual(["reuse","bind"],calls)
            reservation.setsockopt.assert_called_once_with(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)

    def test_live_listener_is_not_replaced(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0));listener.listen()
            with self.assertRaises(OSError):available_port(listener.getsockname()[1])

    def test_complete_pair_is_verified_read_only_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);case=root/'runs'/'case'/'attempt-001';(case/'model').mkdir(parents=True)
            job={"id":"case","qos":1}; row={"id":"case","native":{"ok":1},"model":{"ok":2}}
            (case/'result.json').write_text(json.dumps(row));(case/'config.json').write_text(json.dumps(job))
            (case/'model'/'trial.json').write_text(json.dumps({"runDirectory":"unchanged-model"}))
            before=(case/'result.json').read_bytes()
            with patch('run_fresh_twin_series.reverify_events',side_effect=[row['native'],row['model']]):
                self.assertEqual([row],recovered_rows(root,{"jobs":[job]}))
            self.assertEqual(before,(case/'result.json').read_bytes())

    def test_empty_failed_attempt_is_not_counted_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'runs'/'case'/'attempt-001').mkdir(parents=True)
            self.assertEqual([],recovered_rows(root,{"jobs":[{"id":"case"}]}))

if __name__ == "__main__":unittest.main()
