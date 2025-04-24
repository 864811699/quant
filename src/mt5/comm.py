from datetime import datetime
from dataclasses import dataclass, field
import MetaTrader5 as mt5
from package.zmq import models

# SIDE_DICT={comm.SIDE_BUY:mt5.ORDER_TYPE_BUY,comm.SIDE_SELL:mt5.ORDER_TYPE_SELL}
def getSide(longshort, openclose):
    if openclose == "OPEN":
        side = mt5.ORDER_TYPE_BUY if longshort == ACTION_LONG else mt5.ORDER_TYPE_SELL
        return side
    if openclose == "CLOSE":
        side = mt5.ORDER_TYPE_BUY if longshort == ACTION_SHORT else mt5.ORDER_TYPE_SELL
        return side


# mt5行情 字段
MARKET_SYMBOL = "SYMBOL"
MARKET_BUY1 = "BUY1"
MARKET_SELL1 = "SELL1"

ACTION = mt5.TRADE_ACTION_DEAL  # 市价委托

TRADE_TYPE_OPEN = "OPEN"
TRADE_TYPE_CLOSE = "CLOSE"

ACTION_LONG = "LONG"
ACTION_SHORT = "SHORT"

ORDER_STATUS_UNKNOWN = 0
ORDER_STATUS_PARTTRADE = 2
ORDER_STATUS_AllTrade = 4
ORDER_STATUS_REJECTED = 6

REQ_POSITION = "P"
REQ_ORDER = "O"
REQ_MARKET = "M"
REQ_SEARCH = "S"
REQ_LIQUIDATE ="L"


# @dataclass
# class mt5MD:
#     instrumentID: str = ""
#     askPrice1: float = 0.0
#     bidPrice1: float = 0.0
#     updateTime: datetime = field(default_factory=datetime.now)


# @dataclass
# class mt5Order:
#     account: str = ""
#     openClose: str = ""  # TRADE_TYPE_OPEN/TRADE_TYPE_CLOSE
#     longShort: str = ""  # ACTION_LONG    ACTION_SHORT
#     magic: int = 0
#     entrustNo: int = 0
#     symbol: str = ""  # 汇率+黄金
#     orderSysID: int = 0  # order=390411490  查询用
#     positionID: str = ""
#     askPrice: float = 0.0
#     askQty: float = 0.0
#
#     status: int = 0
#     statusMsg: str = ""
#     bidVol: float = 0.0
#     bidPrice: float = 0.0
#     rspTime: datetime = field(default_factory=datetime.now)
#     reqTime: datetime = field(default_factory=datetime.now)




@dataclass
class RtnRsp:
    req_success: bool = False
    errmsg: str = ""
    order: models.Order = field(default_factory=models.Order)
    market: models.Market = field(default_factory=models.Market)


# @dataclass
# class Request:
#     reqType: str = ""  # 查询持仓 Ｐ持仓, O 订单 ,S 查询订单 ,M 行情
#     symbol: str = ""
#     longShort: str = ""
#     pid: int = 0
#     magic: int = 0
#     volume: float = 0.0
#     openClose: str = ""


# @dataclass
# class RtnOrder:
#     reqSuccess: bool = False
#     positionVol: float = 0.0
#     needToTradeVol: float = 0
#     tradedVol: float = 0
#     bidPrice: float = 0.0
#     status: int = 0
#     msg: str = ""
#     askPrice1: float = 0.0
#     bidPrice1: float = 0.0
#     orderSysID: int = 0  # order=390411490  查询用


