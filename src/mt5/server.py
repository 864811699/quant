import json

import logging

from package.logger.logger import setup_logger

log = logging.getLogger('root')

import threading
import MetaTrader5 as mt5Api

from src.mt5 import mt5
from package.config import config
from package.db import db
from package.zmq import server
from package.zmq import models
from src.mt5 import utils
from src.mt5 import comm


class Server():
    def __init__(self, baseConfigFile, strategyConfigFile):
        self.baseConfigFile = baseConfigFile
        self.strategyConfigFile = strategyConfigFile
        self.baseConfig = None
        self.dbConfig = None
        self.zmqConfig = None
        self.cfg = None
        self.db = None
        self.mt5Api = None
        self.zmqServer = None
        self.loadCfg()
        self.entrustNo = 0

        self.lock = threading.Lock()
        self.OrderDict = {}  # entrustNo-->order

        log.info("server load config success!!!")

    def loadCfg(self):
        cfg = config.Config(self.baseConfigFile, self.strategyConfigFile)
        cfg.load_config()
        self.cfg = cfg
        log.info("ctp config: {}".format(cfg))
        self.baseConfig = cfg.get_base_config()
        self.dbConfig = cfg.get_db_config()
        self.zmqConfig = cfg.get_zmq_config()

    def init_api(self):
        self.db = db.dbServer(self.dbConfig)
        self.db.create_child_table()
        self.mt5Api = mt5.Mt5Api(self.baseConfig["path"], self.baseConfig["user"], self.baseConfig["pwd"],
                                 self.baseConfig["host"], self.baseConfig["fillType"])
        self.mt5Api.run()

        self.zmqServer = server.ZmqServer(self.zmqConfig["reqrspPort"])
        log.info("server init api success!!!, port:{}".format(self.zmqConfig["reqrspPort"]))

    def get_entrustNo(self):
        self.entrustNo += 1
        return self.entrustNo

    def store_order(self, order):
        with self.lock:
            self.OrderDict[order.entrustNo] = order

    def get_orders(self):
        orders = []
        with self.lock:
            for entrustNo in self.OrderDict.keys():
                orders.append(self.OrderDict[entrustNo])
        return orders

    def get_order_from_pid(self, pid):
        orders = []
        with self.lock:
            for order in self.OrderDict.values():
                if order.pEntrustNo == pid and order.status==4:
                    orders.append(self.OrderDict[order.entrustNo])
        return orders

    def save_order(self, order):
        self.store_order(order)
        self.db.save_child_order(self.dbConfig["table"], order)
        return True, ""

    def update_order(self, order):
        self.store_order(order)
        self.db.update_child_order(self.dbConfig["table"], order)
        return True, ""

    def create_order(self, symbol, pEntrustNo, longShort, openClose, Volume):
        order = models.Order()
        order.account = self.baseConfig["user"]
        order.symbol = symbol
        order.pEntrustNo = pEntrustNo
        order.entrustNo = self.get_entrustNo()
        order.longShort = longShort
        order.openClose = openClose
        order.askPrice = 0
        order.askQty = Volume
        order.parentAskQty = Volume
        order.status = models.ORDER_STATUS_UNKNOWN
        success, errmsg = self.save_order(order)
        if success:
            return True, order
        else:
            log.warninging("ctp save order fail,pEntrustNo:{}  error:{}".format(pEntrustNo, errmsg))
            return False, None

    def close_all_orders(self, symbol, pid):
        positions = self.mt5Api.getPostions(symbol)
        if positions is not None and len(positions) > 0:
            for position in positions:
                longShort = comm.ACTION_LONG if position.type == mt5Api.ORDER_TYPE_BUY else comm.ACTION_SHORT
                success, order = self.create_order(symbol, pid, longShort, models.TRADE_TYPE_CLOSE, position.volume)
                if success:
                    ret = self.mt5Api.ExecOrder(order)
                    if ret.req_success:
                        order = ret.order
                    else:
                        order.status = models.REJECTED
                    self.update_order(order)
            return False
        else:
            # 无持仓可清时,则返回清理成功,否则均返回False
            return True

    def loadOrdersFromDB(self):
        orders = self.db.load_child_all_orders(self.dbConfig["table"], self.baseConfig["subSymbol"])
        for order in orders:
            if self.entrustNo < order.entrustNo:
                self.entrustNo = order.entrustNo
            self.OrderDict[order.entrustNo] = order
            if order.status < 4:
                if order.orderSysID == "":
                    order.status = 6
                    order.statusMsg = "mt5未知状态"
                success, msg, rsporders = self.mt5Api.getHistoryOrders(order.orderSysID)
                if success is False or len(rsporders) == 0:
                    log.warning("mt5 qry order [{}]fail,msg:{}".format(order.entrustNo, msg))
                    order.status = 6
                    order.statusMsg = "mt5未查询到"
                else:
                    order.positionID = str(rsporders[0].position_id)
                    order.status = comm.ORDER_STATUS_AllTrade
                    order.bidVol = rsporders[0].volume_initial
                    order.bidPrice = rsporders[0].price_current
                    order.rspTime = utils.getLocalTimeFromMilliseconds(rsporders[0].time_done_msc)
                    log.info("qry from mt5 ,entrustNo:{},pEntrustNo:{},order:{}".format(order.entrustNo,order.pEntrustNo,rsporders))
                    self.OrderDict[order.entrustNo] = order
                self.update_order(order)

    def exec_parent_order(self, request):
        rsp = models.Response()
        #     req_success: bool = False
        #     errmsg: str = ""
        #     order: Order = field(default_factory=Order)
        needToTradeVol = request["volume"]

        while True:
            success, order = self.create_order(request["symbol"], request["pid"], request["longShort"], request["openClose"], needToTradeVol)
            ret =self.mt5Api.ExecChildOrder(order)
            if ret.req_success:
                if ret.order.status== comm.ORDER_STATUS_PARTTRADE:
                    needToTradeVol=needToTradeVol-ret.order.bidVol
                if utils.float_equal(needToTradeVol,0):
                    rsp.req_success=True

            else:
                # 请求失败,直接返回 拒单以及其他异常未成交
                pass


    def run(self):
        self.loadOrdersFromDB()
        log.info("MT5 start success ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        while True:
            request_json = self.zmqServer.socket.recv_string()
            request = json.loads(request_json)
            log.info("recv request:{}".format(request))
            # 处理请求
            if request["request_type"] == models.REQ_ORDER:
                rsp = models.Response()
                # TODO 做母单子单处理,函数拿到母单,未完成再次执行
                # 也写一个,只执行子单函数
                success, order = self.create_order(request["symbol"], request["pid"], request["longShort"],
                                                   request["openClose"], request["volume"])
                if success:
                    ret = self.mt5Api.ExecOrder(order)
                    order = ret.order
                    if ret.req_success:
                        rsp.req_success = True
                        rsp.order = order
                    else:
                        rsp.req_success = False
                        rsp.req_errmsg = "ctp trade fail,pEntrustNo:{} ,msg".format(request["pid"], ret.errmsg)
                        order.status = models.REJECTED
                    self.update_order(order)
                else:
                    rsp.req_success = False
                    rsp.req_errmsg = f"create order fail"
                    log.warninging("create order fail,pEntrustNo:{} ".format(request["pid"]))
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)  # 发送响应
            elif request["request_type"] == models.REQ_POSITION:
                rsp = models.Response()
                positions = self.mt5Api.getPosition(request["symbol"])
                rsp.req_success = True
                rsp.positions = positions
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)

            elif request["request_type"] == models.REQ_SEARCH:
                rsp = models.Response()
                orders = self.get_order_from_pid(request["pid"])
                rsp.req_success = True
                rsp.orders = orders
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == models.REQ_LIQUIDATE:
                # 清仓
                while True:
                    success = self.close_all_orders(request["symbol"], request["pid"])
                    if success:
                        break
                rsp = models.Response()
                rsp.req_success = True
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == models.REQ_MARKET:
                rsp = models.Response()
                md = self.mt5Api.get_tick_price_from_symbol(request["symbol"])
                if md is not None:
                    rsp.req_success = md.req_success
                    rsp.market = md.market
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)
