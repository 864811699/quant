import datetime
import json
import time
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
from package.zmq import publisher
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
        self.monitor_thread = None
        self.accountInfo_zmq = None
        self.loadCfg()
        self.entrustNo = 0

        self.lock = threading.Lock()
        self.OrderDict = {}  # entrustNo-->order

        self.closed_pid_dict = {} # PENDING_CANCELED /CANCELED /不存在
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
        self.accountInfo_zmq = publisher.ZmqPublisher(self.zmqConfig["accountServerAdrr"], self.zmqConfig["accountTopic"],is_proxy=True)
        self.mt5Api = mt5.Mt5Api(self.baseConfig["path"], self.baseConfig["user"], self.baseConfig["pwd"],
                                 self.baseConfig["host"], self.baseConfig["fillType"])
        self.mt5Api.run()

        self.zmqServer = server.ZmqServer(self.zmqConfig["reqrspPort"])
        log.info("server init api success!!!, port:{}".format(self.zmqConfig["reqrspPort"]))

    def get_entrustNo(self):
        self.entrustNo += 1
        return self.entrustNo

    def get_order_by(self,entrustNo):
        with self.lock:
            return self.OrderDict.get(entrustNo,None)
    def store_order(self, order):
        with self.lock:
            self.OrderDict[order.entrustNo] = order

    def get_orders(self):
        orders = []
        with self.lock:
            for entrustNo in self.OrderDict.keys():
                orders.append(self.OrderDict[entrustNo])
        return orders

    def get_unfinished_orders(self):
        with self.lock:
            return {k:v for k ,v in self.OrderDict.items() if v.status in[comm.ORDER_STATUS_UNKNOWN,comm.ORDER_STATUS_PARTTRADE,comm.ORDER_STATUS_NOT_CONNECTED]}
    def get_order_from_pid(self, pid):
        orders = []
        with self.lock:
            for order in self.OrderDict.values():
                if order.pEntrustNo == pid and order.status==4:
                    orders.append(self.OrderDict[order.entrustNo])
        return orders
    def get_traded_order_from_pid_closeopen(self,pid,openClose):
        orders=self.get_order_from_pid(pid)
        for order in orders:
            if  openClose==order.openClose:
                return order

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
        if openClose==comm.TRADE_TYPE_CLOSE:
            orders=self.get_order_from_pid(pEntrustNo)
            for order_tmp in orders:
                if order_tmp.openClose==comm.TRADE_TYPE_OPEN:
                    order.orderRef=order_tmp.orderRef
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

    def check_order_need_to_close(self,request):
        order=None
        need_to_close = False
        if self.closed_pid_dict[request["pid"]] == comm.ORDER_CLOSED:
            order = self.get_traded_order_from_pid_closeopen(request["pid"], request["openClose"])

        elif self.closed_pid_dict[request["pid"]] == comm.ORDER_PENDING_CLOSE:
            order = self.get_traded_order_from_pid_closeopen(request["pid"], models.TRADE_TYPE_OPEN)
            order.status = comm.ORDER_STATUS_CANCELED
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
        return success,""
    def update_order_closed_from_core(self, pids):
        orders = self.get_orders()
        for pid in pids:
            for order in orders:
                if order.pEntrustNo == pid:
                    order.status = comm.ORDER_STATUS_AllTrade
                    self.update_order(order)
                    self.closed_pid_dict[order.pEntrustNo]=comm.ORDER_CLOSED
    def monitor_accountInfo(self):
        log.info("监控账户资金的查询启动~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        while True:
            success,account_info,errmsg=self.mt5Api.QryAccount()
            if not success :
                log.warning("monitor accountInfo qry fail,rtn={}".format(errmsg))
            else:
                # log.info("accountInfo qry success,account_info:{}".format(account_info))
                accountInfo = models.AccountInfo()
                accountInfo.account = self.baseConfig["user"]
                accountInfo.name = self.baseConfig.get("name","未知")
                accountInfo.symbol = self.baseConfig["subSymbol"]
                accountInfo.margin_level = account_info.margin_level
                accountInfo.equity = account_info.equity
                accountInfo.margin_free = account_info.margin_free
                json_str = accountInfo.to_json()
                self.accountInfo_zmq.publish(json_str)
                # log.info("accountInfo push success,accountInfo:{}".format(accountInfo))
            time.sleep(10)
    def loadOrdersFromDB(self):
        self.entrustNo = self.db.get_max_id(self.dbConfig["table"])
        orders = self.db.load_child_all_orders(self.dbConfig["table"], self.baseConfig["subSymbol"])
        from_t, to_t = utils.get_mt5_last_hours()
        success, mt5_orders_tuple = self.mt5Api.get_history_orders_from_time(from_t, to_t, self.baseConfig["subSymbol"])
        if not success:
            log.error("get history orders fail,errmsg={}".format(mt5_orders_tuple))
            return success, mt5_orders_tuple
        mt5_orders_dict = {}
        for mt5_order in mt5_orders_tuple:
            entrust_no=int(mt5_order.comment) if mt5_order.comment.strip() else 0
            if entrust_no !=0 :
                mt5_orders_dict[entrust_no]=mt5_order
        for order in orders:
            if self.entrustNo < order.entrustNo:
                self.entrustNo = order.entrustNo
            self.OrderDict[order.entrustNo] = order
            if order.status < 4 or order.status==comm.ORDER_STATUS_NOT_CONNECTED:
                if order.orderSysID == "":
                    if  order.entrustNo in mt5_orders_dict:
                        order.orderSysID = str(mt5_orders_dict[order.entrustNo].ticket)
                        order.orderRef= mt5_orders_dict[order.entrustNo].comment
                    else:
                        order.status = 6
                        order.statusMsg = "mt5未知状态"
                        self.OrderDict[order.entrustNo] = order
                        self.update_order(order)
                        continue
                success, msg, rsporders = self.mt5Api.getHistoryOrders(order.orderSysID)
                if success is False or len(rsporders) == 0:
                    log.warning("mt5 qry order [{}]fail,msg:{}".format(order.entrustNo, msg))
                    order.status = 6
                    order.statusMsg = "mt5未查询到"
                else:
                    order.orderRef=rsporders[0].comment
                    order.positionID = str(rsporders[0].position_id)
                    order.status = comm.ORDER_STATUS_AllTrade
                    order.bidVol = rsporders[0].volume_initial
                    order.bidPrice = rsporders[0].price_current
                    order.rspTime = utils.getLocalTimeFromMilliseconds(rsporders[0].time_done_msc)
                    log.info("qry from mt5 ,entrustNo:{},pEntrustNo:{},order:{}".format(order.entrustNo,order.pEntrustNo,rsporders))
                    self.OrderDict[order.entrustNo] = order
                self.update_order(order)
    def mt5_reconnect(self):
        self.mt5Api.run()
        local_orders_dict=self.get_unfinished_orders()
        from_t,to_t = utils.get_mt5_last_hours()
        success,mt5_orders_tuple=self.mt5Api.get_history_orders_from_time(from_t, to_t, self.baseConfig["subSymbol"])
        if not success:
            return success,mt5_orders_tuple
        mt5_orders_dict={}
        for mt5_order in mt5_orders_tuple:
            entrust_no=int(mt5_order.comment) if mt5_order.comment.strip() else 0
            if entrust_no !=0 :
                mt5_orders_dict[entrust_no]=mt5_order
        for k,v in local_orders_dict.items():
            if k in mt5_orders_dict:
                if v.pEntrustNo == mt5_orders_dict[k].magic:
                    v.orderSysID=str(mt5_orders_dict[k].ticket)
                    v.order=mt5_orders_dict[k].ticket
                    v.positionID=mt5_orders_dict[k].position_id
                    v.bidVol=mt5_orders_dict[k].volume_initial
                    v.bidPrice=mt5_orders_dict[k].price_current
                    v.status=comm.STATUS_TO_ZMQ[mt5_orders_dict[k].state]
                    v.orderRef=mt5_orders_dict[k].comment
                    v.rspTime=datetime.datetime.now()
                    self.update_order(v)
        return success,""
    def move_order(self,pEntrustNo_old,pEntrustNo_new):
        orders_old=self.get_order_from_pid(pEntrustNo_old)
        for order_old in orders_old:
            order_old.pEntrustNo=pEntrustNo_new
            self.closed_pid_dict[pEntrustNo_old] = comm.ORDER_CLOSED
            self.closed_pid_dict[pEntrustNo_new] = comm.ORDER_NEW
            self.update_order(order_old)
    def run(self):
        self.loadOrdersFromDB()
        self.update_closed_orders()
        if not self.monitor_thread or not self.monitor_thread.is_alive():
            self.monitor_thread = threading.Thread(target=self.monitor_accountInfo)
            self.monitor_thread.start()
        log.info("MT5 start success ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        while True:
            request_json = self.zmqServer.socket.recv_string()
            request = json.loads(request_json)
            # 处理请求
            if request["request_type"] == models.REQ_ORDER:
                log.info("recv request:{}".format(request))
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
                success, order = self.create_order(request["symbol"], request["pid"], request["longShort"],
                                                   request["openClose"], request["volume"])
                if success:
                    ret = self.mt5Api.ExecOrder(order)
                    order = ret.order
                    if ret.req_success:
                        rsp.req_success = True
                        rsp.order = order
                        if request["openClose"] == models.TRADE_TYPE_CLOSE:
                            if order.status == comm.ORDER_STATUS_AllTrade:
                                self.closed_pid_dict[request["pid"]] = comm.ORDER_CLOSED
                            else:
                                self.closed_pid_dict[request["pid"]] = comm.ORDER_NEW
                    elif not ret.req_success and ret.order.status==comm.ORDER_STATUS_NOT_CONNECTED:
                        rsp.order = ret.order
                        self.update_order(order)
                        self.mt5_reconnect()
                        order=self.get_order_by(order.entrustNo)
                        if order:
                            rsp.req_success = True
                            rsp.order = order
                        else:
                            rsp.req_success = False
                            rsp.errmsg =ret.errmsg
                    else:
                        rsp.req_success = False
                        rsp.errmsg = "ctp trade fail,pEntrustNo:{} ,msg".format(request["pid"], ret.order.statusMsg)
                        order.status = models.REJECTED
                    self.update_order(order)
                else:
                    rsp.req_success = False
                    rsp.errmsg = f"create order fail"
                    log.warning("create order fail,pEntrustNo:{} ".format(request["pid"]))
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)  # 发送响应
                log.info("send order result,{} ".format(rsp))
            elif request["request_type"] == models.REQ_POSITION:
                log.info("recv request:{}".format(request))
                rsp = models.Response()
                positions = self.mt5Api.getPosition(request["symbol"])
                rsp.req_success = True
                rsp.positions = positions
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)

            elif request["request_type"] == models.REQ_SEARCH:
                log.info("recv request:{}".format(request))
                rsp = models.Response()
                orders = self.get_order_from_pid(request["pid"])
                rsp.req_success = True
                rsp.orders = orders
                json_str = rsp.to_json()
                self.zmqServer.socket.send_string(json_str)
                log.info("search order {} success,pEntrustNo:{} ".format(request["pid"],orders))
            elif request["request_type"] == models.REQ_LIQUIDATE:
                log.info("recv request:{}".format(request))
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
            elif request["request_type"] == models.REQ_CLEAR:
                log.info("recv request:{}".format(request))
                rsp = models.Response()
                success,errmsg=self.clear_all_data()
                rsp.req_success=success
                rsp.errmsg=errmsg
                json_str = rsp.to_json()
                log.info("send clear all data orders result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] == models.REQ_UPDATE:
                log.info("recv request:{}".format(request))
                rsp = models.Response()
                self.update_order_closed_from_core(request["closedOrders"])
                rsp.req_success = True
                rsp.errmsg = ""
                json_str = rsp.to_json()
                log.info("send update order status orders result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] ==  models.REQ_RECONNECT:
                log.info("recv request:{}".format(request))
                rsp = models.Response()
                success,errmsg=self.mt5_reconnect()
                if success:
                    rsp.req_success = True
                    rsp.errmsg = ""
                else:
                    rsp.req_success = False
                    rsp.errmsg = errmsg
                json_str = rsp.to_json()
                log.info("send reconnect mt5 result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)
            elif request["request_type"] ==  models.REQ_MOVE_ORDER:
                log.info("recv request:{}".format(request))
                rsp = models.Response()
                self.move_order(request["pid_old"],request["pid"])
                rsp.req_success=True
                rsp.errmsg=""
                json_str = rsp.to_json()
                log.info("send move order mt5 result: {}".format(rsp))
                self.zmqServer.socket.send_string(json_str)