import zmq
import time
import threading
from package.logger.logger import setup_logger
import logging

# 获取已经配置好的日志记录器
logger = logging.getLogger('root')


class ZmqPublisher:
    """ ZeroMQ PUB 端（发布者）封装 """

    def __init__(self, address="tcp://127.0.0.1:5555",topic="market"):
        self.address = address
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(self.address)
        self.topic =topic

    def publish(self,  message: str):
        """ 发布消息，带有主题（topic） """
        self.socket.send_string(f"{self.topic} {message}")

    def close(self):
        """ 关闭发布端 """
        self.socket.close()
        self.context.term()