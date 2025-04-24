import zmq


class ZmqServer:
    """ ZeroMQ 请求应答模式（服务端） """

    def __init__(self, address="tcp://*:5555"):
        self.address = address
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)  # 响应（Reply）模式
        self.socket.bind(self.address)

    def listen(self,handler):
        """
        监听请求，并调用 handler 处理数据
        :param handler: 处理请求的回调函数，格式：handler(request_data) -> response_data
        """
        while True:
            request = self.socket.recv_string()
            response = handler(request)  # 处理请求
            self.socket.send_string(response)  # 发送响应

    def close(self):
        """ 关闭服务端 """
        self.socket.close()
        self.context.term()