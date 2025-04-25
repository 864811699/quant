import zmq
import json
from package.zmq import models

class ZmqSubscriber:
    """ ZeroMQ SUB 端（订阅者）封装 """

    def __init__(self, address="tcp://127.0.0.1:5555", topic_filter="market"):
        """
        :param address: 发布者地址
        :param topic_filter: 订阅的主题（支持前缀匹配），默认为空字符串（订阅所有）
        """
        self.address = address
        self.topic_filter = topic_filter
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect(self.address)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, self.topic_filter)
        self.socket.setsockopt(zmq.CONFLATE, 1)

    def get_data(self):
        """ 直接获取最新的消息，丢弃之前的 """
        try:
            message = self.socket.recv_string()  # 阻塞等待最新数据
            topic, content = message.split(" ", 1)
            return topic, json.loads(content,object_hook=models.custom_json_decoder)
        except zmq.error.ZMQError:
            return None, None

    def close(self):
        """ 关闭订阅端 """
        self.socket.close()
        self.context.term()

if __name__ == '__main__':
    sub=ZmqSubscriber("tcp://127.0.0.1:20001","market")
    while True:
        topic,data=sub.get_data()
        if topic is not  None:
            print(topic)
            print(data)
        print(11)