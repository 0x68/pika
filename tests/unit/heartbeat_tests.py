"""
Tests for pika.heartbeat

"""
import unittest

import mock

from pika import connection, frame, heartbeat
import pika.exceptions


# protected-access
# pylint: disable=W0212

# missing-docstring
# pylint: disable=C0111

# invalid-name
# pylint: disable=C0103


class ConstructableConnection(connection.Connection):
    """Adds dummy overrides for `Connection`'s abstract methods so
    that we can instantiate and test it.

    """
    def _adapter_connect_stream(self):
        pass

    def _adapter_disconnect_stream(self):
        raise NotImplementedError

    def add_timeout(self, deadline, callback):
        raise NotImplementedError

    def remove_timeout(self, timeout_id):
        raise NotImplementedError

    def _adapter_emit_data(self, data):
        raise NotImplementedError

    def _adapter_get_write_buffer_size(self):
        raise NotImplementedError

    def _adapter_add_callback_threadsafe(self):
        raise NotImplementedError

    def _adapter_add_timeout(self):
        raise NotImplementedError

    def _adapter_remove_timeout(self):
        raise NotImplementedError


class HeartbeatTests(unittest.TestCase):

    TIMEOUT = 60
    HALF_TIMEOUT = TIMEOUT / 2

    def setUp(self):
        self.mock_conn = mock.Mock(spec_set=ConstructableConnection())
        self.mock_conn.bytes_received = 100
        self.mock_conn.bytes_sent = 100
        self.mock_conn._heartbeat_checker = mock.Mock(spec=heartbeat.HeartbeatChecker)
        self.obj = heartbeat.HeartbeatChecker(self.mock_conn, self.TIMEOUT)

    def tearDown(self):
        del self.obj
        del self.mock_conn

    def test_default_initialization_interval(self):
        self.assertEqual(self.obj._timeout, self.HALF_TIMEOUT)

    def test_constructor_assignment_connection(self):
        self.assertIs(self.obj._connection, self.mock_conn)

    def test_constructor_assignment_heartbeat_interval(self):
        self.assertEqual(self.obj._timeout, self.HALF_TIMEOUT)

    def test_constructor_initial_bytes_received(self):
        self.assertEqual(self.obj._bytes_received, 0)

    def test_constructor_initial_bytes_sent(self):
        self.assertEqual(self.obj._bytes_received, 0)

    def test_constructor_initial_heartbeat_frames_received(self):
        self.assertEqual(self.obj._heartbeat_frames_received, 0)

    def test_constructor_initial_heartbeat_frames_sent(self):
        self.assertEqual(self.obj._heartbeat_frames_sent, 0)

    def test_constructor_initial_idle_byte_intervals(self):
        self.assertEqual(self.obj._idle_byte_intervals, 0)

    @mock.patch('pika.heartbeat.HeartbeatChecker._setup_timer')
    def test_constructor_called_setup_timer(self, timer):
        heartbeat.HeartbeatChecker(self.mock_conn, self.TIMEOUT)
        timer.assert_called_once_with()

    def test_active_true(self):
        self.mock_conn._heartbeat_checker = self.obj
        self.assertTrue(self.obj.active)

    def test_active_false(self):
        self.mock_conn._heartbeat_checker = mock.Mock()
        self.assertFalse(self.obj.active)

    def test_bytes_received_on_connection(self):
        self.mock_conn.bytes_received = 128
        self.assertEqual(self.obj.bytes_received_on_connection, 128)

    def test_connection_is_idle_false(self):
        self.assertFalse(self.obj.connection_is_idle)

    def test_connection_is_idle_true(self):
        self.obj._idle_byte_intervals = self.TIMEOUT
        self.assertTrue(self.obj.connection_is_idle)

    def test_received(self):
        self.obj.received()
        self.assertTrue(self.obj._heartbeat_frames_received, 1)

    @mock.patch('pika.heartbeat.HeartbeatChecker._close_connection')
    def test_send_and_check_not_closed(self, close_connection):
        obj = heartbeat.HeartbeatChecker(self.mock_conn, self.TIMEOUT)
        obj.send_and_check()
        close_connection.assert_not_called()

    @mock.patch('pika.heartbeat.HeartbeatChecker._close_connection')
    def test_send_and_check_missed_bytes(self, close_connection):
        obj = heartbeat.HeartbeatChecker(self.mock_conn, self.TIMEOUT)
        obj._idle_byte_intervals = self.TIMEOUT
        obj.send_and_check()
        close_connection.assert_called_once_with()

    def test_send_and_check_increment_no_bytes(self):
        self.mock_conn.bytes_received = 100
        self.obj._bytes_received = 100
        self.obj.send_and_check()
        self.assertEqual(self.obj._idle_byte_intervals, 1)

    def test_send_and_check_increment_bytes(self):
        self.mock_conn.bytes_received = 100
        self.obj._bytes_received = 128
        self.obj.send_and_check()
        self.assertEqual(self.obj._idle_byte_intervals, 0)

    @mock.patch('pika.heartbeat.HeartbeatChecker._update_counters')
    def test_send_and_check_update_counters(self, update_counters):
        obj = heartbeat.HeartbeatChecker(self.mock_conn, self.TIMEOUT)
        obj.send_and_check()
        update_counters.assert_called_once_with()

    @mock.patch('pika.heartbeat.HeartbeatChecker._send_heartbeat_frame')
    def test_send_and_check_send_heartbeat_frame(self, send_heartbeat_frame):
        obj = heartbeat.HeartbeatChecker(self.mock_conn, self.TIMEOUT)
        obj.send_and_check()
        send_heartbeat_frame.assert_called_once_with()

    @mock.patch('pika.heartbeat.HeartbeatChecker._start_timer')
    def test_send_and_check_start_timer(self, start_timer):
        obj = heartbeat.HeartbeatChecker(self.mock_conn, self.TIMEOUT)
        obj.send_and_check()
        start_timer.assert_called_once_with()

    def test_connection_close(self):
        self.obj._idle_byte_intervals = 3
        self.obj._idle_heartbeat_intervals = 4
        self.obj._close_connection()
        reason = self.obj._STALE_CONNECTION % (
            heartbeat.HeartbeatChecker._MAX_IDLE_COUNT * self.obj._timeout)
        self.mock_conn._terminate_stream.assert_called_once_with(mock.ANY)

        self.assertIsInstance(self.mock_conn._terminate_stream.call_args[0][0],
                              pika.exceptions.AMQPHeartbeatTimeout)
        self.assertEqual(
            self.mock_conn._terminate_stream.call_args[0][0].args[0],
            reason)

    def test_has_received_data_false(self):
        self.obj._bytes_received = 100
        self.assertFalse(self.obj._has_received_data)

    def test_has_received_data_true(self):
        self.mock_conn.bytes_received = 128
        self.obj._bytes_received = 100
        self.assertTrue(self.obj._has_received_data)

    def test_new_heartbeat_frame(self):
        self.assertIsInstance(self.obj._new_heartbeat_frame(), frame.Heartbeat)

    def test_send_heartbeat_send_frame_called(self):
        frame_value = self.obj._new_heartbeat_frame()
        with mock.patch.object(self.obj, '_new_heartbeat_frame') as new_frame:
            new_frame.return_value = frame_value
            self.obj._send_heartbeat_frame()
            self.mock_conn._send_frame.assert_called_once_with(frame_value)

    def test_send_heartbeat_counter_incremented(self):
        self.obj._send_heartbeat_frame()
        self.assertEqual(self.obj._heartbeat_frames_sent, 1)

    def test_setup_timer_called(self):
        self.mock_conn._adapter_add_timeout.assert_called_once_with(
            self.HALF_TIMEOUT, self.obj.send_and_check)

    @mock.patch('pika.heartbeat.HeartbeatChecker._setup_timer')
    def test_start_timer_not_active(self, setup_timer):
        self.obj._start_timer()
        setup_timer.assert_not_called()

    @mock.patch('pika.heartbeat.HeartbeatChecker._setup_timer')
    def test_start_timer_active(self, setup_timer):
        self.mock_conn._heartbeat_checker = self.obj
        self.obj._start_timer()
        self.assertTrue(setup_timer.called)

    def test_update_counters_bytes_received(self):
        self.mock_conn.bytes_received = 256
        self.obj._update_counters()
        self.assertEqual(self.obj._bytes_received, 256)

    def test_update_counters_bytes_sent(self):
        self.mock_conn.bytes_sent = 256
        self.obj._update_counters()
        self.assertEqual(self.obj._bytes_sent, 256)
