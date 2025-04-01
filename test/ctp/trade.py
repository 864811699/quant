from package.zmq import client
from package.zmq import struct

c=client.ZmqClient("tcp://127.0.0.1:30003")
def subMarket():
    req=struct.Request()
    req.request_type="M"
    req.symbol="USDCNH"
    msg = req.to_json()
    success, response = c.request(message=msg)
    print(success)
    print(response)


def sendOrder():
    order=struct.Request()
    order.request_type=struct.REQ_ORDER
    order.symbol="USDCNH"
    order.longShort=struct.ACTION_LONG
    order.openClose=struct.TRADE_TYPE_OPEN
    order.pid=1
    order.volume=0.01
    msg=order.to_json()

    success,response=c.request(message=msg)
    print(success)
    print(response)

def qryOrder():
    order = struct.Request()
    order.request_type=struct.REQ_SEARCH
    order.pid = 1
    msg = order.to_json()
    success,response=c.request(message=msg)
    print(success)
    print(response)

# subMarket()
# sendOrder()
qryOrder()