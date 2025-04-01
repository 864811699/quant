import db
import uuid
from dataclasses import dataclass, field, asdict
from datetime import  datetime
cfg={
    "host":"rm-bp1l09swp665a10naro.mysql.rds.aliyuncs.com",
    "user":"test_quant",
    "pwd":"ASDzxc!%40#",
    "charset":"utf8",
	"db":"quant",
	"table":"child"
}

d=db.dbServer(cfg)
# d.create_child_table()
@dataclass
class Order:
    id: int = None
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    account: str = ""
    symbol: str = ""  # 汇率+黄金
    pEntrustNo: int = 0
    orderRef: str = 0
    entrustNo: int = 0
    openClose: str = ""  # TRADE_TYPE_OPEN/TRADE_TYPE_CLOSE
    longShort: str = ""  # ACTION_LONG    ACTION_SHORT
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


order1=Order()
order1.account: str = "111"
order1.symbol: str = "222"  # 汇率+黄金
order1.pEntrustNo: int = 1

# d.save_child_order(order1,"child")
# orders=d.load_orders("child",1)
# print(orders)
order1.uuid="30802caa-ac81-4013-9e51-0c04a5ce2395"
order1.orderSysID="asd"
order1.bidPrice=1.3
order1.bidVol=1.1
order1.status=2
order1.statusMsg="123"
order1.rspTime=datetime.now()
d.update_child_order("child",order1)

