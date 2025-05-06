import datetime
import threading

import concurrent.futures

import logging


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
        self.long_thread = None
        self.short_thread = None
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
        # self.ErrorOrderDict = {}  #
        self.ErrorOrders = []  #

        # 用作 重启/更新策略后 重新计算 各种点差
        self.POrderToChildOrdersDict = {}  #pid-->symbol-->open_close-->orders  orders[11]=symbol_order  symbol_order[symbol]=childOrders childOrders[open_close]=orders

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

        self.zmqCtpClient = client.ZmqClient(self.zmqConfig['ctpReqAddr'],self.zmqConfig['timeout'])
        self.zmqCtpMarket = subscriber.ZmqSubscriber(self.zmqConfig['ctpSubAddr'], self.zmqConfig['topic'])
        self.zmqUSDClient = client.ZmqClient(self.zmqConfig['mt5USDCNHReqAddr'],self.zmqConfig['timeout'])
        self.zmqXAUClient = client.ZmqClient(self.zmqConfig['mt5XAUUSDReqAddr'],self.zmqConfig['timeout'])
        log.info("server init api success!!!")

    def check_send_status(self, success,rsp, msg):
        # zmq 发送失败关闭程序
        if not success:
            log.error("zmq send fail,msg:{} ,{}".format(rsp,msg))
            self.notifyApi.notify_net_error(msg)
            exit(-1)

    def getEntrustNo(self):
        self.entrustNo += 1
        return self.entrustNo


    def get_longshort_strategy(self,longshort):
        with self.lock:
            return {
                "base": self.strategyConfig[longshort]["base"],
                "op1": self.strategyConfig[longshort]["op1"],
                "op2": self.strategyConfig[longshort]["op2"],
                "op3": self.strategyConfig[longshort]["op3"]
            }


    def update_base_strategy(self,data):
        long_short=data['long_short']
        start_spread=data['start_spread']
        range_spread=data['range_spread']
        close_spread=data['close_spread']
        max_vol=data['max_vol']

        self.strategyStatus = False
        with self.lock:
            self.strategyConfig[long_short]['base']['startSpread']=start_spread
            self.strategyConfig[long_short]['base']['rangeSpread']=range_spread
            self.strategyConfig[long_short]['base']['closeSpread']=close_spread
            self.strategyConfig[long_short]['base']['maxVol']=max_vol

            self.cfg.write_strategy(self.strategyConfig)

        for order  in self.OrdersDict.values():
            if order.status < 4 and order.status>0:
                self.OrdersDict[order.entrustNo].closeSpread = util.get_caculate_close_spread(order.realOpenSpread, self.strategyConfig[long_short]["base"]["startSpread"], self.strategyConfig[long_short]["base"]["rangeSpread"])

        self.strategyStatus = True
        log.info("strategy update success: {}".format(self.strategyConfig))

    def update_core_strategy(self,data):
        long_short=data['long_short']
        ctp_vol=data['op1_vol']
        xau_vol=data['op2_vol']
        usd_vol=data['op3_vol']

        self.strategyStatus = False
        with self.lock:
            self.strategyConfig[long_short]['op1']['rate']=ctp_vol
            self.strategyConfig[long_short]['op2']['rate']=xau_vol
            self.strategyConfig[long_short]['op3']['rate']=usd_vol

            self.cfg.write_strategy(self.strategyConfig)

        self.strategyStatus = True
        log.info("strategy update success: {}".format(self.strategyConfig))


    def update_time_strategy(self,data):
        long_short=data['long_short']
        date_start=data['date_start']
        date_stop=data['date_stop']
        datetime_start=data['datetime_start']
        datetime_stop=data['datetime_stop']

        self.strategyStatus = False
        with self.lock:
            self.strategyConfig[long_short]['base']['stopDate']=[date_start,date_stop]
            self.strategyConfig[long_short]['base']['stopDateTime']=[datetime_start,datetime_stop]

            self.cfg.write_strategy(self.strategyConfig)

        self.strategyStatus = True
        log.info("strategy update success: {}".format(self.strategyConfig))

    def stop_strategy(self,data):
        long_short=data['long_short']

        self.strategyStatus = False
        with self.lock:
            self.strategyConfig[long_short]['base']['isRun'] =False
            self.cfg.write_strategy(self.strategyConfig)
        self.strategyStatus = True
        log.info("strategy stop: {}".format(self.strategyConfig))
        return True

    def start_strategy(self,data):
        long_short=data['long_short']

        self.strategyStatus = False
        with self.lock:
            self.strategyConfig[long_short]['base']['isRun'] =True
            self.cfg.write_strategy(self.strategyConfig)
        self.strategyStatus = True
        log.info("strategy stop: {}".format(self.strategyConfig))
        return True


    def save_order(self, order):
        self.OrdersDict[order.entrustNo] = order
        self.db.save_parent_order(self.dbConfig["table"], order)
        return True, ""

    def updateOrder(self, order):
        self.OrdersDict[order.entrustNo] = order
        self.db.update_parent_order(self.dbConfig["table"], order)
        return True, ""

    def create_order(self, longShort, CTPAUAskPrice, CTPAUBidPrice, MT5AUAskPrice, MT5AUBidPrice, USDAskPrice, USDBidPrice, spread, askCtpQty, askMt51Qty, askMt52Qty):
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
            log.warning("save order fail,pEntrustNo:{}  error:{}".format(order.entrustNo, errmsg))
            return False, None

    def getNoFinishOrders(self,longshort):
        orders = []
        for order in self.OrdersDict.values():
            if order.status < 6 and order.status > 0 and order.longShort== longshort:
                orders.append(order)
        return orders

    def getPosition(self, longShort):
        vol = 0
        for order in self.OrdersDict.values():
            if order.longShort == longShort and order.status > 1 and order.status < 4:
                vol += 1

        return vol

    def openOrder(self, action, spread, ctpMarket_askPrice1, ctpMarket_bidPrice1, XAUUSDm_askPrice1, XAUUSDm_bidPrice1, USDCNHm_askPrice1, USDCNHm_bidPrice1,strategy):
        openOrderStatus = False

        # 开仓失败 或者不需要开仓,则更新数据库
        success, order = self.create_order(action, ctpMarket_askPrice1, ctpMarket_bidPrice1, XAUUSDm_askPrice1, XAUUSDm_bidPrice1, USDCNHm_askPrice1, USDCNHm_bidPrice1, spread, strategy["op1"]["rate"], strategy["op2"]["rate"], strategy["op3"]["rate"])
        if not success:
            self.notifyApi.notify_net_error("save db")
            exit(-1)

        # -1未知  0待开仓/ 1ctp开仓 / 2伦敦金开仓/ 3汇率开仓 /5 待平仓 /6 ctp平仓 /7 伦敦金平仓/ 8 汇率平仓  /10异常
        # 成交结果返回, 需要区分 异常 和 未成交
        success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, strategy["op1"]["symbol"], action, comm.OFFSET_OPEN, strategy["op1"]["rate"], order.entrustNo)
        self.check_send_status(success, rtnExecOrder," ctp open order {}".format(action))
        if rtnExecOrder.req_success:
            # 系统撤单等非 系统本身异常的委托,直接废母单,其他未成交的委托返回请求失败,待下一次行情触发
            if rtnExecOrder.order.status != models.AllTrade:
                order.status=comm.PARENT_STATUS_OPEN_FAIL
                order.statusMsg=rtnExecOrder.order.statusMsg
                self.updateOrder(order)
                log.warning("ctp open order {} fail,order:{}".format(action,rtnExecOrder.order))
                return False
            order.status = comm.PARENT_STATUS_OPEN_CTP
            log.info("ctp open success,{}  {}  vol:{} ,price:{}".format(strategy["op1"]["symbol"], action, strategy["op1"]["rate"], rtnExecOrder.order.bidPrice))
            self.updateOrder(order)
            # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op1"]["symbol"], action, comm.OFFSET_OPEN, self.strategyConfig["op1"]["rate"], self.strategyConfig["op1"]["rate"])
            self.notifyApi.notify_trade_success()

            # symbol, magic, longShort, openClose, volume
            # 先执行 伦敦金
            xau_p = 0.0
            usd_p = 0.0
            # mt5 伦敦金成交 订单成交通知
            xauusd_askQty = strategy["op2"]["rate"]
            xauusd_bidQty = 0

            mt5Action = util.get_longShort_from_ctp_longShort(action)
            while True:
                #c,symbol,longShort,openClose,vol,pid
                success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient, strategy["op2"]["symbol"], mt5Action, comm.OFFSET_OPEN,xauusd_askQty,order.entrustNo )
                self.check_send_status(success, rtnExecOrder , "xau open order {}".format(action))
                if rtnMt5Exec1.req_success:
                    xau_p = rtnMt5Exec1.order.bidPrice
                    if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                        xauusd_askQty -= rtnMt5Exec1.order.bidVol
                        xauusd_bidQty += rtnMt5Exec1.order.bidVol
                        log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(strategy["op2"]["symbol"], action, xauusd_bidQty, xau_p))
                    if float_equal(xauusd_askQty, 0):
                        order.status = comm.PARENT_STATUS_OPEN_MT5_1
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_OPEN, xauusd_askQty, xauusd_bidQty)
                        break

                else:
                    log.warning("mt5 open fail,spread:{}, {}  {}  {}  {}".format(spread, strategy["op2"]["symbol"], mt5Action, xauusd_askQty, rtnMt5Exec1.errmsg))
                    #  发送成交失败通知
                    self.notifyApi.notify_trade_fail(spread, strategy["op2"]["symbol"], mt5Action, comm.OFFSET_OPEN, xauusd_askQty, rtnMt5Exec1.errmsg)
                    self.strategyStatus = False
                    break

            #  mt5 汇率成交  订单成交通知
            usdcnh_askQty = strategy["op3"]["rate"]
            usdcnh_bidQty = 0
            while True:
                success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient, strategy["op3"]["symbol"],mt5Action,comm.OFFSET_OPEN, usdcnh_askQty, order.entrustNo)
                self.check_send_status(success, rtnExecOrder ,"usd open order")
                #  mt5 汇率成交  订单成交通知
                if rtnMt5Exec2.req_success:
                    usd_p = rtnMt5Exec2.order.bidPrice
                    if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                        usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                        usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                        log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(strategy["op3"]["symbol"], action, usdcnh_bidQty, usd_p))
                    if float_equal(usdcnh_askQty, 0):
                        order.status = comm.PARENT_STATUS_OPEN_MT5_2
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op3"]["symbol"], mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, usdcnh_bidQty)
                        break

                else:
                    log.warning("mt5 open fail,spread:{}, {}  {}  {}  {}".format(spread, strategy["op3"]["symbol"], mt5Action, usdcnh_askQty, rtnMt5Exec2.errmsg))
                    #  发送成交失败通知
                    self.notifyApi.notify_trade_fail(spread, strategy["op3"]["symbol"], mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, rtnMt5Exec2.errmsg)
                    self.strategyStatus = False
                    break

            # 计算实际点差
            realSpread = util.get_caculate_spread_from_price(rtnExecOrder.order.bidPrice, xau_p, usd_p)
            order.realOpenSpread = realSpread
            order.closeSpread = util.get_caculate_close_spread(realSpread, strategy["base"]["startSpread"], strategy["base"]["closeSpread"])
            self.updateOrder(order)
            log.info("server open order success,{}".format(order))

            openOrderStatus = True
        else:
            order.status = comm.PARENT_STATUS_OPEN_FAIL
            order.statusMsg=rtnExecOrder.errmsg
            self.updateOrder(order)
            log.info("server open order fail,msg:{}".format(rtnExecOrder.errmsg))
            #symbol, longshort, openclose, vol, msg
            self.notifyApi.notify_trade_fail(spread, strategy["op1"]["symbol"], action, comm.OFFSET_OPEN, strategy["op1"]["rate"], rtnExecOrder.errmsg)


        return openOrderStatus

    def closeOrder(self, order,strategy):
        # 根据order 状态平仓, 3 平全部,4 平mt5,5平外汇
        closeOrderStatus = False
        ctp_p = 0.0
        xau_p = 0.0
        usd_p = 0.0
        if order.status == comm.PARENT_STATUS_OPEN_MT5_2:
            # 4 ctp平仓 /5 伦敦金平仓/ 6汇率平仓
            success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, strategy["op1"]["symbol"], order.longShort, comm.OFFSET_CLOSE, strategy["op1"]["rate"], order.entrustNo)
            self.check_send_status(success, rtnExecOrder , "  ctp close order")
            if rtnExecOrder.req_success:
                if rtnExecOrder.order.status==models.AllTrade:
                    order.status = comm.PARENT_STATUS_CLOSE_CTP
                    ctp_p = rtnExecOrder.order.bidPrice
                    self.updateOrder(order)
                    log.info("server close ctp order success,msg:{}".format(rtnExecOrder))
                else:
                    log.info("server close ctp fail,msg:{}".format(rtnExecOrder))
                    return
            else:
                log.info("server req close ctp order fail,msg::{}".format(rtnExecOrder.errmsg))
                self.notifyApi.notify_trade_fail(order.speard, strategy["op1"]["symbol"], order.longShort, comm.OFFSET_CLOSE, strategy["op1"]["rate"], rtnExecOrder.errmsg)

        mt5Action = util.get_longShort_from_ctp_longShort(order.longShort)
        if order.status == comm.PARENT_STATUS_CLOSE_CTP:
            # 平 伦敦金
            xauusd_askQty = strategy["op2"]["rate"]
            xauusd_bidQty = 0
            while True:
                success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient, strategy["op2"]["symbol"],  mt5Action, comm.OFFSET_CLOSE, strategy["op2"]["rate"],order.entrustNo)
                self.check_send_status(success, rtnExecOrder , "  xau close order")
                if rtnMt5Exec1.req_success:
                    xau_p = rtnMt5Exec1.order.bidPrice
                    if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                        xauusd_askQty -= rtnMt5Exec1.order.bidVol
                        xauusd_bidQty += rtnMt5Exec1.order.bidVol
                        log.info("mt5 close success,{}  {}  vol:{} ,price:{}".format(strategy["op2"]["symbol"], mt5Action, xauusd_bidQty, xau_p))
                    if float_equal(xauusd_askQty, 0):
                        order.status = comm.PARENT_STATUS_CLOSE_MT5_1
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, xauusd_askQty, xauusd_bidQty)
                        break

                else:
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread, strategy["op2"]["symbol"], mt5Action, xauusd_askQty, rtnMt5Exec1.errmsg))
                    #  发送成交失败通知
                    self.notifyApi.notify_trade_fail(order.speard,strategy["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, strategy["op2"]["rate"], rtnMt5Exec1.errmsg)

        if order.status == comm.PARENT_STATUS_CLOSE_MT5_1:
            #  mt5 汇率成交  订单成交通知
            usdcnh_askQty = strategy["op3"]["rate"]
            usdcnh_bidQty = 0
            while True:
                success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient, strategy["op3"]["symbol"], mt5Action, comm.OFFSET_CLOSE, strategy["op3"]["rate"], order.entrustNo)
                self.check_send_status(success, rtnExecOrder , "  xau close order")
                if rtnMt5Exec2.req_success:
                    usd_p = rtnMt5Exec2.order.bidPrice
                    if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                        usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                        usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                        log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(strategy["op3"]["symbol"], mt5Action, usdcnh_bidQty, usd_p))
                    if float_equal(usdcnh_askQty, 0):
                        order.status =comm.PARENT_STATUS_CLOSE_MT5_2
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op3"]["symbol"], mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, usdcnh_bidQty)
                        break
                else:
                    #  发送成交失败通知
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread, strategy["op3"]["symbol"], mt5Action, usdcnh_askQty, rtnMt5Exec2.errmsg))
                    self.notifyApi.notify_trade_fail(order.speard,strategy["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, strategy["op2"]["rate"], rtnMt5Exec2.errmsg)

            # 计算实际点差
            order.realCloseSpread = util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
            order.closed_at = datetime.datetime.now()
            self.updateOrder(order)
            closeOrderStatus = True

        return closeOrderStatus

    def close_all_positions(self,longshort):
        #TODO 清仓指令开始时先 关闭策略 按策略清
        self.strategyStatus = False
        with self.lock:
            self.strategyConfig[longshort]['base']['isRun']=False
        closeAllOrdersStatus = False
        while True:
            orders = self.getNoFinishOrders(longshort)
            if len(orders) == 0:
                closeAllOrdersStatus=True
                break
            else:
                log.info("server need to close orders vol: {}".format(len(orders)))
                # 清仓 ctp
                for order in orders:
                    strategy=self.get_longshort_strategy(order.longShort)
                    self.closeOrder(order,strategy)
        log.info("server closed all orders success !!!!!!!!!!!!!!!")

    def check_position_limit(self,strategy,vol):
        longshort=strategy["base"]["longShort"]
        current_positon = self.getPosition(longshort)
        #检查总的最大值
        if current_positon >= strategy["base"]["maxVol"]:
            return False ,current_positon,strategy["base"]["maxVol"]
        # 检查挡位最大值
        if current_positon >= strategy["op1"]["rate"]*vol:
            return False,current_positon,strategy["op1"]["rate"]*vol
        return True,current_positon,strategy["base"]["maxVol"]


    def checkShouldCloseOrder(self, ctpMarket, XAUUSDm, USDCNHm,strategy):
        longShort=strategy["base"]["longShort"]
        orders = self.getNoFinishOrders(longShort)
        start_spread=strategy["base"]["startSpread"]
        range_spread=strategy["base"]["rangeSpread"]
        long_spread,short_spread=util.get_caculate_long_short_spread(ctpMarket, XAUUSDm, USDCNHm)
        log_msg=f"check {longShort} order should to be closed,current_long_spread:{long_spread:.2f}, current_short_spread:{short_spread:.2f}, start_spread:{start_spread:.0f}, range_spread:{range_spread:.0f}"

        need_to_close_orders=[]
        for order in orders:
            if order.status==comm.PARENT_STATUS_OPEN_MT5_2:
                is_close, spread = util.should_close_order(ctpMarket, XAUUSDm, USDCNHm, order)
                log_msg+=f"\n\t\t\t\tentrustNo:{order.entrustNo}, need_to_close:{is_close} ,open_spread:{order.realOpenSpread:.2f},close_spread:{order.closeSpread:.2f}"
                if is_close == True:
                    need_to_close_orders.append(order)
        log.info(log_msg)

        for order in need_to_close_orders:
            success = self.closeOrder(order,strategy)
            if success:
                current_position = self.getPosition(order.longShort)
                log.info("close order success,{}  current positions={}".format(longShort,current_position))
                # 汇总通知 start, strategy_range, spread,longshort, openClose, position
                self.notifyApi.send_trade_result(strategy["base"]["startSpread"], strategy["base"]["rangeSpread"], order.spread, order.longShort, comm.OFFSET_CLOSE, current_position)

    def checkShouldOpenOrder(self, ctpMarket, XAUUSDm, USDCNHm,strategy):
        longshort=strategy["base"]["longShort"]
        is_open, vol,  spread = util.should_open_order_longshort(ctpMarket, XAUUSDm, USDCNHm, strategy["base"]["startSpread"], strategy["base"]["rangeSpread"],longshort)
        # is_open 为False,spread 为[空点差,多点差], True 为点差
        if  not is_open:
            log.info("this market not to open {},[short_spread,long_spread]==>{}".format(longshort,spread))
            return

        log.info("this market could to open order {}:{} ,current_spread:{:.2f},start_spread:{:.2f},range_spread:{:.2f}".format(longshort,is_open, spread, strategy["base"]["startSpread"], strategy["base"]["rangeSpread"]))
        is_open,current_position,max_position=self.check_position_limit(strategy,vol*strategy["op1"]["rate"])
        # 区间最大手数=区间倍数*区间手数
        if not is_open:
            log.info("not should to open {},current_position[{}] >= max_position[{}], not to open order".format(longshort,current_position, max_position))
            return

        if is_open:
            success=self.openOrder(longshort, spread, ctpMarket.askPrice1, ctpMarket.bidPrice1, XAUUSDm.askPrice1, XAUUSDm.bidPrice1, USDCNHm.askPrice1, USDCNHm.bidPrice1,strategy)
            if success:
                current_position=self.getPosition(longshort)
                log.info("open order {} success,current positions={}".format(longshort,current_position))
                self.notifyApi.send_trade_result(strategy["base"]["startSpread"], strategy["base"]["rangeSpread"], spread, longshort, comm.OFFSET_OPEN,current_position)


    def loadOrdersFromDB(self):
        # 导入本地mysql 所有委托,
        # 查询ctp/mt5 委托

        # 获取entrustNo
        # 整理数据,检查持仓(清理异常持仓)
        # maxEntrustNo
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
            self.check_send_status(success, ctp_orders,"ctp search")
            child_orders_dict[self.strategyConfig["LONG"]["op1"]["symbol"]] = ctp_orders
            if tmp_status==comm.PARENT_STATUS_OPEN_FAIL:
                porder.status=comm.PARENT_STATUS_OPEN_FAIL
                self.updateOrder(porder)
                continue

            # 校验MT5-1
            longshort=util.get_longShort_from_ctp_longShort(porder.longShort)
            success, xau_orders, xau_errors_orders = util.get_err_orders(self.zmqXAUClient, porder, porder.askMt51Qty, self.strategyConfig["LONG"]["op2"]["symbol"], longshort,tmp_status)
            self.check_send_status(success,xau_orders, "xau search")
            child_orders_dict[self.strategyConfig["LONG"]["op2"]["symbol"]] = xau_orders
            self.ErrorOrders.extend(xau_errors_orders)
            if len(xau_errors_orders)==0:
                tmp_status+=1

            # 校验MT5-2
            success, usd_orders, usd_errors_orders = util.get_err_orders(self.zmqUSDClient,porder, porder.askMt52Qty,self.strategyConfig["LONG"]["op3"]["symbol"],longshort,tmp_status)
            self.check_send_status(success,usd_orders, "usd search")
            child_orders_dict[self.strategyConfig["LONG"]["op3"]["symbol"]] = usd_orders
            self.ErrorOrders.extend(usd_errors_orders)
            if len(usd_errors_orders)==0:
                tmp_status+=1

            if porder.status!=tmp_status:
                porder.status = tmp_status
                self.updateOrder(porder)

            self.POrderToChildOrdersDict[porder.entrustNo]=child_orders_dict
        log.info("load order success, err order:{}".format(self.ErrorOrders))

    def addOrder(self):
        # 异常委托补单
        for order in self.ErrorOrders:
            success, rtnExecOrder = util.send_order_to_server(order.zmqClient, order.symbol, order.long_short, order.open_close, order.vol, order.entrustNo)
            self.check_send_status(success, rtnExecOrder,f"{order.symbol}  {order.long_short} {order.open_close} order")

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
                ctpOrders = childOrders[self.strategyConfig["LONG"]["op1"]["symbol"]]
                ctp_p=util.get_open_trade_bid_price(ctpOrders)
                longshort=ctpOrders[0].longShort


                xauOrders = childOrders[self.strategyConfig["LONG"]["op2"]["symbol"]]
                xau_p=util.get_open_trade_bid_price(xauOrders)

                usdOrders = childOrders[self.strategyConfig["LONG"]["op3"]["symbol"]]
                usd_p=util.get_open_trade_bid_price(usdOrders)

                realSpread=util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
                pOrder.realOpenSpread=realSpread
                if longshort ==comm.ACTION_LONG:
                    pOrder.closeSpread=util.get_caculate_close_spread(realSpread,self.strategyConfig["LONG"]["base"]["startSpread"],self.strategyConfig["LONG"]["base"]["closeSpread"])
                else:
                    pOrder.closeSpread = util.get_caculate_close_spread(realSpread, self.strategyConfig["SHORT"]["base"]["startSpread"], self.strategyConfig["SHORT"]["base"]["closeSpread"])

            elif pOrder.status ==comm.PARENT_STATUS_CLOSE_MT5_2:
                # 若平仓,则计算 实际平仓点差  util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
                ctpOrders = childOrders[self.strategyConfig["LONG"]["op1"]["symbol"]]
                ctp_p=util.get_close_trade_bid_price(ctpOrders)

                xauOrders = childOrders[self.strategyConfig["LONG"]["op2"]["symbol"]]
                xau_p=util.get_close_trade_bid_price(xauOrders)

                usdOrders = childOrders[self.strategyConfig["LONG"]["op3"]["symbol"]]
                usd_p=util.get_close_trade_bid_price(usdOrders)

                pOrder.realCloseSpread=util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)

            self.updateOrder(pOrder)
        log.info("reboot caculate spead success !!! ")

    def runStrategy(self):
        # self.ctpmdApi.runSubMarket()
        # task = Process(target=self.ctpmdApi.run, args=self.queueCtpMD)
        # task.start()
        log.debug("!!!!!!!!!!!!! strategy get ctp market start !!!!!!!!!!!!!!")
        while True:
            # 先平,再开

            topic,rspCtpMarket = self.zmqCtpMarket.get_data()
            # log.debug("!!!!!!!!!!!!!!!!  ctp market:{}".format(rspCtpMarket.market))
            if self.strategyStatus:
                ctpMarket=rspCtpMarket.market
                b, t = util.check_time_is_valid(ctpMarket.updateTime)
                if b == False:
                    log.info("this ctp market is not valid,current time is {} ,market time is {}".format(t, ctpMarket.updateTime))
                    continue
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future1 = executor.submit(util.mt5_api_get_tick_price_from_symbol,self.zmqXAUClient, self.strategyConfig["LONG"]["op2"]['symbol'])
                    future2 = executor.submit(util.mt5_api_get_tick_price_from_symbol,self.zmqUSDClient, self.strategyConfig["LONG"]["op3"]["symbol"])
                    success,rsp_XAUUSDm = future1.result()
                    success,rsp_USDCNHm = future2.result()
                # success,rsp_XAUUSDm = util.mt5_api_get_tick_price_from_symbol(self.zmqXAUClient, self.strategyConfig["op2"]['symbol'])
                self.check_send_status(success and rsp_XAUUSDm.req_success,rsp_XAUUSDm, "mt5 {} qry tick".format(self.strategyConfig["LONG"]["op2"]['symbol']))

                # success,rsp_USDCNHm = util.mt5_api_get_tick_price_from_symbol(self.zmqUSDClient, self.strategyConfig["op3"]["symbol"])
                self.check_send_status(success and rsp_USDCNHm.req_success, rsp_USDCNHm,"mt5 {} qry tick".format(self.strategyConfig["LONG"]["op3"]['symbol']))
                log.info("get market ctp:{} ,mt5-XAUUSDm:{} ,mt5-USDCNHm:{}".format(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market))

                strategy_long = self.get_longshort_strategy(comm.ACTION_LONG)
                strategy_short = self.get_longshort_strategy(comm.ACTION_SHORT)

                log.info("strategy LONG is {}".format(strategy_long["base"]["isRun"]))
                if strategy_long["base"]["isRun"]:
                    is_trade_time = util.check_is_trade_time(strategy_long["base"]["stopDate"], strategy_long["base"]["stopDateTime"])
                    if is_trade_time is False:
                        log.debug("!!!!!!!!!!!!!!! check time,stopDate:{} ,stopTime:{} ,stopDateTime:{} not trade ".format(strategy_long["base"]["stopDate"], strategy_long["base"]["stopTime"], strategy_long["base"]["stopDateTime"]))
                        continue

                    self.checkShouldCloseOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market ,strategy_long)
                    self.checkShouldOpenOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market,strategy_long)

                log.info("strategy SHORT is {}".format(strategy_short["base"]["isRun"]))
                if strategy_short["base"]["isRun"]:

                    is_trade_time = util.check_is_trade_time(strategy_short["base"]["stopDate"], strategy_short["base"]["stopDateTime"])
                    if is_trade_time is False:
                        log.debug("!!!!!!!!!!!!!!! check time,stopDate:{} ,stopDateTime:{} not trade ".format(strategy_short["base"]["stopDate"], strategy_short["base"]["stopDateTime"]))
                        continue

                    self.checkShouldCloseOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market,strategy_short)
                    self.checkShouldOpenOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market,strategy_short)



    def runApi(self):

        if not self.long_thread or not self.long_thread.is_alive():
            self.long_thread = threading.Thread(target=self.runStrategy)
            self.long_thread.start()
        self.loadOrdersFromDB()
        self.addOrder()
        self.cacluAddOrderSpread()
        self.strategyStatus = True


    def notify(self):
        # 检查当前持仓和点差
        pass
