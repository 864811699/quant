import json

import logging

from package.logger.logger import setup_logger

log = logging.getLogger('root')

import threading

from src.ctp import ctp_td
from src.ctp import ctp_md
from package.config import config
from package.db import db
from package.zmq import server
from package.zmq import struct
from src.ctp import comm


class Server:
    def __init__(self, baseConfigFile, strategyConfigFile):
        self.baseConfigFile = baseConfigFile
        self.strategyConfigFile = strategyConfigFile
        self.baseConfig = None
        self.dbConfig = None
        self.zmqConfig = None
        self.cfg = None
        self.db = None
        self.ctpmdApi = None
        self.ctptdApi = None
        self.loadCfg()
        self.ExchangeID = "SHFE"
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
        self.ctpmdApi = ctp_md.CMdImpl(self.baseConfig["mdhost"], self.baseConfig["subSymbol"], self.zmqConfig["subpubPort"], self.zmqConfig["topic"])
        self.ctptdApi = ctp_td.TdImpl(self.baseConfig["tdhost"], self.baseConfig["broker"], self.baseConfig["user"],
                                      self.baseConfig["pwd"], self.baseConfig["appid"], self.baseConfig["authcode"])
        self.zmqServer = server.ZmqServer(self.zmqConfig["reqrspPort"])
        log.info("server init api success!!!")

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
                if order.pEntrustNo == pid:
                    orders.append(self.OrderDict[order.entrustNo])
        return orders

    def save_order(self, order):
        self.store_order(order)
        self.db.save_child_order(self.dbConfig["table"],order)
        return True, ""


    def update_order(self, order):
        self.store_order(order)
        self.db.update_child_order(self.dbConfig["table"],order)
        return True, ""

    def create_order(self, symbol, pEntrustNo, longShort, openClose, Volume):
        price = self.ctptdApi.GetPrice(self.ExchangeID, symbol, longShort, openClose)
        if price == 0:
            log.warning("ctp get price fail,pEntrustNo:{}".format(pEntrustNo))
            return False, None
        order = struct.Order()
        order.account = self.baseConfig["user"]
        order.symbol = symbol
        order.pEntrustNo = pEntrustNo
        order.entrustNo = self.get_entrustNo()
        order.orderRef = str(order.entrustNo)
        order.longShort = longShort
        order.openClose = openClose
        order.askPrice = price
        order.askQty = Volume
        order.status = struct.ORDER_STATUS_UNKNOWN
        success, errmsg = self.save_order(order)
        if success:
            return True, order
        else:
            log.warning("ctp save order fail,pEntrustNo:{}  error:{}".format(pEntrustNo, errmsg))
            return False, None

    def close_all_orders(self, symbol, pid):
        positions_dict = self.ctptdApi.getPosition(symbol)
        if positions_dict is not None and len(positions_dict) > 0:
            for longshort, vol in positions_dict.items():
                if vol != 0:
                    for n in range(vol):
                        success, order = self.create_order(symbol, pid, longshort, struct.TRADE_TYPE_CLOSE, 1)
                        if success:
                            ret = self.ctptdApi.ExecOrder(order)
                            if ret.reqSuccess:
                                order = ret.order
                            else:
                                order.status = struct.REJECTED
                            self.update_order(order)
            return False
        else:
            # 无持仓可清时,则返回清理成功,否则均返回False
            return True

    def loadOrdersFromDB(self):
        success, orders_dict = self.ctptdApi.load_orders_from_ctp()
        if success:
            if orders_dict is not None:
                log.info("qry orders from ctp success")
        else:
            log.warning("qry orders from ctp fail")
            return

        orders = self.db.load_child_all_orders(self.dbConfig["table"],self.baseConfig["subMarket"])
        for order in orders:
            if self.entrustNo< order.entrustNo:
                self.entrustNo=order.entrustNo
            self.OrderDict[order.entrustNo] = order
            ctp_order= orders_dict.get(order.entrustNo,0)
            if ctp_order==0:
                order.status = comm.REJECTED
                order.tradedVol = 0.0
                order.bidPrice = 0.0
                self.OrderDict[order.entrustNo]=order
            else:
                order.status = ctp_order.status
                order.orderSysID = ctp_order.orderSysID
                order.bidVol = ctp_order.bidVol
                order.bidPrice = ctp_order.bidPrice
                self.OrderDict[order.entrustNo]=order
            self.update_order(order)


    def run(self):
        self.loadOrdersFromDB()
        self.ctpmdApi.Run()
        self.ctptdApi.Run()
        while True:
            request_json = self.zmqServer.socket.recv_string()
            request = json.loads(request_json)
            log.info("recv request:{}".format(request))
            # 处理请求
            if request["request_type"] == struct.REQ_ORDER:
                rsp = struct.Response()
                success, order = self.create_order(request["symbol"], request["pid"], request["longShort"], request["openClose"], request["volume"])
                if success:
                    ret = self.ctptdApi.ExecOrder(order)
                    if ret.reqSuccess:
                        rsp.req_success = True
                        order = ret.order
                        rsp.order = order
                    else:
                        rsp.req_success = False
                        rsp.req_errmsg = "ctp trade fail,pEntrustNo:{} ,msg".format(request["pid"], ret.errorMsg)
                        order.status = struct.REJECTED
                    self.update_order(order)
                else:
                    rsp.req_success = False
                    rsp.req_errmsg = f"create order fail"
                    log.warning("create order fail,pEntrustNo:{} ".format(request["pid"]))
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)  # 发送响应
            elif request["request_type"] == struct.REQ_POSITION:
                rsp = struct.Response()
                positions = self.ctptdApi.getPosition(request["symbol"])
                rsp.req_success = True
                rsp.positions = positions
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)

            elif request["request_type"] == struct.REQ_SEARCH:
                rsp = struct.Response()
                orders = self.ctptdApi.getFinishedOrderFromPid(request["pid"])
                rsp.req_success = True
                rsp.orders = orders
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == struct.REQ_LIQUIDATE:
                while True:
                    success = self.close_all_orders(request["symbol"], request["pid"])
                    if success:
                        break
                rsp = struct.Response()
                rsp.req_success = True
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)
