# ***** BEGIN LICENSE BLOCK *****
#
# For copyright and licensing please refer to COPYING.
#
# ***** END LICENSE BLOCK *****
"""
Connection Test

First test to make sure all async adapters can connect properly
"""
import nose
import os
import platform
import sys
sys.path.append('..')
sys.path.append(os.path.join('..', '..'))

import pika
import pika.adapters as adapters
from pika.adapters.tornado_connection import IOLoop as tornado_ioloop

import warnings

from config import HOST, PORT


class TestAdapters(object):

    def __init__(self):
        self.connection = None
        self._timeout = False

    @nose.tools.timed(2)
    def test_asyncore_connection(self):
        self.connection = self._connect(adapters.AsyncoreConnection)
        self.connection.ioloop.start()
        if not self.connected:
            assert False, "Not Connected"
        pass

    @nose.tools.timed(2)
    def test_select_connection(self):
        self._set_select_poller('select')
        self.connection = self._connect(adapters.SelectConnection)
        self.connection.ioloop.start()
        if self.connection.ioloop.poller_type != 'SelectPoller':
            assert False, "Not SelectPoller"
        if not self.connected:
            assert False, "Not Connected"
        pass

    @nose.tools.timed(2)
    def test_tornado_connection(self):
        # Tornado is 2.5+ only
        if float('.'.join(platform.python_version().split('.')[:-1])) < 2.5:
            raise nose.SkipTest
        # Ignore the Tornado ioloop shutdown warning
        warnings.simplefilter('ignore', UserWarning)
        if not tornado_ioloop:
            raise nose.SkipTest
        self.connection = self._connect(adapters.TornadoConnection)
        self.connection.ioloop.start()
        if not self.connected:
            assert False, "Not Connected"
        pass

    @nose.tools.timed(2)
    def test_epoll_connection(self):
        # EPoll is 2.6+ and linux only
        if os.uname()[0].lower() != 'linux':
            raise nose.SkipTest
        if float('.'.join(platform.python_version().split('.')[:-1])) < 2.6:
            raise nose.SkipTest
        self._set_select_poller('epoll')
        self.connection = self._connect(adapters.SelectConnection)
        self.connection.ioloop.start()
        if self.connection.ioloop.poller_type != 'EPollPoller':
            assert False, "Not EPollPoller"
        if not self.connected:
            assert False, "Not Connected"
        pass

    @nose.tools.timed(2)
    def test_poll_connection(self):
        if os.uname()[0].lower() != 'linux':
            raise nose.SkipTest
        self._set_select_poller('poll')
        self.connection = self._connect(adapters.SelectConnection)
        self.connection.ioloop.start()
        if self.connection.ioloop.poller_type != 'PollPoller':
            assert False, "Not PollPoller"
        if not self.connected:
            assert False, "Not Connected"
        pass

    @nose.tools.timed(2)
    def test_kqueue_connection(self):
        if os.uname()[0].lower() not in ['bsd', 'darwin']:
            raise nose.SkipTest
        # KQueue is 2.6+
        if float('.'.join(platform.python_version().split('.')[:-1])) < 2.6:
            raise nose.SkipTest
        self._set_select_poller('kqueue')
        self.connection = self._connect(adapters.SelectConnection)
        self.connection.ioloop.start()
        if self.connection.ioloop.poller_type != 'KQueuePoller':
            assert False, "Not KQueuePoller"
        if not self.connected:
            assert False, "Not Connected"
        pass

    def _connect(self, connection_type):
        if self.connection:
            del self.connection
        self.connected = False
        parameters = pika.ConnectionParameters(HOST, PORT)
        return connection_type(parameters, self._on_connected)

    def _on_connected(self, connection):
        self.connected = self.connection.is_open
        self.connection.add_on_close_callback(self._on_closed)
        self.connection.close()

    def _on_closed(self, frame):
        self.connection.ioloop.stop()

    def _set_select_poller(self, type):
        adapters.select_connection.SELECT_TYPE = type
