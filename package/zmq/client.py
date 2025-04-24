import zmq
from package.logger.logger import setup_logger
import logging
import json
from package.zmq import models

# 获取已经配置好的日志记录器
logger = logging.getLogger('root')

class ZmqClient:
    """ ZeroMQ 请求应答模式（客户端） """

    def __init__(self, address="tcp://127.0.0.1:5555"):
        self.address = address
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)  # 请求（Request）模式
        self.socket.connect(self.address)

    def request(self, message: str, timeout=5):
        """ 发送请求并等待响应 """
        self.socket.send_string(message)
        poller = zmq.Poller()
        poller.register(self.socket, zmq.POLLIN)

        if poller.poll(timeout * 2000):  # 设置超时时间
            response = self.socket.recv_string()
            return True,json.loads(response,object_hook=models.custom_json_decoder)
        else:
            return False,"Timeout: No response from server"

    def close(self):
        """ 关闭客户端 """
        self.socket.close()
        self.context.term()

