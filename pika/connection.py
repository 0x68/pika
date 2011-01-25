# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0
#
# The contents of this file are subject to the Mozilla Public License
# Version 1.1 (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS"
# basis, WITHOUT WARRANTY OF ANY KIND, either express or implied. See
# the License for the specific language governing rights and
# limitations under the License.
#
# The Original Code is Pika.
#
# The Initial Developers of the Original Code are LShift Ltd, Cohesive
# Financial Technologies LLC, and Rabbit Technologies Ltd.  Portions
# created before 22-Nov-2008 00:00:00 GMT by LShift Ltd, Cohesive
# Financial Technologies LLC, or Rabbit Technologies Ltd are Copyright
# (C) 2007-2008 LShift Ltd, Cohesive Financial Technologies LLC, and
# Rabbit Technologies Ltd.
#
# Portions created by LShift Ltd are Copyright (C) 2007-2009 LShift
# Ltd. Portions created by Cohesive Financial Technologies LLC are
# Copyright (C) 2007-2009 Cohesive Financial Technologies
# LLC. Portions created by Rabbit Technologies Ltd are Copyright (C)
# 2007-2009 Rabbit Technologies Ltd.
#
# Portions created by Tony Garnock-Jones are Copyright (C) 2009-2010
# LShift Ltd and Tony Garnock-Jones.
#
# All Rights Reserved.
#
# Contributor(s): ______________________________________.
#
# Alternatively, the contents of this file may be used under the terms
# of the GNU General Public License Version 2 or later (the "GPL"), in
# which case the provisions of the GPL are applicable instead of those
# above. If you wish to allow use of your version of this file only
# under the terms of the GPL, and not to allow others to use your
# version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the
# notice and other provisions required by the GPL. If you do not
# delete the provisions above, a recipient may use your version of
# this file under the terms of any one of the MPL or the GPL.
#
# ***** END LICENSE BLOCK *****

import logging

import pika.callback as callback
import pika.channel as channel
import pika.codec as codec
import pika.simplebuffer as simplebuffer
import pika.spec as spec

from pika.specbase import _codec_repr
from pika.exceptions import *

from pika.credentials import PlainCredentials
from pika.heartbeat import HeartbeatChecker
from pika.reconnection_strategies import NullReconnectionStrategy

CHANNEL_MAX = 32767
FRAME_MAX = 131072
PRODUCT = "Pika Python AMQP Client Library"


# Module wide default credentials for default RabbitMQ configurations
default_credentials = PlainCredentials('guest', 'guest')


class ConnectionParameters(object):

    def __init__(self,
                 host,
                 port=None,
                 virtual_host="/",
                 credentials=None,
                 channel_max=0,
                 frame_max=131072,
                 heartbeat=0):

        self.host = host
        self.port = port
        self.virtual_host = virtual_host
        self.credentials = credentials
        self.channel_max = channel_max
        self.frame_max = frame_max
        self.heartbeat = heartbeat

    def __repr__(self):

        return _codec_repr(self, lambda: ConnectionParameters(None))


