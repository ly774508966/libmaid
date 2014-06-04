import struct

from google.protobuf.service import RpcChannel
from google.protobuf.message import DecodeError
from gevent.hub import get_hub
from gevent.pool import Pool
from gevent.event import AsyncResult
from gevent.queue import Queue
from gevent.greenlet import Greenlet
from gevent import socket
from gevent import core
from gevent import sleep

from controller_pb2 import ControllerMeta

from controller import Controller


class Channel(RpcChannel):

    def __init__(self, loop=None):
        self._header = "!II"
        self._loop = loop or get_hub().loop
        self._services = {}
        self._pending_request = {}
        self._transmit_id = 0
        self._listen_watchers = {}
        self._send_queue = {}
        self._default_sock = None

    def set_default_sock(self, sock):
        self._default_sock = sock

    def append_service(self, service):
        self._services[service.DESCRIPTOR.full_name] = service

    def CallMethod(self, method, controller, request, response_class, done):
        if not isinstance(controller, Controller):
            raise Exception("controller should has type Controller")

        controller.meta_data.stub = True
        controller.meta_data.service_name = method.containing_service.full_name
        controller.meta_data.method_name = method.name

        if controller.sock is None:
            controller.sock = self._default_sock

        while True:
            if self._transmit_id >= (1 << 63) -1:
                self._transmit_id = 0
            else:
                self._transmit_id += 1
            controller.response_class = response_class
            controller.async_result = AsyncResult()
            if self._pending_request.get(self._transmit_id, None) is None:
                controller.meta_data.transmit_id = self._transmit_id
                self._pending_request[self._transmit_id] = controller
                break

        if not self._send_queue.has_key(controller.sock):
            controller.SetFailed("did not connect")
            controller.async_result.set(None)
        else:
            self._send_queue[controller.sock].put(controller)
        return controller.async_result

    def connect(self, host, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        while True:
            result = sock.connect_ex((host, port))
            if result == socket.EISCONN: # connected
                break
            elif result == socket.EINPROGRESS: # connection in progress
                continue
            else:
                raise Exception('%d: connection failed' % result)
        self.new_connection(sock)
        return sock

    def listen(self, host, port, backlog=1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(backlog)
        sock.setblocking(0)
        accept_watcher = self._loop.io(sock.fileno(), core.READ)
        accept_watcher.start(self._do_accept, sock)

    def new_connection(self, sock):
        send_queue = Queue()
        greenlet_recv = Greenlet.spawn(self._handle, sock)
        greenlet_send = Greenlet.spawn(self._write, sock)

        # closure
        def close(gr):
            greenlet_recv.kill()
            greenlet_send.kill()
            if not self._send_queue.has_key(sock):
                return
            sock.close()
            del self._send_queue[sock]

        greenlet_recv.link(close)
        greenlet_send.link(close)
        self._send_queue[sock] = send_queue

    def _do_accept(self, sock):
        try:
            client_socket, address = sock.accept()
        except socket.error as err:
            if err.args[0] == socket.EWOULDBLOCK:
                return
            raise
        self.new_connection(client_socket)

    def _recv_n(self, sock, length):
        buf = ""
        try:
            while True:
                if len(buf) >= length:
                    return buf
                tmp = sock.recv(length - len(buf))
                if tmp is None or len(tmp) == 0:
                    return None
                buf += tmp
        except socket.error as e:
            return None

    def _handle(self, sock):
        while True:
            header_length = struct.calcsize(self._header)
            header_buffer = self._recv_n(sock, header_length)
            if header_buffer is None:
                break

            controller_length, message_length = struct.unpack(self._header, header_buffer)
            controller_buffer = self._recv_n(sock, controller_length)
            if controller_buffer is None:
                break

            controller = Controller()
            controller.sock = sock
            try:
                controller.meta_data.ParseFromString(controller_buffer)
            except DecodeError:
                break
            message_buffer = ""
            if message_length > 0:
                message_buffer = sock.recv(message_length)
            if controller.meta_data.stub: # request
                self._handle_request(controller, message_buffer)
            else:
                self._handle_response(controller, message_buffer)

    def _handle_request(self, controller, message_buffer):
        service = self._services.get(controller.meta_data.service_name, None)
        controller.meta_data.stub = False
        send_queue = self._send_queue[controller.sock]

        if service is None:
            controller.SetFailed("service not exist")
            send_queue.put(controller)
            return

        method = service.DESCRIPTOR.FindMethodByName(
                controller.meta_data.method_name)
        if method is None:
            controller.SetFailed("method not exist")
            send_queue.put(controller)
            return

        request_class = service.GetRequestClass(method)
        request = request_class()
        request.ParseFromString(message_buffer)

        response = service.CallMethod(method, controller, request, None)
        controller.response = response
        send_queue.put(controller)

    def _handle_response(self, controller_, message_buffer):
        controller = self._pending_request.get(controller_.meta_data.transmit_id, None)
        if controller is None:
            return

        controller.meta_data = controller_.meta_data

        if controller.Failed:
            controller.async_result.set(None)
            return

        response = controller.response_class()
        result = response.ParseFromString(message_buffer)
        if not result:
            controller.SetFailed("parse failed")
            controller.async_result.set(None)
            return
        controller.async_result.set(response)

    def _write(self, sock):
        header_buffer = ""
        controller_buffer = ""
        for controller in self._send_queue[sock]:
            if controller is None:
                break
            controller_buffer = controller.meta_data.SerializeToString()
            message_buffer = ""
            if not controller.Failed():
                if controller.meta_data.stub:
                    message = getattr(controller, "request", None)
                else:
                    message = getattr(controller, "response", None)

                if message is not None:
                    message_buffer = message.SerializeToString()

            header_buffer = struct.pack(self._header, len(controller_buffer), len(message_buffer))

            sock.sendall(header_buffer)
            sock.sendall(controller_buffer)
            sock.sendall(message_buffer)
