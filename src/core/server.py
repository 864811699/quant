import datetime
import threading

import concurrent.futures

import logging

from package.logger.logger import setup_logger

log = logging.getLogger('root')

from package.config import config

from src.mt5 import comm as mt5comm
from src.core import comm
from package.db import db
from package.notify import notify
from src.core import util
from src.core.util import float_equal
from package.zmq import client
from package.zmq import subscriber
from package.zmq import models


class Server:
    def __init__(self, baseConfigFile, strategyConfigFile):
        self.baseConfigFile = baseConfigFile
        self.strategyConfigFile = strategyConfigFile
        self.webConfig = None
        self.notifyConfig = None
        self.zmqConfig = None
        self.dbConfig = None
        self.thread = None
        self.notifyApi = None
        self.zmqCtpClient = None
        self.zmqCtpMarket = None
        self.zmqUSDClient = None
        self.zmqXAUClient = None

        self.lock = threading.Lock()
        self.strategyConfig = None
        self.cfg = None
        self.loadCfg()
        # self.queueCtpMD = queue.Queue()
        self.strategyStatus = False

        self.OrdersDict = {}  # pid->POrder
        self.entrustNo = 0
        self.ErrorOrderDict = {}  #

        log.info("server init success!!!")

    def loadCfg(self):
        cfg = config.Config(self.baseConfigFile, self.strategyConfigFile)
        cfg.load_config()
        self.cfg = cfg
        self.webConfig = cfg.get_web_config()
        log.info("web server config :{}".format(self.webConfig))

        self.notifyConfig = cfg.get_notify_config()
        log.info("notify config: {}".format(self.notifyConfig))

        self.dbConfig = cfg.get_db_config()
        log.info("db config: {}".format(self.dbConfig))

        self.zmqConfig = cfg.get_zmq_config()
        log.info("zmq config: {}".format(self.zmqConfig))

        self.strategyConfig = cfg.getStrategyConfig()
        log.info("strategy config: {}".format(cfg.getStrategyConfig()))

    def init_api(self):
        self.db = db.dbServer(self.dbConfig)
        self.db.create_parent_table()

        self.notifyApi = notify.Notify(self.notifyConfig['url'], self.notifyConfig['successAudio'],
                                       self.notifyConfig['failAudio'],
                                       self.notifyConfig['mentioned_list'])

        self.zmqCtpClient = client.ZmqClient(self.zmqConfig['ctpReqAddr'])
        self.zmqCtpMarket = subscriber.ZmqSubscriber(self.zmqConfig['ctpSubAddr'], self.zmqConfig['topic'])
        self.zmqUSDClient = client.ZmqClient(self.zmqConfig['mt5USDCNHReqAddr'])
        self.zmqXAUClient = client.ZmqClient(self.zmqConfig['mt5XAUUSDReqAddr'])
        log.info("server init api success!!!")

    def check_send_status(self, success, msg):
        # zmq 发送失败关闭程序
        if not success:
            log.error("zmq send fail,msg:{}".format(msg))
            self.notifyApi.notify_net_error(msg)
            exit(-1)

    def getEntrustNo(self):
        self.entrustNo += 1
        return self.entrustNo

    def save_order(self, order):
        self.OrdersDict[order.entrustNo] = order
        self.db.save_parent_order(self.dbConfig["table"], order)
        return True, ""

    def updateOrder(self, order):
        self.OrdersDict[order.entrustNo] = order
        self.db.update_parent_order(self.dbConfig["table"], order)
        return True, ""

    def create_order(self, longShort, CTPAUAskPrice, CTPAUBidPrice, MT5AUAskPrice, MT5AUBidPrice, USDAskPrice,
                     USDBidPrice, spread, realOpenSpread):
        order = models.POrder()
        order.pEntrustNo = self.getEntrustNo()
        order.longShort = longShort
        order.CTPAUAskPrice = CTPAUAskPrice
        order.CTPAUBidPrice = CTPAUBidPrice
        order.MT5AUAskPrice = MT5AUAskPrice
        order.MT5AUBidPrice = MT5AUBidPrice
        order.USDAskPrice = USDAskPrice
        order.USDBidPrice = USDBidPrice
        order.spread = spread
        order.status = 0
        success, errmsg = self.save_order(order)
        if success:
            return True, order
        else:
            log.warning("save order fail,pEntrustNo:{}  error:{}".format(order.pEntrustNo, errmsg))
            return False, None

    def getNoFinishOrders(self):
        orders = []
        for order in self.OrdersDict.values():
            if order.status < 6 and order.status > 0:
                orders.append(order)
        return orders

    def getPosition(self, longShort):
        vol = 0.0
        for order in self.OrdersDict.values():
            if order.longShort == longShort and order.status > 0 and order.status < 4:
                vol += order.volume

        return vol

    def openOrder(self, vol, action, spread, order):
        current_positon = self.getPosition(action)
        max_position = self.strategyConfig["vol"] * vol
        openOrderStatus = False
        # 当前持仓值,当前应该应该持有的最大持仓量
        if current_positon < max_position:
            # 0创建/ 1ctp开仓 / 2伦敦金开仓/ 3汇率开仓 /4 ctp平仓 /5 伦敦金平仓/ 6汇率平仓
            success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, self.strategyConfig["op1"]["symbol"],
                                                              action, comm.OFFSET_OPEN,
                                                              self.strategyConfig["op1"]["rate"], order.entrustNo)
            self.check_send_status(success, "ctp open order")

            if rtnExecOrder.reqSuccess:
                order.status = 1
                log.info(
                    "ctp open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op1"]["symbol"], action,
                                                                       self.strategyConfig["op1"]["rate"],
                                                                       rtnExecOrder.order.bidPrice))
                self.updateOrder(order)
                self.notifyApi.notify_trade_success(spread, self.strategyConfig["op1"]["symbol"], action,
                                                    comm.OFFSET_OPEN, self.strategyConfig["op1"]["rate"],
                                                    self.strategyConfig["op1"]["rate"])
                # symbol, magic, longShort, openClose, volume
                # 先执行 伦敦金
                xau_p = 0.0
                usd_p = 0.0
                # mt5 伦敦金成交 订单成交通知
                xauusd_askQty = self.strategyConfig["op2"]["rate"]
                xauusd_bidQty = 0

                mt5Action = util.get_longShort_from_ctp_longShort(action)
                while True:
                    success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient,
                                                                     self.strategyConfig["op2"]["symbol"],
                                                                     order.entrustNo, mt5Action, comm.OFFSET_OPEN,
                                                                     xauusd_askQty)
                    self.check_send_status(success, "xau open order")
                    if rtnMt5Exec1.reqSuccess:
                        xau_p = rtnMt5Exec1.order.bidPrice
                        if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                            xauusd_askQty -= rtnMt5Exec1.order.bidVol
                            xauusd_bidQty += rtnMt5Exec1.order.bidVol
                            log.info(
                                "mt5 open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op2"]["symbol"],
                                                                                   action, xauusd_bidQty, xau_p))
                        if float_equal(xauusd_askQty, 0):
                            order.status = 2
                            self.updateOrder(order)
                            self.notifyApi.notify_trade_success(spread, self.strategyConfig["op2"]["symbol"], mt5Action,
                                                                comm.OFFSET_OPEN, xauusd_askQty, xauusd_bidQty)
                            break

                    else:
                        log.warning("mt5 open fail,spread:{}, {}  {}  {}  {}".format(spread, self.strategyConfig["op2"][
                            "symbol"], mt5Action, xauusd_askQty, rtnMt5Exec1.msg))
                        #  发送成交失败通知
                        self.notifyApi.notify_trade_fail(spread, self.strategyConfig["op2"]["symbol"], mt5Action,
                                                         comm.OFFSET_OPEN,
                                                         xauusd_askQty, rtnMt5Exec1.msg)
                        self.strategyStatus = False
                        break

                #  mt5 汇率成交  订单成交通知
                usdcnh_askQty = self.strategyConfig["op3"]["rate"]
                usdcnh_bidQty = 0
                while True:
                    success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient,
                                                                     self.strategyConfig["op3"]["symbol"],
                                                                     order.entrustNo, mt5Action,
                                                                     comm.OFFSET_OPEN, usdcnh_askQty)
                    self.check_send_status(success, "usd open order")
                    #  mt5 汇率成交  订单成交通知
                    if rtnMt5Exec2.reqSuccess:
                        usd_p = rtnMt5Exec2.order.bidPrice
                        if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                            usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                            usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                            log.info(
                                "mt5 open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op3"]["symbol"],
                                                                                   action,
                                                                                   usdcnh_bidQty, usd_p))
                        if float_equal(usdcnh_askQty, 0):
                            order.status = 3
                            self.updateOrder(order)
                            self.notifyApi.notify_trade_success(spread, self.strategyConfig["op3"]["symbol"], mt5Action,
                                                                comm.OFFSET_OPEN,
                                                                usdcnh_askQty, usdcnh_bidQty)
                            break

                    else:
                        log.warning("mt5 open fail,spread:{}, {}  {}  {}  {}".format(spread, self.strategyConfig["op3"][
                            "symbol"], mt5Action, usdcnh_askQty, rtnMt5Exec2.msg))
                        #  发送成交失败通知
                        self.notifyApi.notify_trade_fail(spread, self.strategyConfig["op3"]["symbol"], mt5Action,
                                                         comm.OFFSET_OPEN,
                                                         usdcnh_askQty, rtnMt5Exec2.msg)
                        self.strategyStatus = False
                        break

                # 计算实际点差
                realSpread = util.get_caculate_spread_from_price(rtnExecOrder.order.bidPrice, xau_p, usd_p)
                order.realOpenSpread = realSpread
                order.closeSpread = util.get_caculate_close_spread(realSpread, order.longShort,
                                                                   self.strategyConfig["rangeSpread"])
                self.updateOrder(order)
                log.info("server open order success,{}".format(order))
                # 汇总通知
                self.notifyApi.send_trade_result(self.strategyConfig["base"]["startSpread"],
                                                 self.strategyConfig["base"]["rangeSpread"], spread, action,
                                                 comm.OFFSET_OPEN,
                                                 current_positon + 1)
                openOrderStatus = True
            else:
                log.info("server open order fail,msg:{}".format(rtnExecOrder.errorMsg))
                self.notifyApi.notify_trade_fail(spread, self.strategyConfig["op1"]["symbol"], action, comm.OFFSET_OPEN,
                                                 self.strategyConfig["op1"]["rate"], rtnExecOrder.errorMsg)
        else:
            log.info(
                "current_position[{}] >= max_position[{}], not to open order".format(current_positon, max_position))
        return openOrderStatus

    def openAddOrder(self, order):
        xau_p = 0.0
        usd_p = 0.0
        ctp_p = 0.0
        success, ctp_orders = util.qry_child_order_from_pid(self.zmqCtpClient, order.entrustNo)
        self.check_send_status(success, "ctp search")

        for order in ctp_orders:
            if order.status == 4 and order.openClose == comm.OFFSET_OPEN:
                ctp_p = order.bidPrice
        if order.status == 1:
            # 开伦敦金
            mt5Action = util.get_longShort_from_ctp_longShort(order.longShort)
            xauusd_askQty = self.strategyConfig["op2"]["rate"]
            xauusd_bidQty = 0
            while True:
                success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient,
                                                                 self.strategyConfig["op2"]["symbol"], order.entrustNo,
                                                                 mt5Action,
                                                                 comm.OFFSET_OPEN, self.strategyConfig["op2"]["rate"])
                self.check_send_status(success, "xau open add order")
                if rtnMt5Exec1.reqSuccess:
                    xau_p = rtnMt5Exec1.order.bidPrice
                    if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                        xauusd_askQty -= rtnMt5Exec1.order.bidVol
                        xauusd_bidQty += rtnMt5Exec1.order.bidVol
                        log.info(
                            "mt5 open add success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op2"]["symbol"],
                                                                                   mt5Action, xauusd_bidQty, xau_p))
                    if float_equal(xauusd_askQty, 0):
                        order.status = 2
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op2"]["symbol"],
                                                            mt5Action,
                                                            comm.OFFSET_OPEN,
                                                            xauusd_askQty, xauusd_bidQty)
                        break

                else:
                    #  发送成交失败通知
                    log.warning("mt5 open add fail,spread:{}, {}  {}  {}  {}".format(order.spread,
                                                                                     self.strategyConfig["op2"][
                                                                                         "symbol"], mt5Action,
                                                                                     xauusd_askQty, rtnMt5Exec1.msg))
                    self.notifyApi.notify_trade_fail(order.spread, self.strategyConfig["op2"]["symbol"], mt5Action,
                                                     comm.OFFSET_OPEN,
                                                     xauusd_askQty, rtnMt5Exec1.msg)
                    self.strategyStatus = False
                    break

        if order.status == 2:
            #  mt5 汇率成交  订单成交通知
            mt5Action = util.get_longShort_from_ctp_longShort(order.longShort)
            usdcnh_askQty = self.strategyConfig["op3"]["rate"]
            usdcnh_bidQty = 0
            while True:
                success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient,
                                                                 self.strategyConfig["op3"]["symbol"], order.entrustNo,
                                                                 mt5Action,
                                                                 comm.OFFSET_OPEN,
                                                                 self.strategyConfig["op3"]["rate"])
                self.check_send_status(success, "usd open add order")
                if rtnMt5Exec2.reqSuccess:
                    usd_p = rtnMt5Exec2.order.bidPrice
                    if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                        usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                        usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                        log.info(
                            "mt5 open add success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op3"]["symbol"],
                                                                                   mt5Action,
                                                                                   usdcnh_bidQty, usd_p))
                    if float_equal(usdcnh_askQty, 0):
                        order.status = 3
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op2"]["symbol"],
                                                            mt5Action,
                                                            comm.OFFSET_OPEN,
                                                            usdcnh_askQty, usdcnh_bidQty)
                        break

                else:
                    #  发送成交失败通知
                    log.warning("mt5 open add fail,spread:{}, {}  {}  {}  {}".format(order.spread,
                                                                                     self.strategyConfig["op3"][
                                                                                         "symbol"], mt5Action,
                                                                                     usdcnh_askQty, rtnMt5Exec2.msg))
                    self.notifyApi.notify_trade_fail(order.spread, self.strategyConfig["op2"]["symbol"], mt5Action,
                                                     comm.OFFSET_OPEN,
                                                     usdcnh_askQty, rtnMt5Exec1.errmsg)
                    self.strategyStatus = False
                    break

            # 计算实际点差
            realSpread = util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
            order.realOpenSpread = realSpread
            order.closeSpread = realSpread - self.strategyConfig["rangeSpread"]
            self.updateOrder(order)
            self.notifyApi.send_trade_result(self.strategyConfig["base"]["startSpread"],
                                             self.strategyConfig["base"]["rangeSpread"], order.spread, order.longShort,
                                             comm.OFFSET_OPEN, self.getPosition(mt5Action))

    def closeOrder(self, order):
        # 根据order 状态平仓, 3 平全部,4 平mt5,5平外汇
        closeOrderStatus = False
        ctp_p = 0.0
        xau_p = 0.0
        usd_p = 0.0
        if order.status == 3:
            # 4 ctp平仓 /5 伦敦金平仓/ 6汇率平仓
            success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, self.strategyConfig["op1"]["symbol"],
                                                              order.longShort,
                                                              comm.OFFSET_CLOSE,
                                                              self.strategyConfig["op1"]["rate"], order.entrustNo)
            self.check_send_status(success, "ctp close order")
            if rtnExecOrder.reqSuccess:
                order.status = 4
                ctp_p = rtnExecOrder.order.bidPrice
                self.updateOrder(order)
                log.info("server close ctp order success,msg:{}".format(rtnExecOrder))
            else:
                log.info("server close ctp order fail,msg::{}".format(rtnExecOrder.errorMsg))
                self.notifyApi.notify_trade_fail(order.speard, self.strategyConfig["op1"]["symbol"], order.longShort,
                                                 comm.OFFSET_CLOSE, self.strategyConfig["op1"]["rate"],
                                                 rtnExecOrder.errorMsg)
        elif order.status > 3:
            # 获取ctp成交价
            ctp_orders = util.qry_child_order_from_pid(self.zmqCtpClient, order.entrustNo)
            for order in ctp_orders:
                if order.status == 4 and order.openClose == comm.OFFSET_CLOSE:
                    ctp_p = order.bidPrice
        mt5Action = util.get_longShort_from_ctp_longShort(order.longShort)
        if order.status == 4:
            # 平 伦敦金
            xauusd_askQty = self.strategyConfig["op2"]["rate"]
            xauusd_bidQty = 0
            while True:
                success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient,
                                                                 self.strategyConfig["op2"]["symbol"], order.entrustNo,
                                                                 mt5Action,
                                                                 comm.OFFSET_CLOSE, self.strategyConfig["op2"]["rate"])
                self.check_send_status(success, "xau close order")
                if rtnMt5Exec1.reqSuccess:
                    xau_p = rtnMt5Exec1.order.bidPrice
                    if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                        xauusd_askQty -= rtnMt5Exec1.order.bidVol
                        xauusd_bidQty += rtnMt5Exec1.order.bidVol
                        log.info(
                            "mt5 close success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op2"]["symbol"],
                                                                                mt5Action, xauusd_bidQty, xau_p))
                    if float_equal(xauusd_askQty, 0):
                        order.status = 5
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op2"]["symbol"],
                                                            mt5Action, comm.OFFSET_CLOSE, xauusd_askQty, xauusd_bidQty)
                        break

                else:
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread,
                                                                                  self.strategyConfig["op2"]["symbol"],
                                                                                  mt5Action, xauusd_askQty,
                                                                                  rtnMt5Exec1.msg))
                    #  发送成交失败通知
                    self.notifyApi.notify_trade_fail(self.strategyConfig["op2"]["symbol"], mt5Action,
                                                     comm.OFFSET_CLOSE,
                                                     self.strategyConfig["op2"]["rate"], rtnMt5Exec1.msg)
        elif order.status > 4:
            mt5_orders = util.qry_child_order_from_pid(self.zmqXAUClient, order.entrustNo)
            for order in mt5_orders:
                if order.status == 4 and order.openClose == comm.OFFSET_CLOSE and order.symbol == \
                        self.strategyConfig["op2"]["symbol"]:
                    mt5_p = order.bidPrice

        if order.status == 5:
            #  mt5 汇率成交  订单成交通知
            usdcnh_askQty = self.strategyConfig["op3"]["rate"]
            usdcnh_bidQty = 0
            while True:
                success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient,
                                                                 self.strategyConfig["op3"]["symbol"], order.entrustNo,
                                                                 mt5Action, comm.OFFSET_CLOSE,
                                                                 self.strategyConfig["op3"]["rate"])
                self.check_send_status(success, "xau close order")
                if rtnMt5Exec2.reqSuccess:
                    usd_p = rtnMt5Exec2.order.bidPrice
                    if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                        usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                        usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                        log.info(
                            "mt5 open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op3"]["symbol"],
                                                                               mt5Action, usdcnh_bidQty, usd_p))
                    if float_equal(usdcnh_askQty, 0):
                        order.status = 6
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op3"]["symbol"],
                                                            mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, usdcnh_bidQty)
                        break
                else:
                    #  发送成交失败通知
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread,
                                                                                  self.strategyConfig["op3"]["symbol"],
                                                                                  mt5Action, usdcnh_askQty,
                                                                                  rtnMt5Exec2.msg))
                    self.notifyApi.notify_trade_fail(self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE,
                                                     self.strategyConfig["op2"]["rate"], rtnMt5Exec2.msg)

            # 计算实际点差
            realSpread = util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
            order.realCloseSpread = realSpread
            order.closed_at = datetime.datetime.now()
            self.updateOrder(order)
            # 汇总通知 start, strategy_range, spread,longshort, openClose, position
            self.notifyApi.send_trade_result(self.strategyConfig["base"]["startSpread"],
                                             self.strategyConfig["base"]["rangeSpread"], order.spread, order.longShort,
                                             comm.OFFSET_CLOSE, self.getPosition(mt5Action))
            closeOrderStatus = True

        return closeOrderStatus

    def closeAllOrders(self):
        # 清仓指令开始时先 关闭策略
        self.StrategyStatus = False
        closeAllOrdersStatus = False
        while True:
            orders = self.getNoFinishOrders()
            needCloseN = len(orders)
            if needCloseN == 0:
                return closeAllOrdersStatus
            else:
                log.info("server need to close orders vol: {}".format(needCloseN))
                # 清仓 ctp
                entrustNo = self.getEntrustNo()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future1 = executor.submit(util.sed_close_all_to_server, self.zmqCtpClient,
                                              self.strategyConfig["op1"]["symbol"], entrustNo)
                    future2 = executor.submit(util.sed_close_all_to_server, self.zmqXAUClient,
                                              self.strategyConfig["op2"]["symbol"], entrustNo)
                    future3 = executor.submit(util.sed_close_all_to_server, self.zmqUSDClient,
                                              self.strategyConfig["op3"]["symbol"], entrustNo)
                    close_ctp_status = future1.result()
                    close_mt51_status = future2.result()
                    close_mt52_status = future3.result()
                    if close_ctp_status is False or close_mt51_status is False or close_mt52_status is False:
                        close_fail_symbol = ""
                        if close_ctp_status is False:
                            close_fail_symbol += self.strategyConfig["op1"]["symbol"] + "  "
                        if close_mt51_status is False:
                            close_fail_symbol += self.strategyConfig["op2"]["symbol"] + "  "
                        if close_mt52_status is False:
                            close_fail_symbol += self.strategyConfig["op3"]["symbol"]
                        self.notifyApi.notify_close_all_order_fail(close_fail_symbol)
                        log.warning("server closed all orders fail !!!!!!!!!!!!!!! ")
                    log.info("server closed all orders success !!!!!!!!!!!!!!!")

    def checkShouldCloseOrder(self, ctpMarket, XAUUSDm, USDCNHm):
        orders = self.getNoFinishOrders()
        for order in orders:
            is_close, spread = util.should_close_order(ctpMarket, XAUUSDm, USDCNHm, order)
            log.info(
                "should_close_order :{} ,pOrder:{} ,current_spread:{},close_spread:{}".format(is_close, order.PID,
                                                                                              spread,
                                                                                              order.closeSpread))
            if is_close == True:
                self.closeOrder(order)

    def checkShouldOpenOrder(self, ctpMarket, XAUUSDm, USDCNHm):
        is_open, vol, action, spread = util.should_open_order(ctpMarket, XAUUSDm, USDCNHm,
                                                              self.strategyConfig["startSpread"],
                                                              self.strategyConfig["rangeSpread"])
        log.info("should_open_order :{} ,current_spread:{},start_spread:{},range_spread:{}".format(is_open, spread,
                                                                                                   self.strategyConfig[
                                                                                                       "startSpread"],
                                                                                                   self.strategyConfig[
                                                                                                       "rangeSpread"]))
        if is_open:

            success, order = self.create_order(action, ctpMarket.askPrice1, ctpMarket.bidPrice1, XAUUSDm.askPrice1,
                                               XAUUSDm.bidPrice1, USDCNHm.askPrice1, USDCNHm.bidPrice1, spread)
            if not success:
                self.notifyApi.notify_net_error("save db")
                exit(-1)
            self.openOrder(vol, action, spread, order)

    def loadOrdersFromDB(self):
        # 导入本地mysql 所有委托,
        # 查询ctp/mt5 委托

        # 获取entrustNo
        # 整理数据,检查持仓(清理异常持仓)
        # maxEntrustNo
        pOrders = self.db.load_parent_orders(self.dbConfig["table"])
        for porder in pOrders:
            # 开平仓是按顺序来执行,CTP 只有一笔 ,MT5可能多笔
            if porder.entrustNo > self.entrustNo:
                self.entrustNo = porder.entrustNo
            if porder.status < comm.PARENT_STATUS_CLOSED and porder.status>comm.PARENT_STATUS_OPEN_PENDING:
                # 母单已开单,但未平完
                success, ctpOrders = util.qry_child_order_from_pid(self.zmqCtpClient, porder.entrustNo)
                self.check_send_status(success,"ctp search")

                success, usdOrders = util.qry_child_order_from_pid(self.zmqUSDClient, porder.entrustNo)
                self.check_send_status(success, "usd search")

                success, xauOrders = util.qry_child_order_from_pid(self.zmqXAUClient, porder.entrustNo)
                self.check_send_status(success, "xau search")

                porder.status = util.get_trade_vol_from_order(ctpOrders) + util.get_trade_vol_from_order(usdOrders) + util.get_trade_vol_from_order(xauOrders)
                self.OrdersDict[porder.entrustNo] = porder
                if porder.status == 1 or porder.status == 2 or porder.status == 4 or porder.status == 5:
                    self.ErrorOrderDict[porder.entrustNo] = porder

    def addOrder(self):
        # 异常委托补单
        for errorOrder in self.ErrorOrderDict:
            if errorOrder.status == 4 or errorOrder.status == 5:
                self.closeOrder(errorOrder)
            elif errorOrder.status == 2 or errorOrder.status == 1:
                self.openAddOrder(errorOrder)
        # TODO 通知一次持仓

    def loadStrategy(self):
        # 每次启动程序/修改策略,需要重新计算 平仓值
        self.StrategyStatus = False
        for order in self.OrdersDict:
            if order.status < 4:
                self.OrdersDict[order.entrustNo].closeSpread = util.get_caculate_close_spread(order.realOpenSpread,
                                                                                              order.longShort,
                                                                                              self.strategyConfig[
                                                                                                  "rangeSpread"])

        self.StrategyStatus = True

    def updateStrategy(self, data):
        # 先停止交易,更新完成后再执行交易
        self.StrategyStatus = False

        self.cfg.write_strategy(data)

        with self.lock:
            self.strategyConfig = data
        for order in self.OrdersDict:
            if order.status < 4:
                self.OrdersDict[order.entrustNo].closeSpread = util.get_caculate_close_spread(order.realOpenSpread,
                                                                                              order.longShort,
                                                                                              self.strategyConfig[
                                                                                                  "rangeSpread"])

        self.StrategyStatus = True
        log.info("strategy update success: {}".format(self.strategyConfig))

    def runStrategy(self):
        # self.ctpmdApi.runSubMarket()
        # task = Process(target=self.ctpmdApi.run, args=self.queueCtpMD)
        # task.start()

        while True:
            # 先平,再开
            ctpMarket = self.zmqCtpMarket.get_data()
            is_trade_time = util.check_is_trade_time()
            if is_trade_time is False:
                continue
            if self.StrategyStatus:
                b, t = util.check_time_is_valid(ctpMarket.updateTime)
                if b == False:
                    log.info("this ctp market is not valid,current time is {} ,market time is {}".format(t,
                                                                                                         ctpMarket.updateTime))
                    continue
                XAUUSDm = util.mt5_api_get_tick_price_from_symbol(self.XAUUSDm_parent_conn,
                                                                  self.mt5Config1['subMarket'])
                USDCNHm = util.mt5_api_get_tick_price_from_symbol(self.USDCNHm_parent_conn,
                                                                  self.mt5Config2["subMarket"])
                log.info("get market ctp:{} ,mt5-XAUUSDm:{} ,mt5-USDCNHm:{}".format(ctpMarket, XAUUSDm, USDCNHm))
                self.checkShouldCloseOrder(ctpMarket, XAUUSDm, USDCNHm)
                self.checkShouldOpenOrder(ctpMarket, XAUUSDm, USDCNHm)

    def runApi(self):

        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.runStrategy)
            self.thread.start()
        self.loadOrdersFromDB()
        self.addOrder()
        self.loadStrategy()
        self.runStrategy()
        self.strategyStatus = True
        self.thread = threading.Thread(target=self.runStrategy)
        self.thread.start()

    def notify(self):
        # 检查当前持仓和点差
        pass