class Connection(object):

    """
    Pika Connection Class

    This class is extended by the adapter Connection classes such as
    blocking_adapter.BlockingConnection & asyncore_adapter.AsyncoreConnection.
    To build an adapter Connection class implement the following functions:

        Required:

        def connect(self, host, port)
        def disconnect(self)
        def flush_outbound(self)
        def add_timeout(self, delay_sec, callback)

        Optional:

        def erase_credentials(self)

    """
    def __init__(self, parameters, on_open_callback,
                 reconnection_strategy=None):
        """
        Connection initialization expects a ConnectionParameters object and
        a callback function to notify when we have successfully connected
        to the AMQP Broker.

        A reconnection_strategy of None will use the NullReconnectionStrategy
        """
        # If we did not pass in a reconnection_strategy, setup the default
        if not reconnection_strategy:
            reconnection_strategy = NullReconnectionStrategy()

        # Define our callback dictionary
        self.callbacks = callback.CallbackManager.instance()

        # On connection callback
        if on_open_callback:
            self.add_on_open_callback(on_open_callback)

        # Set our configuration options
        self.parameters = parameters
        self.reconnection_strategy = reconnection_strategy

        # Add our callback for if we close by being disconnected
        self.add_on_close_callback(reconnection_strategy.on_connection_closed)

        # Set all of our default connection state values
        self._init_connection_state()

        # Connect to the AMQP Broker
        self._connect()

    def add_on_close_callback(self, callback):
        """
        Add a callback notification when the connection has closed
        """
        logging.debug('%s._add_on_close_callback: %s' % \
                      (self.__class__.__name__, callback))

        self.callbacks.add(0, '_on_connection_closed', callback, False)

    def add_on_open_callback(self, callback):
        """
        Add a callback notification when the connection has closed
        """
        logging.debug('%s._add_on_open_callback: %s' % \
                      (self.__class__.__name__, callback))

        self.callbacks.add(0, '_on_connection_open', callback, False)

    def add_timeout(self, delay_sec, callback):
        """
        Adapters should override to call the callback after the
        specified number of seconds have elapsed, using a timer, or a
        thread, or similar.
        """
        raise NotImplementedError('%s needs to implement this function ' \
                                  % self.__class__.__name__)

    def is_open(self):
        """
        Returns a boolean reporting the current connection state
        """
        return self.open and (not self.closing and not self.closed)

    def _init_connection_state(self):
        """
        Initialize or reset all of our internal state variables for a given
        connection. If we disconnect and reconnect, all of our state needs to
        be wiped
        """
        logging.debug('%s._init_connection_state' % self.__class__.__name__)

        # Inbound and outbound buffers
        self.buffer = simplebuffer.SimpleBuffer()
        self.outbound_buffer = simplebuffer.SimpleBuffer()

        # Connection state, server properties and channels all change on
        # each connection
        self.state = codec.ConnectionState()
        self.server_properties = None
        self._channels = dict()

        # Data used for Heartbeat checking
        self.bytes_sent = 0
        self.bytes_received = 0
        self.heartbeat = None

        # AMQP Lifecycle States
        self.closed = True
        self.closing = False
        self.open = False

        # Our starting point once connected, first frame received
        self.callbacks.add(0, spec.Connection.Start, self._on_connection_start)

    def _local_protocol_header(self):
        """
        Returns the Frame Protocol Header for our AMQP Client for communicating
        with our AMQP Broker
        """
        return codec.FrameProtocolHeader(1, 1,
                                         spec.PROTOCOL_VERSION[0],
                                         spec.PROTOCOL_VERSION[1])

    def connect(self, host, port):
        """
        Subclasses should override to set up the outbound
        socket.
        """
        raise NotImplementedError('%s needs to implement this function ' \
                                  % self.__class__.__name__)

    def _connect(self):
        """
        Internal connection method that will kick off the socket based
        connections in our Adapter and kick off the initial communication
        frame.

        Connect in our Adapter's Connection is a blocking operation
        """
        logging.debug('%s._connect' % self.__class__.__name__)

        # Let our RS know what we're up to
        self.reconnection_strategy.on_connect_attempt(self)

        # Try and connect and send the first frame
        self.connect(self.parameters.host,
                     self.parameters.port or  spec.PORT)

    def disconnect(self):
        """
        Subclasses should override this to cause the underlying
        transport (socket) to close.
        """
        raise NotImplementedError('%s needs to implement this function ' \
                                  % self.__class__.__name__)

    def reconnect(self):
        """
        Called by the Reconnection Strategy classes or Adapters to disconnect
        and reconnect to the broker
        """
        logging.debug('%s.reconnect' % self.__class__.__name__)

        # We're already closing but it may not be from reconnect, so first
        # Add a callback that won't be duplicated
        if self.closing:
            self.add_on_close_callback(self._reconnect)
            return

        # If we're open, we want to close normally if we can, then actually
        # reconnect via callback that can't be added more than once
        if self.open:
            self.add_on_close_callback(self._reconnect)
            self._ensure_closed()
            return

        # We're not closing and we're not open, so reconnect
        self._reconnect()

    def _reconnect(self):
        """
        Actually do the reconnecting
        """
        self._init_connection_state()
        self._connect()

    def _on_connected(self):
        """
        This is called by our connection Adapter to let us know that we've
        connected and we can notify our connection strategy
        """
        logging.debug('%s.on_connected' % self.__class__.__name__)

        # Start the communication with the RabbitMQ Broker
        self._send_frame(self._local_protocol_header())

        # Let our reconnection_strategy know we're connected
        self.reconnection_strategy.on_transport_connected(self)

    def _on_connection_open(self, frame):
        """
        This is called once we have tuned the connection with the server and
        called the Connection.Open on the server and it has replied with
        Connection.Ok.
        """
        logging.debug('%s._on_connection_open' % self.__class__.__name__)

        self.known_hosts = frame.method.known_hosts

        # Add a callback handler for the Broker telling us to disconnect
        self.callbacks.add(0, spec.Connection.Close, self._on_remote_close)

        # We're now connected at the AMQP level
        self.open = True

        # Call our initial callback that we're open
        self.callbacks.process(0, '_on_connection_open', self, self)

    def _on_connection_start(self, frame):
        """
        This is called as a callback once we have received a Connection.Start
        from the server.
        """
        logging.debug('%s._on_connection_start' % self.__class__.__name__)

        # We're now connected to the broker
        self.closed = False

        # We didn't expect a FrameProtocolHeader, did we get one?
        if isinstance(frame, codec.FrameProtocolHeader):
            raise ProtocolVersionMismatch(self._local_protocol_header(), frame)

        # Make sure that the major and minor version matches our spec version
        if (frame.method.version_major,
            frame.method.version_minor) != spec.PROTOCOL_VERSION:
            raise ProtocolVersionMismatch(self._local_protocol_header(),
                                          frame)

        # Get our server properties for use elsewhere
        self.server_properties = frame.method.server_properties

        # Use the default credentials if the user didn't pass any in
        credentials = self.parameters.credentials or default_credentials

        # Build our StartOk authentication response
        response = credentials.response_for(frame.method)

        # Server asked for credentials for a method we don't support so raise
        # an exception to let the implementing app know
        if not response:
            raise LoginError("No %s support for the credentials" %\
                             self.parameters.credentials.TYPE)

        # Erase our credentials if we don't want to retain them in the state
        # of the connection. By default this is a noop function but adapters
        # may override this
        self.erase_credentials()

        # Add our callback for our Connection Tune event
        self.callbacks.add(0, spec.Connection.Tune, self._on_connection_tune)

        # Send our Connection.StartOk
        method = spec.Connection.StartOk(client_properties={"product":
                                                            PRODUCT},
                                        mechanism=response[0],
                                        response=response[1])
        self.send_method(0, method)

    def erase_credentials(self):
        """
        Override if in some context you need the object to forget
        its login credentials after successfully opening a connection.
        """
        pass

    def _combine(self, a, b):
        """
        Pass in two values, if a is 0, return b otherwise if b is 0, return a.
        If neither case matches return the smallest value.
        """
        if not a:
            return b
        elif not b:
            return a
        return min(a, b)

    def _on_connection_tune(self, frame):
        """
        Once the Broker sends back a Connection.Tune, we will set our tuning
        variables that have been returned to us and kick off the Heartbeat
        monitor if required, send our TuneOk and then the Connection. Open rpc
        call on channel 0
        """
        logging.debug('%s._on_connection_tune' % self.__class__.__name__)
        cmax = self._combine(self.parameters.channel_max,
                             frame.method.channel_max)
        fmax = self._combine(self.parameters.frame_max,
                             frame.method.frame_max)
        hint = self._combine(self.parameters.heartbeat,
                             frame.method.heartbeat)

        # If we have a heartbeat interval, create a heartbeat checker
        if hint:
            self.heartbeat = HeartbeatChecker(self, hint)

        # Update our connection state with our tuned values
        self.state.tune(cmax, fmax)

        # Send the TuneOk response with what we've agreed upon
        self.send_method(0, spec.Connection.TuneOk(channel_max=cmax,
                                                   frame_max=fmax,
                                                   heartbeat=hint))

        # Send the Connection.Open RPC call for the vhost
        cmd = spec.Connection.Open(virtual_host=self.parameters.virtual_host,
                                   insist=True)
        self.rpc(self._on_connection_open, 0, cmd, [spec.Connection.OpenOk])

    def close(self, code=200, text='Normal shutdown'):
        """
        Main close function, will attempt to close the channels and if there
        are no channels left, will go straight to on_close_ready
        """
        logging.debug("%s.close Closing Connection: (%s) %s" % \
                      (self.__class__.__name__, code, text))

        if self.closing or self.closed:
            logging.warning("%s.Close invoked while closing or closed" %\
                            self.__class__.__name__)
            return

        # Carry our code and text around with us
        self.closing = code, text

        # Remove the reconnection strategy callback for when we close
        self.callbacks.remove(0, '_on_connection_close',
                              self.reconnection_strategy.on_connection_closed)

        # If we're not already closed
        for channel_number in self._channels.keys():
            self._channels[channel_number].close(code, text)

        # If we already dont have any channels, close out
        if not len(self._channels):
            self._on_close_ready()

    def _on_close_ready(self):
        """
        On a clean shutdown we'll call this once all of our channels are closed
        Let the Broker know we want to close
        """
        logging.info('%s._on_close_ready' % self.__class__.__name__)

        if self.closed:
            logging.warn("%s.on_close_ready invoked while closed" %\
                         self.__class__.__name__)
            return

        self.rpc(self._on_connection_closed, 0,
                 spec.Connection.Close(self.closing[0],
                                       self.closing[1], 0, 0),
                 [spec.Connection.CloseOk])

    def _on_connection_closed(self, frame, from_adapter=False):
        """
        Let both our RS and Event object know we closed
        """
        logging.debug('%s._on_close' % self.__class__.__name__)

        # Set that we're actually closed
        self.closed = True
        self.closing = False
        self.open = False

        # Call any callbacks registered for this
        self.callbacks.process(0, '_on_connection_closed', self, self)

        # Disconnect our transport if it didn't call on_disconnected
        if not from_adapter:
            self.disconnect()

    def _on_remote_close(self, frame):
        """
        We've received a remote close from the server
        """
        logging.debug('%s._on_remote_close: %r' % (self.__class__.__name__,
                                                   frame))
        self.close(frame.method.reply_code, frame.method.reply_text)

    def _ensure_closed(self):
        """
        If we're not already closed, make sure we're closed
        """
        logging.debug('%s._ensure_closed' % self.__class__.__name__)

        # We carry the connection state and so we want to close if we know
        if self.is_open() and not self.closing:
            self.close()

    # Channel related functionality

    def channel(self, on_open_callback, channel_number=None):
        """
        Create a new channel with the next available or specified channel #
        """
        logging.debug('%s.channel' % self.__class__.__name__)

        # If the user didn't specify a channel_number get the next avail
        if not channel_number:
            channel_number = self._next_channel_number()

        # Add the channel spec.Channel.CloseOk callback for _on_channel_close
        self.callbacks.add(channel_number, spec.Channel.CloseOk,
                           self._on_channel_close)

        # Add it to our Channel dictionary
        self._channels[channel_number] = channel.Channel(self, channel_number,
                                                         on_open_callback)

    def _next_channel_number(self):
        """
        Return the next available channel number or raise on exception
        """
        # Our limit is the the Codec's Channel Max or MAX_CHANNELS if it's None
        limit = self.state.channel_max or CHANNEL_MAX

        # We've used all of our channels
        if len(self._channels) == limit:
            raise NoFreeChannels()

        # Get a list of all of our keys, all should be numeric channel ids
        channel_numbers = self._channels.keys()

        # We don't start with any open channels
        if not len(channel_numbers):
            return 1

        # Our next channel is the max key value + 1
        return max(channel_numbers) + 1

    def _on_channel_close(self, frame):
        """
        RPC Response from when a channel closes itself, remove from our stack
        """
        logging.debug('%s._on_channel_close: %s' % (self.__class__.__name__,
                                                    frame.channel_number))

        if frame.channel_number in self._channels:
            del(self._channels[frame.channel_number])

        if self.closing and not len(self._channels):
            self._on_close_ready()

    # Data packet and frame handling functions

    def on_data_available(self, data):
        """
        This is called by our Adapter, passing in the data from the socket
        As long as we have buffer try and map out frame data
        """

        # Append what we received to our class level read buffer
        self.buffer.write(data)

        # Get the full contents of our buffer for use in the while loop
        data = self.buffer.read()

        # Flush the class read buffer
        self.buffer.flush()

        while data:

            (consumed_count, frame) = self.state.handle_input(data)

            # If we don't have a full frame, set our global buffer and exit
            if not frame:
                self.buffer.write(data)
                break

            # Remove the frame we just consumed from our data
            data = data[consumed_count:]

            # Increment our bytes received buffer for heartbeat checking
            self.bytes_received += consumed_count


            # If we have a Method Frame and have callbacks for it
            if isinstance(frame, codec.FrameMethod) and \
                self.callbacks.pending(frame.channel_number, frame.method):

                # Process the callbacks for it
                self.callbacks.process(frame.channel_number,  # Prefix
                                       frame.method,          # Key
                                       self,                  # Caller
                                       frame)                 # Args

            # We don't check for heartbeat frames because we can not count
            # atomic frames reliably due to different message behaviors
            # such as large content frames being transferred slowly
            elif isinstance(frame, codec.FrameHeartbeat):
                continue

            elif frame.channel_number > 0:
                # Call our Channel Handler with the frame
                self._channels[frame.channel_number].transport.deliver(frame)

    def rpc(self, callback, channel_number, method, acceptable_replies):
        """
        Make an RPC call for the given callback, channel number and method.
        acceptable_replies lists out what responses we'll process from the
        server with the specified callback.
        """

        # If we were passed a callback, add it to our stack
        if callback:
            for reply in acceptable_replies:
                self.callbacks.add(channel_number, reply, callback)

        # Send the rpc call to RabbitMQ
        self.send_method(channel_number, method)

    def _send_frame(self, frame):
        """
        This appends the fully generated frame to send to the broker to the
        output buffer which will be then sent via the connection adapter
        """
        logging.debug('%s._send_frame: %r' % (self.__class__.__name__,
                                              frame))

        marshalled_frame = frame.marshal()
        self.bytes_sent += len(marshalled_frame)
        self.outbound_buffer.write(marshalled_frame)
        self.flush_outbound()

    def flush_outbound(self):
        """
        Adapters should override to flush the contents of
        outbound_buffer out along the socket.
        """
        raise NotImplementedError('%s needs to implement this function ' \
                                  % self.__class__.__name__)

    def send_method(self, channel_number, method, content=None):
        """
        Constructs a RPC method frame and then sends it to the broker
        """
        logging.debug('%s.send_method(%i, %s, %s)' % (self.__class__.__name__,
                                                      channel_number,
                                                      method,
                                                      content))
        self._send_frame(codec.FrameMethod(channel_number, method))

        if isinstance(content, tuple):
            props = content[0]
            body = content[1]
        else:
            props = None
            body = content

        if props:
            length = 0
            if body:
                length = len(body)
            self._send_frame(codec.FrameHeader(channel_number, length, props))

        if body:
            max_piece = (self.state.frame_max - \
                         codec.ConnectionState.HEADER_SIZE - \
                         codec.ConnectionState.FOOTER_SIZE)
            body_buf = simplebuffer.SimpleBuffer(body)

            while body_buf:
                piece_len = min(len(body_buf), max_piece)
                piece = body_buf.read_and_consume(piece_len)
                self._send_frame(codec.FrameBody(channel_number, piece))

    def suggested_buffer_size(self):
        """
        Return the suggested buffer size from the codec/tune or the default
        if that is None
        """
        if not self.state.frame_max:
            return FRAME_MAX

        return self.state.frame_max
