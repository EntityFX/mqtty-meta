import socket
import unittest
from run_twin_native_micro import packet, receive, text


class MicroCodecTests(unittest.TestCase):
    def test_known_mqtt_connect_and_ack_encoding(self):
        self.assertEqual(b"\x40\x02\x00\x01", packet(0x40, b"\0\1"))
        self.assertEqual(b"\x00\x04MQTT", text("MQTT"))

    def test_remaining_length_multibyte_and_socket_reader(self):
        left, right = socket.socketpair()
        try:
            body = b"x" * 128
            self.assertEqual(b"\x30\x80\x01", packet(0x30, body)[:3])
            left.sendall(packet(0x30, body))
            self.assertEqual((0x30, body), receive(right))
        finally:
            left.close()
            right.close()

    def test_disconnected_peer_is_explicit(self):
        left, right = socket.socketpair()
        left.close()
        try:
            with self.assertRaises(RuntimeError):
                receive(right)
        finally:
            right.close()


if __name__ == "__main__":
    unittest.main()
