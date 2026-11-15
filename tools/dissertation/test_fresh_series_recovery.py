import json
from pathlib import Path
import tempfile
import socket
import unittest
from unittest.mock import MagicMock, patch
from run_fresh_twin_series import available_port, recovered_rows, mqtt_ready, reusable_model

class PortRecoveryTests(unittest.TestCase):
    def test_readiness_requires_full_success_connack(self):
        with patch('run_fresh_twin_series.socket.create_connection') as connect:
            connection=connect.return_value.__enter__.return_value
            connection.recv.side_effect=[b'\x20',b'\x02\x00\x00']
            mqtt_ready(33083)
            sent=connection.sendall.call_args_list[0].args[0]
            self.assertEqual(len(sent)-2,sent[1])
            self.assertEqual(b'\xe0\x00',connection.sendall.call_args_list[-1].args[0])

    def test_tcp_close_and_negative_connack_are_not_ready(self):
        for reply in (b'',b'\x20\x02\x00\x03'):
            with self.subTest(reply=reply),patch('run_fresh_twin_series.socket.create_connection') as connect:
                connect.return_value.__enter__.return_value.recv.return_value=reply
                with self.assertRaises(ValueError):mqtt_ready(33083)

    def test_valid_model_from_failed_native_is_reused_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            parent=Path(directory);case=parent/'attempt-001';(case/'model').mkdir(parents=True)
            job={'id':'case','qos':1};row={'model':{'verified':1},'nativeError':'connect closed'}
            (case/'config.json').write_text(json.dumps(job))
            (case/'result.json').write_text(json.dumps(row))
            trial={'runDirectory':'old-frozen-model'}
            (case/'model/trial.json').write_text(json.dumps(trial))
            with patch('run_fresh_twin_series.reverify_events',return_value=row['model']):
                result=reusable_model(parent,job)
                self.assertEqual(row,result[0]);self.assertEqual(trial,result[1])
            self.assertEqual(row,json.loads((case/'result.json').read_text()))

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
