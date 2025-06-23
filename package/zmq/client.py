import zmq
from package.logger.logger import setup_logger
import logging
import json
from package.zmq import models
from threading import Lock

# 获取已经配置好的日志记录器
logger = logging.getLogger('root')

class ZmqClient:
    """ ZeroMQ 请求应答模式（客户端） """

    def __init__(self, address="tcp://127.0.0.1:5555",timeout=4):
        self.address = address
        self.context = zmq.Context()
        self.timeout = timeout
        self.lock = Lock()
        self._create_socket()
    def _create_socket(self):
        """创建并注册 socket 到 poller"""
        self.socket = self.context.socket(zmq.REQ)  # 请求（Request）模式
        self.socket.connect(self.address)
        self.poller = zmq.Poller()
        self.poller.register(self.socket, zmq.POLLIN)
    def _reset_socket(self):
        """超时或异常后重置 socket"""
        try:
            self.poller.unregister(self.socket)
        except Exception:
            pass  # 安全处理
        self.socket.close()
        self._create_socket()

    def request(self, message: str):
        """ 发送请求并等待响应 """
        with self.lock:
            try:
                self.socket.send_string(message)
                events = dict(self.poller.poll(self.timeout * 1000))

                if self.socket in events:
                    response = self.socket.recv_string()
                    return True, json.loads(response, object_hook=models.custom_json_decoder)
                else:
                    self._reset_socket()
                    return False, "Timeout: No response from server"
            except zmq.ZMQError as e:
                self._reset_socket()
                return False, f"ZMQError: {str(e)}"

    def close(self):
        """ 关闭客户端 """
        self.socket.close()
        self.context.term()

if __name__ == '__main__':
    c=ZmqClient("tcp://127.0.0.1:30001")
    from package.zmq import models
    order=models.Request()

    order.longShort="LONG"
    order.openClose="OPEN"
    order.pid=1
    order.volume=1
    order.request_type = models.REQ_ORDER
    order.symbol="au2506"
    msg = order.to_json()
    print(c.request(order.to_json()))