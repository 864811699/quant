import datetime
import threading

import concurrent.futures

import logging
import time

from package.logger.logger import setup_logger

log = logging.getLogger('root')

from package.config import config

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
        self.strategyStatus

        self.OrdersDict = {}  # pid->POrder
        self.entrustNo = 0
        # self.ErrorOrderDict = {}  #
        self.ErrorOrders = []  #

        # 用作 重启/更新策略后 重新计算 各种点差
        self.POrderToChildOrdersDict={}  #pid-->symbol-->open_close-->orders  orders[11]=symbol_order  symbol_order[symbol]=childOrders childOrders[open_close]=orders

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

        cfg.read_strategy()
        self.strategyConfig = cfg.getStrategyConfig()
        log.info("strategy config: {}".format(cfg.getStrategyConfig()))

    def init_api(self):
        self.db = db.dbServer(self.dbConfig)
        self.db.create_parent_table()

        self.notifyApi = notify.Notify(self.notifyConfig['url'], self.notifyConfig['successAudio'], self.notifyConfig['failAudio'], self.notifyConfig['mentioned_list'])

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
        self.entrustNo = self.entrustNo+1
        return self.entrustNo

    def save_order(self, order):
        self.OrdersDict[order.entrustNo] = order
        self.db.save_parent_order(self.dbConfig["table"], order)
        return True, ""

    def updateOrder(self, order):
        self.OrdersDict[order.entrustNo] = order
        self.db.update_parent_order(self.dbConfig["table"], order)
        return True, ""

    def create_order(self, longShort, CTPAUAskPrice, CTPAUBidPrice, MT5AUAskPrice, MT5AUBidPrice, USDAskPrice, USDBidPrice, spread,askCtpQty,askMt51Qty,askMt52Qty):
        order = models.POrder()
        order.entrustNo = self.getEntrustNo()
        order.longShort = longShort
        order.CTPAUAskPrice = CTPAUAskPrice
        order.CTPAUBidPrice = CTPAUBidPrice
        order.MT5AUAskPrice = MT5AUAskPrice
        order.MT5AUBidPrice = MT5AUBidPrice
        order.USDAskPrice = USDAskPrice
        order.USDBidPrice = USDBidPrice
        order.spread = spread
        order.status = comm.PARENT_STATUS_OPEN_PENDING
        order.askCtpQty = askCtpQty
        order.askMt51Qty = askMt51Qty
        order.askMt52Qty = askMt52Qty
        success, errmsg = self.save_order(order)
        if success:
            log.info("create order :{}".format(order))
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
            if order.longShort == longShort and order.status > 1 and order.status < 4:
                vol += 1

        return vol

    def openOrder(self, action, spread, ctpMarket_askPrice1, ctpMarket_bidPrice1, XAUUSDm_askPrice1, XAUUSDm_bidPrice1, USDCNHm_askPrice1, USDCNHm_bidPrice1):
        current_positon = self.getPosition(action)
        max_position = self.strategyConfig["base"]["maxVol"]
        openOrderStatus = False
        # 当前持仓值,当前应该应该持有的最大持仓量
        # 开仓失败 或者不需要开仓,则更新数据库
        if current_positon < max_position:

            success, order = self.create_order(action, ctpMarket_askPrice1, ctpMarket_bidPrice1, XAUUSDm_askPrice1, XAUUSDm_bidPrice1, USDCNHm_askPrice1, USDCNHm_bidPrice1, spread, self.strategyConfig["op1"]["rate"], self.strategyConfig["op2"]["rate"], self.strategyConfig["op3"]["rate"])
            if not success:
                self.notifyApi.notify_net_error("save db")
                exit(-1)

            # -1未知  0待开仓/ 1ctp开仓 / 2伦敦金开仓/ 3汇率开仓 /5 待平仓 /6 ctp平仓 /7 伦敦金平仓/ 8 汇率平仓  /10异常
            # 成交结果返回, 需要区分 异常 和 未成交
            success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, self.strategyConfig["op1"]["symbol"], action, comm.OFFSET_OPEN, self.strategyConfig["op1"]["rate"], order.entrustNo)
            self.check_send_status(success, "ctp open order")
            if rtnExecOrder.req_success:
                # 系统撤单等非 系统本身异常的委托,直接废母单,其他未成交的委托返回请求失败,待下一次行情触发
                if rtnExecOrder.order.status !=models.AllTrade:
                    order.status=comm.PARENT_STATUS_OPEN_FAIL
                    order.statusMsg=rtnExecOrder.order.statusMsg
                    self.updateOrder(order)
                    log.warning("ctp open order fail,order:{}".format(rtnExecOrder.order))
                    return False
                order.status = comm.PARENT_STATUS_OPEN_CTP
                log.info("ctp open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op1"]["symbol"], action, self.strategyConfig["op1"]["rate"], rtnExecOrder.order.bidPrice))
                self.updateOrder(order)
                # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op1"]["symbol"], action, comm.OFFSET_OPEN, self.strategyConfig["op1"]["rate"], self.strategyConfig["op1"]["rate"])
                self.notifyApi.notify_trade_success()

                # symbol, magic, longShort, openClose, volume
                # 先执行 伦敦金
                xau_p = 0.0
                usd_p = 0.0
                # mt5 伦敦金成交 订单成交通知
                xauusd_askQty = self.strategyConfig["op2"]["rate"]
                xauusd_bidQty = 0

                mt5Action = util.get_longShort_from_ctp_longShort(action)
                while True:
                    #c,symbol,longShort,openClose,vol,pid
                    success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient, self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_OPEN,xauusd_askQty,order.entrustNo )
                    self.check_send_status(success, "xau open order")
                    if rtnMt5Exec1.req_success:
                        xau_p = rtnMt5Exec1.order.bidPrice
                        if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                            xauusd_askQty -= rtnMt5Exec1.order.bidVol
                            xauusd_bidQty += rtnMt5Exec1.order.bidVol
                            log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op2"]["symbol"], action, xauusd_bidQty, xau_p))
                        if float_equal(xauusd_askQty, 0):
                            order.status = comm.PARENT_STATUS_OPEN_MT5_1
                            self.updateOrder(order)
                            self.notifyApi.notify_trade_success()
                            # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_OPEN, xauusd_askQty, xauusd_bidQty)
                            break

                    else:
                        log.warning("mt5 open fail,spread:{}, {}  {}  {}  {}".format(spread, self.strategyConfig["op2"]["symbol"], mt5Action, xauusd_askQty, rtnMt5Exec1.msg))
                        #  发送成交失败通知
                        self.notifyApi.notify_trade_fail(spread, self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_OPEN, xauusd_askQty, rtnMt5Exec1.msg)
                        self.strategyStatus = False
                        break

                #  mt5 汇率成交  订单成交通知
                usdcnh_askQty = self.strategyConfig["op3"]["rate"]
                usdcnh_bidQty = 0
                while True:
                    success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient, self.strategyConfig["op3"]["symbol"],mt5Action,comm.OFFSET_OPEN, usdcnh_askQty, order.entrustNo,  )
                    self.check_send_status(success, "usd open order")
                    #  mt5 汇率成交  订单成交通知
                    if rtnMt5Exec2.req_success:
                        usd_p = rtnMt5Exec2.order.bidPrice
                        if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                            usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                            usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                            log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op3"]["symbol"], action, usdcnh_bidQty, usd_p))
                        if float_equal(usdcnh_askQty, 0):
                            order.status = comm.PARENT_STATUS_OPEN_MT5_2
                            self.updateOrder(order)
                            self.notifyApi.notify_trade_success()
                            # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op3"]["symbol"], mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, usdcnh_bidQty)
                            break

                    else:
                        log.warning("mt5 open fail,spread:{}, {}  {}  {}  {}".format(spread, self.strategyConfig["op3"]["symbol"], mt5Action, usdcnh_askQty, rtnMt5Exec2.msg))
                        #  发送成交失败通知
                        self.notifyApi.notify_trade_fail(spread, self.strategyConfig["op3"]["symbol"], mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, rtnMt5Exec2.msg)
                        self.strategyStatus = False
                        break

                # 计算实际点差
                realSpread = util.get_caculate_spread_from_price(rtnExecOrder.order.bidPrice, xau_p, usd_p)
                order.realOpenSpread = realSpread
                order.closeSpread = util.get_caculate_close_spread(realSpread, self.strategyConfig["base"]["startSpread"], self.strategyConfig["base"]["rangeSpread"])
                self.updateOrder(order)
                log.info("server open order success,{}".format(order))
                # 汇总通知
                self.notifyApi.send_trade_result(self.strategyConfig["base"]["startSpread"], self.strategyConfig["base"]["rangeSpread"], spread, action, comm.OFFSET_OPEN, current_positon + 1)
                openOrderStatus = True
            else:
                order.status = comm.PARENT_STATUS_OPEN_FAIL
                order.statusMsg=rtnExecOrder.errmsg
                self.updateOrder(order)
                log.info("server open order fail,msg:{}".format(rtnExecOrder.errmsg))
                #symbol, longshort, openclose, vol, msg
                self.notifyApi.notify_trade_fail(spread, self.strategyConfig["op1"]["symbol"], action, comm.OFFSET_OPEN, self.strategyConfig["op1"]["rate"], rtnExecOrder.errmsg)
        else:
            log.info("current_position[{}] >= max_position[{}], not to open order".format(current_positon, max_position))
        return openOrderStatus

    def closeOrder(self, order):
        # 根据order 状态平仓, 3 平全部,4 平mt5,5平外汇
        closeOrderStatus = False
        ctp_p = 0.0
        xau_p = 0.0
        usd_p = 0.0
        if order.status == comm.PARENT_STATUS_OPEN_MT5_2:
            # 4 ctp平仓 /5 伦敦金平仓/ 6汇率平仓
            success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, self.strategyConfig["op1"]["symbol"], order.longShort, comm.OFFSET_CLOSE, self.strategyConfig["op1"]["rate"], order.entrustNo)
            self.check_send_status(success, "ctp close order")
            if rtnExecOrder.req_success:
                order.status = comm.PARENT_STATUS_CLOSE_CTP
                ctp_p = rtnExecOrder.order.bidPrice
                self.updateOrder(order)
                log.info("server close ctp order success,msg:{}".format(rtnExecOrder))
            else:
                log.info("server close ctp order fail,msg::{}".format(rtnExecOrder.errmsg))
                self.notifyApi.notify_trade_fail(order.speard, self.strategyConfig["op1"]["symbol"], order.longShort, comm.OFFSET_CLOSE, self.strategyConfig["op1"]["rate"], rtnExecOrder.errmsg)

        mt5Action = util.get_longShort_from_ctp_longShort(order.longShort)
        if order.status == 4:
            # 平 伦敦金
            xauusd_askQty = self.strategyConfig["op2"]["rate"]
            xauusd_bidQty = 0
            while True:
                success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient, self.strategyConfig["op2"]["symbol"],  mt5Action, comm.OFFSET_CLOSE, self.strategyConfig["op2"]["rate"],order.entrustNo)
                self.check_send_status(success, "xau close order")
                if rtnMt5Exec1.req_success:
                    xau_p = rtnMt5Exec1.order.bidPrice
                    if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                        xauusd_askQty -= rtnMt5Exec1.order.bidVol
                        xauusd_bidQty += rtnMt5Exec1.order.bidVol
                        log.info("mt5 close success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op2"]["symbol"], mt5Action, xauusd_bidQty, xau_p))
                    if float_equal(xauusd_askQty, 0):
                        order.status = 5
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, xauusd_askQty, xauusd_bidQty)
                        break

                else:
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread, self.strategyConfig["op2"]["symbol"], mt5Action, xauusd_askQty, rtnMt5Exec1.msg))
                    #  发送成交失败通知
                    self.notifyApi.notify_trade_fail(order.speard,self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, self.strategyConfig["op2"]["rate"], rtnMt5Exec1.msg)

        if order.status == 5:
            #  mt5 汇率成交  订单成交通知
            usdcnh_askQty = self.strategyConfig["op3"]["rate"]
            usdcnh_bidQty = 0
            while True:
                success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient, self.strategyConfig["op3"]["symbol"], mt5Action, comm.OFFSET_CLOSE, self.strategyConfig["op3"]["rate"], order.entrustNo)
                self.check_send_status(success, "xau close order")
                if rtnMt5Exec2.req_success:
                    usd_p = rtnMt5Exec2.order.bidPrice
                    if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                        usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                        usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                        log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(self.strategyConfig["op3"]["symbol"], mt5Action, usdcnh_bidQty, usd_p))
                    if float_equal(usdcnh_askQty, 0):
                        order.status = 6
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op3"]["symbol"], mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, usdcnh_bidQty)
                        break
                else:
                    #  发送成交失败通知
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread, self.strategyConfig["op3"]["symbol"], mt5Action, usdcnh_askQty, rtnMt5Exec2.msg))
                    self.notifyApi.notify_trade_fail(order.speard,self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, self.strategyConfig["op2"]["rate"], rtnMt5Exec2.msg)

            # 计算实际点差
            order.realCloseSpread = util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
            order.closed_at = datetime.datetime.now()
            self.updateOrder(order)
            # 汇总通知 start, strategy_range, spread,longshort, openClose, position
            self.notifyApi.send_trade_result(self.strategyConfig["base"]["startSpread"], self.strategyConfig["base"]["rangeSpread"], order.spread, order.longShort, comm.OFFSET_CLOSE, self.getPosition(mt5Action))
            closeOrderStatus = True

        return closeOrderStatus

    def closeAllOrders(self):
        # 清仓指令开始时先 关闭策略
        self.strategyStatus = False
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
                    future1 = executor.submit(util.sed_close_all_to_server, self.zmqCtpClient, self.strategyConfig["op1"]["symbol"], entrustNo)
                    future2 = executor.submit(util.sed_close_all_to_server, self.zmqXAUClient, self.strategyConfig["op2"]["symbol"], entrustNo)
                    future3 = executor.submit(util.sed_close_all_to_server, self.zmqUSDClient, self.strategyConfig["op3"]["symbol"], entrustNo)
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
            log.info("should_close_order :{} ,pOrder:{} ,current_spread:{},open_close:{},close_spread:{},start_spread:{},range_spread:{}".format(is_close, order.entrustNo, spread,order.realOpenSpread, order.closeSpread,self.strategyConfig["base"]["startSpread"], self.strategyConfig["base"]["rangeSpread"]))
            if is_close == True:
                success=self.closeOrder(order)
                if success:
                    log.info("close order success,current positions={}".format(self.getPosition(order.longShort)))

    def checkShouldOpenOrder(self, ctpMarket, XAUUSDm, USDCNHm):
        is_open, vol, action, spread = util.should_open_order(ctpMarket, XAUUSDm, USDCNHm, self.strategyConfig["base"]["startSpread"], self.strategyConfig["base"]["rangeSpread"])
        log.info("should_open_order :{} ,current_spread:{},start_spread:{},range_spread:{}".format(is_open, spread, self.strategyConfig["base"]["startSpread"], self.strategyConfig["base"]["rangeSpread"]))
        if is_open:
            success=self.openOrder(action, spread, ctpMarket.askPrice1, ctpMarket.bidPrice1, XAUUSDm.askPrice1, XAUUSDm.bidPrice1, USDCNHm.askPrice1, USDCNHm.bidPrice1)
            if success:
                log.info("open order success,current positions={}".format(self.getPosition(action)))
        else:
            log.debug("this market not to open ,[short_spread,long_spread]==>{}".format(spread))

    def loadOrdersFromDB(self):
        # 导入本地mysql 所有委托,
        pOrders = self.db.load_parent_orders(self.dbConfig["table"])
        for porder in pOrders:
            self.OrdersDict[porder.entrustNo]=porder
            # 获取maxEntrustNo
            if porder.entrustNo > self.entrustNo:
                self.entrustNo = porder.entrustNo

            #  平仓 非终态 5<=status<8  ,校验平常
            #  开仓 非终态 1<status<3   ,校验开仓
            #  状态为 未知,待开仓,已开仓,已平仓,错误 直接跳过
            # 整理数据,检查持仓(清理异常持仓)
            if porder.status == comm.PARENT_STATUS_UNKWON  or porder.status == comm.PARENT_STATUS_CLOSE_MT5_2 or porder.status == comm.PARENT_STATUS_ERROR or porder.status==comm.PARENT_STATUS_OPEN_FAIL:
                continue
            # #pid-->symbol-->open_close-->orders  orders[11]=symbol_order  symbol_order[symbol]=childOrders
            child_orders_dict={}

            # 校验 CTP
            # 设置初始状态, 不补单则代表该子单已完成,状态+1 ,由于状态以 CTP 开平做标准,故CTP状态不做处理
            success,ctp_orders,tmp_status=util.get_porder_openclose_from_ctp(self.zmqCtpClient,porder)
            self.check_send_status(success, "ctp search")
            child_orders_dict[self.strategyConfig["op1"]["symbol"]] = ctp_orders
            if tmp_status==0:
                porder.status=comm.PARENT_STATUS_OPEN_FAIL
                self.updateOrder(porder)
                continue

            # 校验MT5-1
            longshort=util.get_longShort_from_ctp_longShort(porder.longShort)
            success, xau_orders, xau_errors_orders = util.get_err_orders(self.zmqXAUClient, porder, porder.askMt51Qty, self.strategyConfig["op2"]["symbol"], longshort,tmp_status)
            self.check_send_status(success, "xau search")
            child_orders_dict[self.strategyConfig["op2"]["symbol"]] = xau_orders
            self.ErrorOrders.extend(xau_errors_orders)
            if len(xau_errors_orders)==0:
                tmp_status+=1

            # 校验MT5-2
            success, usd_orders, usd_errors_orders = util.get_err_orders(self.zmqUSDClient,porder, porder.askMt52Qty,self.strategyConfig["op3"]["symbol"],longshort,tmp_status)
            self.check_send_status(success, "usd search")
            child_orders_dict[self.strategyConfig["op3"]["symbol"]] = usd_orders
            self.ErrorOrders.extend(usd_errors_orders)
            if len(usd_errors_orders)==0:
                tmp_status+=1

            if porder.status!=tmp_status:
                porder.status = tmp_status
                self.updateOrder(porder)

            self.POrderToChildOrdersDict[porder.entrustNo]=child_orders_dict

    def addOrder(self):
        # 异常委托补单
        for order in self.ErrorOrders:
            success, rtnExecOrder = util.send_order_to_server(order.zmqClient, order.symbol, order.long_short, order.open_close, order.vol, order.entrustNo)
            self.check_send_status(success, f"{order.symbol}  {order.long_short} {order.open_close} order")

            if rtnExecOrder.req_success:
                # 状态累计,故成交成功 状态 +1 即可
                porder=self.OrdersDict[order.entrustNo]
                porder.status+=1
                log.info("parent add order success,{}  {}  {}  vol:{} entrustNo:{}".format(order.symbol, order.long_short,order.open_close, order.vol, order.entrustNo))
                self.updateOrder(porder)
                self.POrderToChildOrdersDict[order.entrustNo][order.symbol].append(rtnExecOrder.order)
                #补仓成功通知
                # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op1"]["symbol"], action, comm.OFFSET_OPEN, self.strategyConfig["op1"]["rate"], self.strategyConfig["op1"]["rate"])  # TODO 通知一次持仓
            else:
                # 补仓失败弹窗,暂停交易
                self.strategyStatus = False
                log.warning(f"add order fail,{order.symbol} {order.long_short} {order.open_close} {order.vol} entrustNo:{order.entrustNo},errmsg:{rtnExecOrder.errmsg}")
                #  发送成交失败通知
                self.notifyApi.notify_add_orders_fail( f"{order.symbol} {order.long_short} {order.open_close} {order.vol} entrustNo:{order.entrustNo},errmsg:{rtnExecOrder.errmsg}")



    def cacluAddOrderSpread(self):
        for pid,childOrders in self.POrderToChildOrdersDict.items():
            pOrder=self.OrdersDict[pid]
            if pOrder.status ==comm.PARENT_STATUS_OPEN_MT5_2:
                # 若开仓,则计算 实际开仓点差和预期平仓点差  开仓:util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)   预期平: order.closeSpread = realOpenSpread - self.strategyConfig["base"]["rangeSpread"]
                ctpOrders = childOrders[self.strategyConfig["op1"]["symbol"]]
                ctp_p=util.get_open_trade_bid_price(ctpOrders)

                xauOrders = childOrders[self.strategyConfig["op2"]["symbol"]]
                xau_p=util.get_open_trade_bid_price(xauOrders)

                usdOrders = childOrders[self.strategyConfig["op3"]["symbol"]]
                usd_p=util.get_open_trade_bid_price(usdOrders)

                realSpread=util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
                pOrder.realOpenSpread=realSpread
                pOrder.closeSpread=util.get_caculate_close_spread(realSpread,self.strategyConfig["base"]["startSpread"],self.strategyConfig["base"]["rangeSpread"])

            elif pOrder.status ==comm.PARENT_STATUS_CLOSE_MT5_2:
                # 若平仓,则计算 实际平仓点差  util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
                ctpOrders = childOrders[self.strategyConfig["op1"]["symbol"]]
                ctp_p=util.get_close_trade_bid_price(ctpOrders)

                xauOrders = childOrders[self.strategyConfig["op2"]["symbol"]]
                xau_p=util.get_close_trade_bid_price(xauOrders)

                usdOrders = childOrders[self.strategyConfig["op3"]["symbol"]]
                usd_p=util.get_close_trade_bid_price(usdOrders)

                pOrder.realCloseSpread=util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)

            self.updateOrder(pOrder)
        log.info("reboot caculate spead success !!! ")


    def updateStrategy(self, data):
        # 先停止交易,更新完成后再执行交易
        self.strategyStatus = False

        self.cfg.write_strategy(data)

        with self.lock:
            self.strategyConfig = data
        for order in self.OrdersDict:
            if order.status < 4 and order.status>0:
                self.OrdersDict[order.entrustNo].closeSpread = util.get_caculate_close_spread(order.realOpenSpread, self.strategyConfig["base"]["startSpread"], self.strategyConfig["base"]["rangeSpread"])

        self.strategyStatus = True
        log.info("strategy update success: {}".format(self.strategyConfig))

    def runStrategy(self):
        # self.ctpmdApi.runSubMarket()
        # task = Process(target=self.ctpmdApi.run, args=self.queueCtpMD)
        # task.start()
        log.debug("!!!!!!!!!!!!! strategy get ctp market start !!!!!!!!!!!!!!")
        while True:
            # 先平,再开

            topic,rspCtpMarket = self.zmqCtpMarket.get_data()
            # log.debug("!!!!!!!!!!!!!!!!  ctp market:{}".format(rspCtpMarket.market))

            is_trade_time = util.check_is_trade_time(self.strategyConfig["base"]["stopDate"],self.strategyConfig["base"]["stopTime"],self.strategyConfig["base"]["stopDateTime"])
            if is_trade_time is False:
                log.debug("!!!!!!!!!!!!!!! check time,stopDate:{} ,stopTime:{} ,stopDateTime:{} not trade ".format(self.strategyConfig["base"]["stopDate"],self.strategyConfig["base"]["stopTime"],self.strategyConfig["base"]["stopDateTime"]))
                continue
            if self.strategyStatus:
                ctpMarket=rspCtpMarket.market
                b, t = util.check_time_is_valid(ctpMarket.updateTime)
                if b == False:
                    log.info("this ctp market is not valid,current time is {} ,market time is {}".format(t, ctpMarket.updateTime))
                    continue
                success,rsp_XAUUSDm = util.mt5_api_get_tick_price_from_symbol(self.zmqXAUClient, self.strategyConfig["op2"]['symbol'])
                self.check_send_status(success and rsp_XAUUSDm.req_success, "mt5 {} qry tick".format(self.strategyConfig["op2"]['symbol']))


                success,rsp_USDCNHm = util.mt5_api_get_tick_price_from_symbol(self.zmqUSDClient, self.strategyConfig["op3"]["symbol"])
                self.check_send_status(success and rsp_USDCNHm.req_success, "mt5 {} qry tick".format(self.strategyConfig["op3"]['symbol']))
                log.info("get market ctp:{} ,mt5-XAUUSDm:{} ,mt5-USDCNHm:{}".format(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market))

                self.checkShouldCloseOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market)
                self.checkShouldOpenOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market)

    def runApi(self):

        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.runStrategy)
            self.thread.start()
        self.loadOrdersFromDB()
        self.addOrder()
        self.cacluAddOrderSpread()
        self.strategyStatus = True


    def notify(self):
        # 检查当前持仓和点差
        pass
