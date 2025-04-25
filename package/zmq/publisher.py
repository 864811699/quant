import zmq

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

if __name__ == '__main__':
    p=ZmqPublisher("tcp://127.0.0.1:50001",topic="market")
    from package.zmq import models
    def publish_market(askPrice1,bidPrice1):
        rsp = models.Response()
        ctpmd = models.Market()
        ctpmd.instrumentID = "au2506"
        # bidPrice:787.68  - askPrice:787.74
        ctpmd.bidPrice1 = bidPrice1
        ctpmd.askPrice1 = askPrice1
        rsp.market = ctpmd
        json_str = rsp.to_json()
        p.publish(json_str)
