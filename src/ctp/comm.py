from dataclasses import dataclass, field,asdict
import thosttraderapi as tdapi
from datetime import datetime
import json

from package.zmq import models

OFFSET_OPEN = tdapi.THOST_FTDC_OFEN_Open
OFFSET_CLOSE = tdapi.THOST_FTDC_OFEN_Close
OFFSET_CLOSE_TODAY = tdapi.THOST_FTDC_OFEN_CloseToday
OFFSET_CLOSE_PREV = tdapi.THOST_FTDC_OFEN_CloseYesterday

ZMQ_TO_CTP_OPEN_CLOSE={"OPEN":OFFSET_OPEN, "CLOSE":OFFSET_CLOSE}
CTP_TO_ZMQ_OPEN_CLOSE = {OFFSET_OPEN: "OPEN", OFFSET_CLOSE: "CLOSE",OFFSET_CLOSE_TODAY:"CLOSE",OFFSET_CLOSE_PREV:"CLOSE"}


ACTION_LONG = "LONG"
ACTION_SHORT = "SHORT"
LONG_SHORT_DICT = {tdapi.THOST_FTDC_PD_Long: ACTION_LONG, tdapi.THOST_FTDC_PD_Short: ACTION_SHORT}

POSITION_TODAY='1'
POSITION_YESTERDAY='2'


def getSide(longshort, openclose):
    if openclose == "OPEN":
        side = tdapi.THOST_FTDC_D_Buy if longshort == ACTION_LONG else tdapi.THOST_FTDC_D_Sell
        return side
    if openclose == "CLOSE":
        side = tdapi.THOST_FTDC_D_Buy if longshort == ACTION_SHORT else tdapi.THOST_FTDC_D_Sell
        return side

def getLongShortOpenClose(side, openclose):
    # longshort  openclose
    longshort_data = 0
    openclose_data = CTP_TO_ZMQ_OPEN_CLOSE[openclose]
    if openclose == OFFSET_OPEN :
        longshort_data = ACTION_LONG if side == tdapi.THOST_FTDC_D_Buy else ACTION_SHORT
    if openclose == OFFSET_CLOSE or openclose == OFFSET_CLOSE_PREV or openclose == OFFSET_CLOSE_TODAY:
        longshort_data = ACTION_LONG if side == tdapi.THOST_FTDC_D_Sell else ACTION_SHORT
    return longshort_data, openclose_data


SIDE_DICT = {"BUY": tdapi.THOST_FTDC_D_Buy, "SELL": tdapi.THOST_FTDC_D_Sell}

MARKET_BUY1 = 'Buy1'
MARKET_SELL1 = 'Sell1'
MARKET_INSTRUMENTID = 'InstrumentID'
MARKET_ACTIONDAY = 'ActionDay'
MARKET_UPDATETIME = 'UpdateTime'
MARKET_TRADINGDAY = 'TradingDay'

SEARCH_RESULT = 'SearchResult'

ErrorCodeDict = {0: "成功",
                 -1: "表示网络连接失败",
                 -2: "表示未处理请求超过许可数",
                 -3: "表示每秒发送请求数超过许可数"
                 }

"""
///全部成交
#define THOST_FTDC_OST_AllTraded '0'
///部分成交还在队列中     剩下部分等待成交
#define THOST_FTDC_OST_PartTradedQueueing '1'
///部分成交不在队列中     剩下部分已撤单- 终态
#define THOST_FTDC_OST_PartTradedNotQueueing '2'
///未成交还在队列中      报单已发送,正等待成交
#define THOST_FTDC_OST_NoTradeQueueing '3'
///未成交不在队列中     报单还未发交易所-待发送
#define THOST_FTDC_OST_NoTradeNotQueueing '4'
///撤单              全部撤单
#define THOST_FTDC_OST_Canceled '5'
///未知
#define THOST_FTDC_OST_Unknown 'a'
///尚未触发
#define THOST_FTDC_OST_NotTouched 'b'
///已触发
#define THOST_FTDC_OST_Touched 'c'

"""

#  0     1    2    4    5   6
# 未知, 已报,未完成,已撤,已成,废单
ORDER_STATUS_UNKNOWN = 0
NEW_ORDER = 1
PARTTRADE = 2
AllTrade = 4
CANCELED = 5
REJECTED = 6
STATUS_DICT = {
    'a': 0,
    'c': 1,
    'b': 1,
    '3': 1,
    '2': 5,
    '4': 1,
    '1': 2,
    '0': 4,
    '5': 5,
    '-1': 6,
}


def custom_json_encoder(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()  # 转换为 ISO 8601 格式的字符串
    raise TypeError(f"Type {type(obj)} not serializable")

# @dataclass
# class RtnOrder:
#     account: str = ""
#     symbol:str=""
#     orderRef: str = ""  # open-entrustno   close-entrustno
#     pEntrustNo: int =0
#     entrustNo: int = 0
#     longShort: str ="" # ACTION_LONG / ACTION_SHORT
#     openClose: str= ""  # OFFSET_OPEN /OFFSET_CLOSE
#     askPrice: float = 0.0
#     askQty: int = 0
#     orderSysID: str = ""
#     bidPrice: float = 0.0
#     bidVol: int = 0
#     tmpStatus: int =  0
#     status: int = 0
#     statusMsg: str = ""
#     reqTime:datetime = field(default_factory=datetime.now)
#     rspTime: datetime = field(default_factory=datetime.now)


# @dataclass
# class RtnTrade:
#     account: str = ""
#     tradeTime: str = ""  # date char[9]   time char[9]
#     price: float = 0.0
#     volume: int = 0
#     orderRef: str = ""

@dataclass
class RtnExecOrder:
    reqSuccess: bool = False
    errorMsg: str = ""
    order: models.Order = field(default_factory=models.Order)


DATA_TYPE_ORDER = 1
DATA_TYPE_TRADE = 2


@dataclass
class OrderTrade:
    dataType: int = 0  # DATA_TYPE_ORDER  /DATA_TYPE_TRADE
    account: str = ""
    symbol:str=""
    longShort: str = ""
    openClose: str = ""
    orderRef: str = ""
    entrustNo: str = ""
    orderSysID: str = ""
    askPrice: float = 0.0
    askQty: int = 0
    bidPrice: float = 0.0
    bidVol: int = 0
    status: str = ""
    statusMsg: str = ""
    rspTime: datetime = field(default_factory=datetime.now)


def create_symbol_position_detail():
    return {ACTION_LONG:{POSITION_TODAY:0,POSITION_YESTERDAY:0},ACTION_SHORT:{POSITION_TODAY:0,POSITION_YESTERDAY:0}}
