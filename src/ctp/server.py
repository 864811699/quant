import datetime
import json

import logging
import time

from package.logger.logger import setup_logger

log = logging.getLogger('root')

import threading

from src.ctp import ctp_td
from src.ctp import ctp_md
from package.config import config
from package.db import db
from package.zmq import server
from package.zmq import models
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
        self.monitor_account_qry_thread = None
        self.monitor_cancel_order_thread = None
        self.loadCfg()
        self.ExchangeID = "SHFE"
        self.entrustNo = 0

        self.lock = threading.Lock()
        self.OrderDict = {}  # entrustNo-->order
        self.closed_pid_dict = {} # PENDING_CANCELED /CANCELED /不存在
        self.unfinished_order={}
        self.unfinished_order_lock=threading.Lock()

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
                                      self.baseConfig["pwd"], self.baseConfig["appid"], self.baseConfig["authcode"],
                                        self.baseConfig["subSymbol"],self.zmqConfig["accountServerAdrr"], self.zmqConfig["accountTopic"],self.baseConfig.get("name","未知"))
        self.zmqServer = server.ZmqServer(self.zmqConfig["reqrspPort"])
        log.info("server init api success!!!")

    def get_entrustNo(self):
        self.entrustNo =self.entrustNo+ 1
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
    def get_traded_order_from_pid(self,pid,openClose):
        orders=self.get_order_from_pid(pid)
        for order in orders:
            if order.status==comm.AllTrade and openClose==order.openClose:
                return order

    def save_order(self, order):
        now=datetime.datetime.now()
        self.store_order(order)
        self.db.save_child_order(self.dbConfig["table"],order)
        log.info("save DB order time:{}".format(datetime.datetime.now()-now))
        return True, ""


    def update_order(self, order):
        log.info("update order :{}".format(order))
        if order.status>=comm.PENDING_CANCELED:
            self.del_order_from_unfinished_order(order.entrustNo)
        now=datetime.datetime.now()
        self.store_order(order)
        self.db.update_child_order(self.dbConfig["table"],order)
        log.info("update DB order time:{}".format(datetime.datetime.now()-now))
        return True, ""

    def get_price(self,longshort, openclose,symbol):
        side = comm.getSide(longshort, openclose)
        market=self.ctpmdApi.get_market_dict(symbol)
        price=0
        if market is not None:
            price = market[comm.MARKET_BUY1] if side == comm.SIDE_DICT["BUY"] else market[comm.MARKET_SELL1]
        return price

    def create_order(self, symbol, pEntrustNo, longShort, openClose, Volume):
        # price = self.ctptdApi.GetPrice(self.ExchangeID, symbol, longShort, openClose)
        order = models.Order()
        order.account = self.baseConfig["user"]
        order.symbol = symbol
        order.pEntrustNo = pEntrustNo
        order.entrustNo = self.get_entrustNo()
        order.orderRef = str(order.entrustNo)
        order.longShort = longShort
        order.openClose = openClose
        order.parentAskQty = Volume
        order.askQty = Volume
        order.status = models.ORDER_STATUS_UNKNOWN

        price = self.get_price(longShort, openClose,symbol)
        if price ==0:
            log.warning("ctp get price fail,pEntrustNo:{}".format(pEntrustNo))
            order.status=6
            order.statusMsg="qry price fail"
        order.askPrice = price
        success, errmsg = self.save_order(order)

        if success:
            self.store_pending_order(order)
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
                        success, order = self.create_order(symbol, pid, longshort, models.TRADE_TYPE_CLOSE, 1)
                        if success:
                            ret = self.ctptdApi.ExecOrder(order)
                            order = ret.order
                            if not ret.reqSuccess:
                                order.status = models.REJECTED
                            self.update_order(order)
            return False
        else:
            # 无持仓可清时,则返回清理成功,否则均返回False
            return True

    def update_closed_orders(self):
        for order in self.OrderDict.values():
            if order.status == models.AllTrade:
                if order.pEntrustNo in self.closed_pid_dict:
                    if order.openClose == models.TRADE_TYPE_CLOSE:
                        self.closed_pid_dict[order.pEntrustNo] = comm.ORDER_CLOSED
                else:
                    if order.openClose == models.TRADE_TYPE_OPEN:
                        self.closed_pid_dict[order.pEntrustNo] = comm.ORDER_NEW
                    else:
                        self.closed_pid_dict[order.pEntrustNo] = comm.ORDER_CLOSED
    def loadOrdersFromDB(self):
        # 先导入db  然后查询
        self.entrustNo=self.db.get_max_id(self.dbConfig["table"])
        # 只能用 OrderSysID=236466 映射
        success, orders_dict = self.ctptdApi.load_orders_from_ctp()
        if success:
            if orders_dict is not None:
                log.info("qry orders from ctp success")
        else:
            log.warning("qry orders from ctp fail")
            exit(-2)
            return

        orders = self.db.load_child_all_orders(self.dbConfig["table"],self.baseConfig["subSymbol"])
        for order in orders:
            if self.entrustNo< order.entrustNo:
                self.entrustNo=order.entrustNo
            if order.status>3:
                self.OrderDict[order.entrustNo]=order
                continue
            ctp_order = orders_dict.get(order.entrustNo,0)
            if ctp_order==0:
                order.status = comm.REJECTED
                order.bidVol = 0.0
                order.bidPrice = 0.0

            else:
                order.status = ctp_order.status
                order.orderSysID = ctp_order.orderSysID
                order.bidVol = ctp_order.bidVol
                order.bidPrice = ctp_order.bidPrice
                self.OrderDict[order.entrustNo]=order
            self.update_order(order)
            if order.status <=comm.PARTTRADE:
                self.store_pending_order(order)
                self.ctptdApi.store_entrust_order(order)

    def get_orders_from_pid(self,pid):
        orders = []
        for order in self.OrderDict.values():
            if order.pEntrustNo == pid and order.status == 4:
                orders.append(order)
        return orders

    def check_order_need_to_close(self,request):
        order=None
        need_to_close = False
        if self.closed_pid_dict[request["pid"]] == comm.ORDER_CLOSED:
            order = self.get_traded_order_from_pid(request["pid"], request["openClose"])
        elif self.closed_pid_dict[request["pid"]] == comm.ORDER_PENDING_CLOSE:
            order = self.get_traded_order_from_pid(request["pid"], models.TRADE_TYPE_OPEN)
            order.status = comm.CANCELED
        else:
            need_to_close=True
        return need_to_close,order
    def clear_all_data(self):
        success,errmsg=self.db.clear_table(self.dbConfig["table"])
        if not success:
            log.error("clear table fail ,errmsg:{}".format(errmsg))
            return success,errmsg
        with self.lock:
            self.OrderDict = {}  # entrustNo-->order
        self.closed_pid_dict = {} # PENDING_CANCELED /CANCELED /不存在
        success=self.ctptdApi.clear_all_data()
        return success,""
    def update_order_closed_from_core(self,pids):
        orders=self.get_orders()
        for pid in pids:
            for order in orders:
                if order.pEntrustNo==pid:
                    order.status=comm.AllTrade
                    self.update_order(order)
                    self.ctptdApi.update_positions_from_server(order)
                    self.closed_pid_dict[order.pEntrustNo]=comm.ORDER_CLOSED
    def monitor_accountInfo(self):
        log.info("监控账户资金的查询启动~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        time.sleep(20)
        while True:
            rtn=self.ctptdApi.QryAccount()
            if rtn != 0:
                log.warning("monitor accountInfo qry fail,rtn={}".format(rtn))
            time.sleep(15)
    def store_pending_order(self,order):
        with self.unfinished_order_lock:
            self.unfinished_order[order.entrustNo]=order
    def del_order_from_unfinished_order(self,entrustNo):
        with self.unfinished_order_lock:
            self.unfinished_order.pop(entrustNo,None)
    def monitor_pending_orders(self):
        while True:
            time.sleep(0.1)
            with self.unfinished_order_lock:
                now = datetime.datetime.now()
                for v in self.unfinished_order.values():
                    if v.status == comm.NEW_ORDER:
                        if (now - v.reqTime).total_seconds() >= self.baseConfig["timeout_cancel_order"]:
                            log.info("成交超时,发起撤单,entrustNo={}".format(v.entrustNo))
                            cancelRef=self.get_entrustNo()+1000000
                            success,msg = self.ctptdApi.OrderCancel(v.entrustNo,str(cancelRef))
                            if not success:
                                log.warning("撤单失败, entrustNo:{},cancelRef:{}, errmsg:{}".format(v.entrustNo, cancelRef,  msg))
    def move_order(self,pEntrustNo_old,pEntrustNo_new):
        orders_old=self.get_order_from_pid(pEntrustNo_old)
        for order_old in orders_old:
            order_old.pEntrustNo=pEntrustNo_new
            self.closed_pid_dict[pEntrustNo_old] = comm.ORDER_CLOSED
            self.closed_pid_dict[pEntrustNo_new] = comm.ORDER_NEW
            self.update_order(order_old)
    def reboot_cancel_unfinished_order(self):
        for v in self.OrderDict.values():
            if v.status <=comm.PARTTRADE:
                cancelRef = self.get_entrustNo() + 1000000
                self.ctptdApi.OrderCancel(v.entrustNo, cancelRef)
    def run(self):
        self.ctpmdApi.Run()
        self.ctptdApi.Run()
        self.loadOrdersFromDB()
        self.reboot_cancel_unfinished_order()
        time.sleep(2)
        self.loadOrdersFromDB()
        self.update_closed_orders()
        self.ctptdApi.load_positions_from_ctp()
        if not self.monitor_account_qry_thread or not self.monitor_account_qry_thread.is_alive():
            self.monitor_account_qry_thread = threading.Thread(target=self.monitor_accountInfo)
            self.monitor_account_qry_thread.start()
        if not self.monitor_cancel_order_thread or not self.monitor_cancel_order_thread.is_alive():
            self.monitor_cancel_order_thread = threading.Thread(target=self.monitor_pending_orders)
            self.monitor_cancel_order_thread.start()
        log.info("CTP start success ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        # TODO  检查持仓 未实现

        while True:
            request_json = self.zmqServer.socket.recv_string()
            request = json.loads(request_json)
            log.info("recv request:{}".format(request))
            # 处理请求
            if request["request_type"] == models.REQ_ORDER:
                rsp = models.Response()
                if request["openClose"] == models.TRADE_TYPE_CLOSE:
                    need_to_close, order = self.check_order_need_to_close(request)
                    if need_to_close:
                        self.closed_pid_dict[request["pid"]] = comm.ORDER_PENDING_CLOSE
                    else:
                        rsp.req_success = True
                        rsp.order = order
                        json_str = rsp.to_json()
                        log.info("send order result: {}".format(rsp))
                        self.zmqServer.socket.send_string(json_str)  # 发送响应
                        continue
                else:
                    self.closed_pid_dict[request["pid"]] = comm.ORDER_NEW
                success, order = self.create_order(request["symbol"], request["pid"], request["longShort"], request["openClose"], request["volume"])
                if success:
                    if order.status==comm.ORDER_STATUS_UNKNOWN:
                        ret = self.ctptdApi.ExecOrder(order)
                        order = ret.order
                        if ret.reqSuccess:
                            rsp.req_success = True
                            rsp.order = order
                            if request["openClose"] == models.TRADE_TYPE_CLOSE:
                                if order.status==comm.AllTrade:
                                    self.closed_pid_dict[request["pid"]]=comm.ORDER_CLOSED
                                else:
                                    self.closed_pid_dict[request["pid"]] = comm.ORDER_NEW
                        else:
                            rsp.req_success = False
                            rsp.errmsg = "ctp trade fail,pEntrustNo:{} ,msg:{}".format(request["pid"], ret.errorMsg)
                            order.status = models.REJECTED
                    else:
                        rsp.req_success = True
                        rsp.order = order
                    self.update_order(order)
                else:
                    rsp.req_success = False
                    rsp.errmsg = f"create order fail"
                    log.warning("create order fail,pEntrustNo:{} ".format(request["pid"]))
                json_str = rsp.to_json()
                log.info("send order result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)  # 发送响应
            elif request["request_type"] == models.REQ_POSITION:
                # TODO 有异常
                rsp = models.Response()
                positions = self.ctptdApi.getPosition(request["symbol"])
                rsp.req_success = True
                rsp.positions = positions
                json_str = rsp.to_json()
                log.info("send position result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)

            elif request["request_type"] == models.REQ_SEARCH:
                rsp = models.Response()
                orders = self.get_orders_from_pid(request["pid"])
                rsp.req_success = True
                rsp.orders = orders
                json_str = rsp.to_json()
                log.info("send search result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == models.REQ_LIQUIDATE:
                while True:
                    success = self.close_all_orders(request["symbol"], request["pid"])
                    if success:
                        break
                rsp = models.Response()
                rsp.req_success = True
                json_str = rsp.to_json()
                log.info("send close orders result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == models.REQ_CLEAR:
                rsp = models.Response()
                success,errmsg=self.clear_all_data()
                rsp.req_success=success
                rsp.errmsg=errmsg
                json_str = rsp.to_json()
                log.info("send clear all data orders result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == models.REQ_UPDATE:
                rsp = models.Response()
                self.update_order_closed_from_core(request["closedOrders"])
                rsp.req_success=True
                rsp.errmsg=""
                json_str = rsp.to_json()
                log.info("send update order status orders result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == models.REQ_MOVE_ORDER:
                rsp = models.Response()
                self.move_order(request["pid_old"], request["pid"])
                rsp.req_success=True
                rsp.errmsg=""
                json_str = rsp.to_json()
                log.info("send move order result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)