import threading
import zmq

class ZMQ_Proxy:
    def __init__(self,sub_addr,pub_addr):
        self.sub_addr = sub_addr
        self.pub_addr = pub_addr

    def start_proxy(self):
        context = zmq.Context()

        # XSUB: 接收来自多个服务端的发布
        frontend = context.socket(zmq.XSUB)
        frontend.bind(self.sub_addr)  # 给各服务端连接

        # XPUB: 提供给你的订阅客户端使用
        backend = context.socket(zmq.XPUB)
        backend.bind(self.pub_addr)  # 给订阅模块连接

        zmq.proxy(frontend, backend)  # 阻塞运行

    def run_proxy(self):
        threading.Thread(target=self.start_proxy, daemon=True).start()