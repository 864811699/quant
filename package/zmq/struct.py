import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
from typing import List

REQ_POSITION = "P"
REQ_ORDER = "O"
REQ_MARKET = "M"
REQ_SEARCH = "S"
REQ_LIQUIDATE = "L"

TRADE_TYPE_OPEN = "OPEN"
TRADE_TYPE_CLOSE = "CLOSE"

ACTION_LONG = "LONG"
ACTION_SHORT = "SHORT"

ORDER_STATUS_UNKNOWN = 0
NEW_ORDER = 1
PARTTRADE = 2
AllTrade = 4
CANCELED = 5
REJECTED = 6


def custom_json_encoder(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()  # 转换为 ISO 8601 格式的字符串
    raise TypeError(f"Type {type(obj)} not serializable")


@dataclass
class POrder:
    id: int = None
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    entrustNo: int = 0
    longShort: str = ""  # 多空方向
    trigger: str = ""  # 触发的条件
    CTPAUAskPrice: float = 0.0  # ctp黄金当前价格
    CTPAUBidPrice: float = 0.0  # ctp黄金当前价格
    MT5AUAskPrice: float = 0.0  # 伦敦金当前价格
    MT5AUBidPrice: float = 0.0  # 伦敦金当前价格
    USDAskPrice: float = 0.0  # 汇率价格
    USDBidPrice: float = 0.0  # 汇率价格
    spread: float = 0.0  # 当前点差
    realOpenSpread: float = 0.0  # 实际开仓点差
    closeSpread: float = 0.0  # 预期平仓点差
    realCloseSpread: float = 0.0  # 实际平仓点差
    status: int = 0  # 0创建/ 1ctp开仓 / 2伦敦金开仓/ 3汇率开仓 /4 ctp平仓 /5 伦敦金平仓/ 6汇率平仓
    created_at: datetime = field(default_factory=datetime.now)
    closed_at: datetime = field(default_factory=datetime.now)


@dataclass
class Order:
    id: int = None
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    account: str = ""
    symbol: str = ""  # 汇率+黄金
    orderRef: str = 0
    pEntrustNo: int = 0

    entrustNo: int = 0
    longShort: str = ""  # ACTION_LONG    ACTION_SHORT
    openClose: str = ""  # TRADE_TYPE_OPEN/TRADE_TYPE_CLOSE

    askPrice: float = 0.0
    askQty: float = 0.0
    orderSysID: int = 0  # order=390411490  查询用
    bidVol: float = 0.0
    bidPrice: float = 0.0
    status: int = 0
    statusMsg: str = ""
    tmpStatus: int = 0
    positionID: str = ""
    rspTime: datetime = field(default_factory=datetime.now)
    reqTime: datetime = field(default_factory=datetime.now)


@dataclass
class CtpPosition:
    symbol: str = ""
    PositionDate: int = 0  # 今昨仓
    longShortType: str = ""  # 多空
    position: int = 0  # 总
    todayPosition: int = 0  # 今日持仓
    ysdPosition: int = 0  # 上日持仓
    CloseProfit: float = 0.0  # 平仓盈亏
    PositionCost: float = 0.0  # 持仓成本


@dataclass
class PositionSum:
    symbol: str = ""
    longShortType: str = ""  # 多空
    position: int = 0  # 总


@dataclass
class Market:
    askPrice1: float = 0.0
    bidPrice1: float = 0.0
    instrumentID: str = ""


@dataclass
class Request:
    request_type: str = ""  # Ｐ持仓, O 委托 ,S 查询订单 ,M 行情
    symbol: str = ""
    longShort: str = ""
    openClose: str = ""
    pid: int = 0
    volume: float = 0.0

    def to_json(self):
        return json.dumps(asdict(self), default=custom_json_encoder, indent=4, ensure_ascii=False)


@dataclass
class Response:
    req_success: bool = False
    errmsg: str = ""
    order: Order = field(default_factory=Order)
    orders: List[Order] = field(default_factory=list)
    positions: List[PositionSum] = field(default_factory=list)
    market: Market = field(default_factory=Market)

    def to_json(self):
        return json.dumps(asdict(self), default=custom_json_encoder, indent=4, ensure_ascii=False)
