import copy
import datetime
import threading

import concurrent.futures
import queue
import logging
import time
from collections import defaultdict
import uuid

from package.logger.logger import setup_logger

log = logging.getLogger('root')

from package.config import config

from src.core import comm
from package.db import db
from package.notify import notify
from src.core import risk
from src.core import util
from src.core.util import float_equal
from package.zmq import client
from package.zmq import subscriber
from package.zmq import models
from package.zmq import zmq_proxy
from src.core.trade_limiter import TradeThrottler,OrderMaxLimit


class Server:
    def __init__(self, baseConfigFile, strategyConfigFile,riskConffigFile):
        self.baseConfigFile = baseConfigFile
        self.strategyConfigFile = strategyConfigFile
        self.riskConffigFile = riskConffigFile
        self.webConfig = None
        self.notifyConfig = None
        self.zmqConfig = None
        self.dbConfig = None
        self.risk=None
        self.long_thread = None
        self.monitor_thread = None
        self.notifyApi = None
        self.zmqCtpClient = None
        self.zmqCtpMarket = None
        self.zmqUSDClient = None
        self.zmqXAUClient = None
        self.zmqAccountServer = None
        self.zmqAccountProxy = None
        self.total_cost = 0.0
        self.usd_rate =0.0
        self.market_websocket_queue=queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.strategyConfig = None
        self.cfg = None
        self.loadCfg()
        # self.queueCtpMD = queue.Queue()
        self.strategyStatus = False
        self.strategy_allow_open = True

        self.order_max_limit = OrderMaxLimit(self.strategyConfig["BASE"]["limit_ctp_strategy_order_numbers"])
        # 每2秒只允许开平仓成功一次
        self.trade_limit={comm.ACTION_LONG:{comm.OFFSET_OPEN:TradeThrottler(interval=self.strategyConfig["BASE"]["limit_open_close_time"]),comm.OFFSET_CLOSE:TradeThrottler(interval=self.strategyConfig["BASE"]["limit_open_close_time"])},comm.ACTION_SHORT:{comm.OFFSET_OPEN:TradeThrottler(interval=self.strategyConfig["BASE"]["limit_open_close_time"]),comm.OFFSET_CLOSE:TradeThrottler(interval=self.strategyConfig["BASE"]["limit_open_close_time"])}}

        self.order_lock=threading.Lock()
        self.OrdersDict = {}  # pid->POrder
        self.entrustNo = 0
        # self.ErrorOrderDict = {}  #
        self.ErrorOrders = []  #

        self.child_order_lock=threading.Lock()
        self.child_order_dict=defaultdict(lambda: defaultdict(lambda: defaultdict(models.Order))) # entrustNo-->openclose->symbol-->child_order
        # 用作 重启/更新策略后 重新计算 各种点差
        self.POrderToChildOrdersDict = {}  #pid-->symbol-->open_close-->orders  orders[11]=symbol_order  symbol_order[symbol]=childOrders childOrders[open_close]=orders

        self.AccountInfos_lock=threading.Lock()
        self.AccountInfos_dict={}
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
        self.strategyConfig[comm.ACTION_LONG]["base"]["isRun"]=False
        self.strategyConfig[comm.ACTION_LONG]["base"]["closeByStrategy"]=False
        self.strategyConfig[comm.ACTION_SHORT]["base"]["isRun"]=False
        self.strategyConfig[comm.ACTION_SHORT]["base"]["closeByStrategy"]=False
        self.total_cost = self.strategyConfig["BASE"]["total_cost"]
        self.usd_rate = self.strategyConfig["BASE"]["usd_rate"]
        log.info("strategy config: {}".format(cfg.getStrategyConfig()))

    def init_api(self):
        self.db = db.dbServer(self.dbConfig)
        if self.db.engine is None:
            log.error("数据库引擎初始化失败，终止程序")
            self.notifyApi.notify_start_exe_fail("数据库链接异常,请检查")
            exit(1)
        self.db.create_parent_table()
        self.db.create_account_table()

        self.notifyApi = notify.Notify(self.notifyConfig['url'], self.notifyConfig['successAudio'], self.notifyConfig['failAudio'], self.notifyConfig['mentioned_list'])

        self.zmqCtpClient = client.ZmqClient(self.zmqConfig['ctpReqAddr'],self.zmqConfig['timeout'])
        self.zmqCtpMarket = subscriber.ZmqSubscriber(self.zmqConfig['ctpSubAddr'], self.zmqConfig['topic'],True)
        self.zmqUSDClient = client.ZmqClient(self.zmqConfig['mt5USDCNHReqAddr'],self.zmqConfig['timeout'])
        self.zmqXAUClient = client.ZmqClient(self.zmqConfig['mt5XAUUSDReqAddr'],self.zmqConfig['timeout'])
        self.zmqAccountProxy = zmq_proxy.ZMQ_Proxy(self.zmqConfig['accountServerAdrrProxy'],self.zmqConfig['accountServerAdrr'])
        self.zmqAccountServer=subscriber.ZmqSubscriber(self.zmqConfig['accountServerAdrr'],self.zmqConfig['accountTopic'],False)
        self.risk = risk.Risk(self.riskConffigFile)
        log.info("server init api success!!!")

    def store_child_order(self, order):
        with self.child_order_lock:
            self.child_order_dict[order.pEntrustNo][order.openClose][order.symbol]=order
    def get_child_order(self,pEntrustNo,openclose,symbol):
        with self.child_order_lock:
            return self.child_order_dict[pEntrustNo][openclose][symbol]
    def move_child_order_to_new_parent(self,old_pid,new_pid,symbols):
        with self.child_order_lock:
            for openClose in self.child_order_dict[old_pid].keys():
                for symbol in self.child_order_dict[old_pid][openClose].keys():
                    if symbol in symbols:
                        old_child_order=self.child_order_dict[old_pid][openClose][symbol]
                        old_child_order.pEntrustNo=new_pid
                        self.child_order_dict[new_pid][openClose][symbol]=old_child_order
            del self.child_order_dict[old_pid]
    def check_send_status(self, success,rsp, msg):
        # zmq 发送失败关闭程序
        if not success:
            time.sleep(1)
            log.error("zmq send fail,msg:{} ,{}".format(rsp,msg))
            self.notifyApi.notify_net_error(msg)
            exit(-1)
    def check_search_status(self,success,rsp, msg):
        if not success:
            time.sleep(1)
            log.error("zmq send search fail,msg:{} ,{}".format(rsp,msg))
            self.notifyApi.notify_search_order_net_error(msg)

    def trade_zmq_req_timeout(self,success,rsp,msg):
        if not success:
            log.error("trade zmq send fail,msg:{} ,{}".format(rsp,msg))
            self.notifyApi.notify_net_error(msg)
    def getEntrustNo(self):
        self.entrustNo += 1
        return self.entrustNo
    def get_risk_config(self):
        riskConfig=self.risk.get_risk()
        risk_config={"spread_start":riskConfig["spread_safety_range"][0],
            "stop_spread":riskConfig["spread_safety_range"][1],
            "xau_margin_level":riskConfig["xau_margin_level"],
            "xau_margin_level_stop_trade":riskConfig["xau_margin_level_stop_trade"],
            "usd_margin_level":riskConfig["usd_margin_level"],
            "usd_margin_level_stop_trade":riskConfig["usd_margin_level_stop_trade"],
            "xau_equity":riskConfig["xau_equity"],
            "usd_equity":riskConfig["usd_equity"],
            "ctp_margin_free":riskConfig["ctp_margin_free"],
            "ctp_margin_free_stop_trade":riskConfig["ctp_margin_free_stop_trade"],
            "xua_price_start":riskConfig["xau_price_safety_range"][0],
            "xua_price_stop":riskConfig["xau_price_safety_range"][1],
            "usd_price_start":riskConfig["usd_price_safety_range"][0],
            "usd_price_stop":riskConfig["usd_price_safety_range"][1],
            "xau_volatility_time":riskConfig["xau_volatility_time"],
            "xau_volatility_price":riskConfig["xau_volatility_price"],
            }
        return risk_config
    def update_risk_config(self,data):
        self.risk.update_risk(data)


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
        with self.order_lock:
            for order  in self.OrdersDict.values():
                if order.fixedCloseSpread:
                    continue
                if order.status < 4 and order.status>0:
                    order.closeSpread = util.get_caculate_close_spread(order.realOpenSpread, order.longShort, self.strategyConfig[long_short]["base"]["closeSpread"])
                    # self.OrdersDict[order.entrustNo].closeSpread = util.get_caculate_close_spread(order.realOpenSpread, self.strategyConfig[long_short]["base"]["startSpread"], self.strategyConfig[long_short]["base"]["closeSpread"])
                    self.db.update_parent_order(self.dbConfig["table"],order)
                    self.OrdersDict[order.entrustNo] = order
        self.strategyStatus = True
        log.info("strategy update success: {}".format(self.strategyConfig))

    def update_is_manual_close_flow_strategy(self,long_short,close_flow_strategy):
        self.strategyStatus = False
        with self.lock:
            self.strategyConfig[long_short]['base']['closeByStrategy']=close_flow_strategy
        self.strategyStatus = True
        log.info("strategy update is_manual_close_flow_strategy success: {}".format(self.strategyConfig))
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
    def update_total_cost(self,total_cost):
        with self.lock:
            self.strategyConfig['BASE']['total_cost']=total_cost
            self.cfg.write_strategy(self.strategyConfig)
            self.total_cost=total_cost

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

    def close_orders(self,entrustNos):
        self.strategyStatus=False
        successed_n=0
        for entrustNo in entrustNos:
            with self.order_lock:
                order=self.OrdersDict[entrustNo]
            if order.status>3:
                continue
            strategy = self.get_longshort_strategy(order.longShort)
            while True:
                self.order_max_limit.increase_trade_count()
                success,re_start = self.closeOrder(order,strategy)
                time.sleep(self.strategyConfig["BASE"]["limit_open_close_time"])
                if success:
                    successed_n+=1
                    break
                elif not success and re_start:
                    continue
        self.strategyStatus = True
        return successed_n==len(entrustNos), successed_n
    def get_order_by_entrustno(self,entrustNo):
        with self.order_lock:
            return self.OrdersDict[entrustNo]
    def exec_error_child_order(self, porder, openClose, longShort, symbol, client, vol, max_retries=5):
        for attempt in range(1, max_retries + 1):
            success, rtnExecOrder = util.send_order_to_server(client, symbol, longShort, openClose, vol, porder.entrustNo)
            if not success:
                return False, rtnExecOrder
            if not rtnExecOrder.req_success:
                return False, rtnExecOrder.errmsg
            if rtnExecOrder.order.status == models.AllTrade:
                return True, rtnExecOrder.order
            else:
                log.warning("exec_error_child_order entrustNo:{} {} {} {} fail,order:{}".format(porder.entrustNo, symbol, openClose, longShort, rtnExecOrder.order))
                self.notifyApi.notify_exec_error_order_fail(porder.spread,symbol,longShort,openClose,vol,f"执行处理异常委托的请求失败{attempt}次,msg:{rtnExecOrder.order.statusMsg}",attempt)
            log.warning(f"[{attempt}/{max_retries}] 未成交，重试: {symbol}, {openClose}, {longShort}, order={rtnExecOrder.order}")
            time.sleep(0.1)
        return False, f"重试{max_retries}次后订单仍未成交"
    def exec_error_child_order_special(self,porder,need_to_copy_child_to_new_parent):
        porder_new = copy.deepcopy(porder)
        entrustNo=self.getEntrustNo()
        porder_new.entrustNo = entrustNo
        self.move_child_order_to_new_parent(porder.entrustNo,entrustNo,need_to_copy_child_to_new_parent)
        porder.status=comm.PARENT_STATUS_OPEN_FAIL
        for client in need_to_copy_child_to_new_parent.values():
            success,rsp=util.send_req_move_order_to_new_order_for_child(client,porder.entrustNo,porder_new.entrustNo)
            if not success:
                return success , rsp
        porder_new.uuid = str(uuid.uuid4())
        self.save_order(porder_new)
        self.updateOrder(porder)
        return True,porder_new
    def deal_error_orders(self,entrust_no,deal_type):
        porder = self.get_order_by_entrustno(entrust_no)
        strategy = self.get_longshort_strategy(porder.longShort)
        ctpQty, xauQty, usdQty = self.get_position_from_pid(entrust_no,strategy)
        mt5LongShort = util.get_longShort_from_ctp_longShort(porder.longShort)
        operations = []
        core_move_child_to_new_parent = []
        child_move_child_to_new_parent = []
        need_to_copy_child_to_new_parent ={}
        if deal_type == "CLOSE":
            if not util.float_equal(ctpQty,0) :
                operations.append((comm.OFFSET_CLOSE, porder.longShort, strategy['op1']['symbol'], self.zmqCtpClient, 1))
            if not util.float_equal(xauQty,0) :
                operations.append((comm.OFFSET_CLOSE, mt5LongShort, strategy['op2']['symbol'], self.zmqXAUClient, strategy['op2']['rate']))
            if not util.float_equal(usdQty,0) :
                operations.append((comm.OFFSET_CLOSE, mt5LongShort, strategy['op3']['symbol'], self.zmqUSDClient, strategy['op3']['rate']))
        elif deal_type == "ADD":
            if util.float_equal(ctpQty,0) :
                operations.append((comm.OFFSET_OPEN, porder.longShort, strategy['op1']['symbol'], self.zmqCtpClient, 1))
            else:
                need_to_copy_child_to_new_parent[strategy['op1']['symbol']]=self.zmqCtpClient
            if util.float_equal(xauQty,0) :
                operations.append((comm.OFFSET_OPEN, mt5LongShort, strategy['op2']['symbol'], self.zmqXAUClient, strategy['op2']['rate']))
            else:
                need_to_copy_child_to_new_parent[strategy['op2']['symbol']]=self.zmqXAUClient
            if util.float_equal(usdQty,0) :
                operations.append((comm.OFFSET_OPEN, mt5LongShort, strategy['op3']['symbol'], self.zmqUSDClient, strategy['op3']['rate']))
            else:
                need_to_copy_child_to_new_parent[strategy['op3']['symbol']]=self.zmqUSDClient
            if len(need_to_copy_child_to_new_parent)>0:
                success,rsp=self.exec_error_child_order_special(porder,need_to_copy_child_to_new_parent)
                if not success:
                    log.warning("移动子单至新母单下失败,{}".format(rsp))
                    return success ,"移动子单至新母单下失败,{}".format(rsp)
                porder=rsp
        for offset, direction, symbol, client, rate in operations:
            success,msgOrOrder=self.exec_error_child_order(porder, offset, direction, symbol, client, rate)
            if not success:
                return success ,f"{porder.entrustNo} {symbol} {offset} {direction} {msgOrOrder}"
            self.store_child_order(msgOrOrder)
        if deal_type == "ADD":
            ctp_order = self.get_child_order(porder.entrustNo, comm.OFFSET_OPEN, strategy["op1"]["symbol"])
            xau_order = self.get_child_order(porder.entrustNo, comm.OFFSET_OPEN, strategy["op2"]["symbol"])
            usd_order = self.get_child_order(porder.entrustNo, comm.OFFSET_OPEN, strategy["op3"]["symbol"])
            porder.realOpenSpread = util.get_caculate_spread_from_price(ctp_order.bidPrice, xau_order.bidPrice, usd_order.bidPrice)
            porder.closeSpread = util.get_caculate_close_spread(porder.realOpenSpread, porder.longShort, strategy["base"]["closeSpread"])
            porder.status = comm.PARENT_STATUS_OPEN_MT5_2
        elif deal_type == "CLOSE":
            if porder.status < comm.PARENT_STATUS_CLOSE_CTP:
                porder.status = comm.PARENT_STATUS_OPEN_FAIL
            elif porder.status >= comm.PARENT_STATUS_CLOSE_CTP:
                ctp_order = self.get_child_order(porder.entrustNo, comm.OFFSET_CLOSE, strategy["op1"]["symbol"])
                xau_order = self.get_child_order(porder.entrustNo, comm.OFFSET_CLOSE, strategy["op2"]["symbol"])
                usd_order = self.get_child_order(porder.entrustNo, comm.OFFSET_CLOSE, strategy["op3"]["symbol"])
                porder.realCloseSpread = util.get_caculate_spread_from_price(ctp_order.bidPrice, xau_order.bidPrice, usd_order.bidPrice)
                porder.closed_at = datetime.datetime.now()
                porder.status = comm.PARENT_STATUS_CLOSE_MT5_2
        self.updateOrder(porder)
        return True ,""
    def web_reconnect_mt5(self, mt5_name):
        if mt5_name != 'xau' and mt5_name != 'usd':
            return False,"重连请求字段非法"
        self.strategyStatus = False
        zmq_client = self.zmqXAUClient  if mt5_name == 'xau' else self.zmqUSDClient
        success,response=util.send_reconnect_mt5_to_client(zmq_client)
        time.sleep(2)
        if success:
            unfinished_order_dict=self.get_unfinished_error_orders()
            for entrustNo,local_order in unfinished_order_dict.items():
                openClose = comm.OFFSET_OPEN if local_order.status <= 3 else comm.OFFSET_CLOSE
                success, errmsgOrOrder = util.get_child_order_from_mt5(zmq_client, entrustNo, openClose)
                if success:
                    if errmsgOrOrder.status == models.AllTrade:
                        self.store_child_order(errmsgOrOrder)
                        strategy = self.get_longshort_strategy(local_order.longShort)
                        ctpQty, xauQty, usdQty = self.get_position_from_pid(entrustNo,strategy)
                        if util.float_equal(ctpQty,0) and util.float_equal(xauQty,0) and util.float_equal(usdQty,0):
                            local_order.status=comm.PARENT_STATUS_CLOSE_MT5_2
                            ctp_order = self.get_child_order(entrustNo, openClose, strategy["op1"]["symbol"])
                            xau_order = self.get_child_order(entrustNo, openClose, strategy["op2"]["symbol"])
                            usd_order = self.get_child_order(entrustNo, openClose, strategy["op3"]["symbol"])
                            local_order.realCloseSpread = util.get_caculate_spread_from_price(ctp_order.bidPrice, xau_order.bidPrice, usd_order.bidPrice)
                            local_order.closed_at = datetime.datetime.now()
                        elif not util.float_equal(ctpQty,0) and not util.float_equal(xauQty,0) and not util.float_equal(usdQty,0):
                            local_order.status = comm.PARENT_STATUS_OPEN_MT5_2
                            ctp_order = self.get_child_order(entrustNo, openClose, strategy["op1"]["symbol"])
                            xau_order = self.get_child_order(entrustNo, openClose, strategy["op2"]["symbol"])
                            usd_order = self.get_child_order(entrustNo, openClose, strategy["op3"]["symbol"])
                            local_order.realOpenSpread = util.get_caculate_spread_from_price(ctp_order.bidPrice, xau_order.bidPrice, usd_order.bidPrice)
                            local_order.closeSpread = util.get_caculate_close_spread(local_order.realOpenSpread, local_order.longShort, strategy["base"]["closeSpread"])
                        self.updateOrder(local_order)
                else:
                    self.check_send_status(success, errmsgOrOrder, "重连后查询订单超时")
        self.strategyStatus = True
    def get_position_from_pid(self,pid,strategy):
        with self.child_order_lock:
             ctpQty=self.child_order_dict[pid][comm.OFFSET_OPEN][strategy["op1"]["symbol"]].bidVol-self.child_order_dict[pid][comm.OFFSET_CLOSE][strategy["op1"]["symbol"]].bidVol
             xauQty=self.child_order_dict[pid][comm.OFFSET_OPEN][strategy["op2"]["symbol"]].bidVol-self.child_order_dict[pid][comm.OFFSET_CLOSE][strategy["op2"]["symbol"]].bidVol
             usdQty=self.child_order_dict[pid][comm.OFFSET_OPEN][strategy["op3"]["symbol"]].bidVol-self.child_order_dict[pid][comm.OFFSET_CLOSE][strategy["op3"]["symbol"]].bidVol
             return ctpQty,xauQty,usdQty
    def get_error_orders(self):
        error_orders=self.get_unfinished_error_orders()
        orders = []
        for error_order in error_orders.values():
            strategy = self.get_longshort_strategy(error_order.longShort)
            ctpQty,xauQty,usdQty=self.get_position_from_pid(error_order.entrustNo,strategy)
            order = {}
            order['entrustNo'] = error_order.entrustNo
            order['long_short'] = error_order.longShort
            order['openClose'] = '开' if error_order.status <= 3 else '平'
            order['spread'] = error_order.spread
            order['realOpenSpread'] = error_order.realOpenSpread
            order['closeSpread'] = error_order.closeSpread
            order['created_at'] = error_order.created_at.isoformat()
            order['closed_at'] = error_order.closed_at.isoformat()
            order['ctpQty'] =ctpQty
            order['xauQty'] =xauQty
            order['usdQty'] =usdQty
            orders.append(order)
        return orders
    def web_update_orders(self,entrustNos):
        # 更新数据库(完成)
        # 先关闭策略,最后开启
        # 1 修改母单状态,
        # 2 发送请求给  ctp: 修改CTP子单,修改持仓,更新数据库
        # 3 发送请求给  mt5: 修改2个MT5订单状态
        self.strategyStatus=False
        real_need_to_update_orders=[]
        with self.order_lock:
            for entrustNo in entrustNos:
                if self.OrdersDict[entrustNo].status==comm.PARENT_STATUS_CLOSE_MT5_2:
                    continue
                real_need_to_update_orders.append(entrustNo)
                self.OrdersDict[entrustNo].status=comm.PARENT_STATUS_CLOSE_MT5_2
        success,response=util.send_closed_pid_orders_to_server(self.zmqCtpClient,real_need_to_update_orders)
        if not success:
            log.warning("core update ctp orders fail,err:{}".format(response))
            self.strategyStatus = True
            return success,response.errmsg
        success,errmsg=util.send_closed_pid_orders_to_server(self.zmqXAUClient,real_need_to_update_orders)
        if not success:
            log.warning("core update xau orders fail,err:{}".format(response))
            self.strategyStatus = True
            return success,response.errmsg
        success,errmsg=util.send_closed_pid_orders_to_server(self.zmqUSDClient,real_need_to_update_orders)
        if not success:
            log.warning("core update usd orders fail,err:{}".format(response))
            self.strategyStatus = True
            return success,response.errmsg
        self.strategyStatus = True
        log.info("orders pid={} be update_closed from web".format(entrustNos))
        return success,""
    def web_open_order(self,longShort,spread):
        self.strategyStatus=False
        strategy = self.get_longshort_strategy(longShort)
        while True:
            # is_open, current_position, max_position = self.check_position_limit(strategy, 1)
            # if not is_open:
            #     msg=f"当前持仓{current_position} 当前/总最大持仓{max_position} 禁止手动开仓"
            #     log.warning(msg)
            #     return False, msg
            self.order_max_limit.increase_trade_count()
            success = self.openOrder(longShort,spread, 0, 0, 0, 0, 0, 0,strategy,True)
            if success:
                break
            else:
                self.strategyStatus = True
                return False,"开仓失败"
        self.strategyStatus = True
        return True,"开仓成功"

    def clear_all_data(self):
        self.strategyStatus=False
        with self.lock:
            self.strategyConfig[comm.ACTION_LONG]['base']['isRun'] = False
            self.strategyConfig[comm.ACTION_SHORT]['base']['isRun'] = False
        success,errmsg=self.db.clear_table(self.dbConfig["table"])
        if not success:
            log.error("clear db fail, err:{}".format(errmsg))
            self.strategyStatus=True
            return success,errmsg
        self.trade_limit = {comm.ACTION_LONG: {comm.OFFSET_OPEN: TradeThrottler(interval=0.5), comm.OFFSET_CLOSE: TradeThrottler(interval=0.5)}, comm.ACTION_SHORT: {comm.OFFSET_OPEN: TradeThrottler(interval=0.5), comm.OFFSET_CLOSE: TradeThrottler(interval=0.5)}}
        with self.order_lock:
            self.OrdersDict = {}  # pid->POrder
        success, response=util.send_clear_all_data_to_server(self.zmqCtpClient)
        if not success:
            log.warning("core clear ctp fail,err:{}".format(response.errmsg))
            self.strategyStatus = True
            return success,response.errmsg
        success, response = util.send_clear_all_data_to_server(self.zmqXAUClient)
        if not success:
            log.warning("core clear ctp fail,err:{}".format(response.errmsg))
            self.strategyStatus = True
            return success,response.errmsg
        success, response = util.send_clear_all_data_to_server(self.zmqUSDClient)
        if not success:
            log.warning("core clear ctp fail,err:{}".format(response.errmsg))
            self.strategyStatus = True
            return success,response.errmsg
        self.strategyStatus = True
        return True,""
    def close_all_positions(self, longshort):
        with self.lock:
            self.strategyConfig[longshort]['base']['isRun'] = False
        orders = self.getNoFinishOrders(longshort)
        successed_n = 0
        need_to_closed_n=len(orders)
        while True:
            if len(orders) == 0:
                break
            else:
                log.info("server need to close orders vol: {}".format(len(orders)))
                for order in orders:
                    strategy = self.get_longshort_strategy(order.longShort)
                    while True:
                        success,re_start = self.closeOrder(order, strategy)
                        if success:
                            successed_n+=1
                            break
                        elif not success and re_start:
                            continue
            orders = self.getNoFinishOrders(longshort)
        log.info("server [{}] closed all orders success !!!!!!!!!!!!!!!".format(longshort))
        return 0==len(orders),successed_n
    def save_order(self, order):
        with self.order_lock:
            self.OrdersDict[order.entrustNo] = order
        self.db.save_parent_order(self.dbConfig["table"], order)
        return True, ""

    def updateOrder(self, order):
        with self.order_lock:
            self.OrdersDict[order.entrustNo] = order
        self.db.update_parent_order(self.dbConfig["table"], order)
        return True, ""

    def create_order(self, longShort, CTPAUAskPrice, CTPAUBidPrice, MT5AUAskPrice, MT5AUBidPrice, USDAskPrice, USDBidPrice, spread, askCtpQty, askMt51Qty, askMt52Qty,is_manual):
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
        order.is_manual = is_manual
        success, errmsg = self.save_order(order)
        if success:
            log.info("create order :{}".format(order))
            return True, order
        else:
            log.warning("save order fail,pEntrustNo:{}  error:{}".format(order.entrustNo, errmsg))
            return False, None

    def getNoFinishOrders(self,longshort):
        orders = []
        with self.order_lock:
            for order in self.OrdersDict.values():
                if order.status < 6 and order.status > 0 and order.longShort== longshort:
                    orders.append(order)
        return orders

    def get_position_numbers(self, longShort):
        vol = 0
        with self.order_lock:
            for order in self.OrdersDict.values():
                if order.longShort == longShort and order.status > comm.PARENT_STATUS_OPEN_CTP and order.status < comm.PARENT_STATUS_CLOSE_MT5_2:
                    vol += 1

        return vol
    def get_strategy_position_numbers(self, longShort):
        vol = 0
        with self.order_lock:
            for order in self.OrdersDict.values():
                if order.longShort == longShort and order.status > comm.PARENT_STATUS_OPEN_CTP and order.status < comm.PARENT_STATUS_CLOSE_MT5_2 and not order.is_manual:
                    vol += 1
        return vol
    def get_unfinished_error_orders(self):
        with self.order_lock:
            return  {k: v for k, v in self.OrdersDict.items() if v.status in [comm.PARENT_STATUS_OPEN_CTP, comm.PARENT_STATUS_OPEN_MT5_1, comm.PARENT_STATUS_CLOSE_CTP, comm.PARENT_STATUS_CLOSE_MT5_1]}

    def openOrder(self, action, spread, ctpMarket_askPrice1, ctpMarket_bidPrice1, XAUUSDm_askPrice1, XAUUSDm_bidPrice1, USDCNHm_askPrice1, USDCNHm_bidPrice1,strategy,is_manual=False):
        openOrderStatus = False

        # 开仓失败 或者不需要开仓,则更新数据库
        success, order = self.create_order(action, ctpMarket_askPrice1, ctpMarket_bidPrice1, XAUUSDm_askPrice1, XAUUSDm_bidPrice1, USDCNHm_askPrice1, USDCNHm_bidPrice1, spread, 1, strategy["op2"]["rate"], strategy["op3"]["rate"],is_manual)
        if not success:
            self.notifyApi.notify_net_error("save db")
            exit(-1)

        # -1未知  0待开仓/ 1ctp开仓 / 2伦敦金开仓/ 3汇率开仓 /5 待平仓 /6 ctp平仓 /7 伦敦金平仓/ 8 汇率平仓  /10异常
        # 成交结果返回, 需要区分 异常 和 未成交
        success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, strategy["op1"]["symbol"], action, comm.OFFSET_OPEN, 1, order.entrustNo)
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
            self.store_child_order(rtnExecOrder.order)
            self.updateOrder(order)
            self.order_max_limit.not_cancel()
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
                if not success:
                    self.strategyStatus = False
                    self.trade_zmq_req_timeout(success, rtnMt5Exec1 , "伦敦金开仓失败,方向: {}".format(action))
                    break
                if rtnMt5Exec1.req_success:
                    xau_p = rtnMt5Exec1.order.bidPrice
                    if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                        xauusd_askQty -= rtnMt5Exec1.order.bidVol
                        xauusd_bidQty += rtnMt5Exec1.order.bidVol
                        log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(strategy["op2"]["symbol"], action, xauusd_bidQty, xau_p))
                    if float_equal(xauusd_askQty, 0):
                        order.status = comm.PARENT_STATUS_OPEN_MT5_1
                        self.store_child_order(rtnMt5Exec1.order)
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
            if order.status != comm.PARENT_STATUS_OPEN_MT5_1:
                return False
            #  mt5 汇率成交  订单成交通知
            usdcnh_askQty = strategy["op3"]["rate"]
            usdcnh_bidQty = 0
            while True:
                success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient, strategy["op3"]["symbol"],mt5Action,comm.OFFSET_OPEN, usdcnh_askQty, order.entrustNo)
                if not success:
                    self.strategyStatus = False
                    self.trade_zmq_req_timeout(success, rtnMt5Exec2, "汇率开仓失败,方向: {}".format(action))
                    break
                #  mt5 汇率成交  订单成交通知
                if rtnMt5Exec2.req_success:
                    usd_p = rtnMt5Exec2.order.bidPrice
                    if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                        usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                        usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                        log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(strategy["op3"]["symbol"], action, usdcnh_bidQty, usd_p))
                    if float_equal(usdcnh_askQty, 0):
                        order.status = comm.PARENT_STATUS_OPEN_MT5_2
                        self.store_child_order(rtnMt5Exec2.order)
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
            order.closeSpread = util.get_caculate_close_spread(realSpread, order.longShort, strategy["base"]["closeSpread"])
            self.updateOrder(order)
            log.info("server open order success,{}".format(order))

            openOrderStatus = True
        else:
            order.status = comm.PARENT_STATUS_OPEN_FAIL
            order.statusMsg=rtnExecOrder.errmsg + rtnExecOrder.order.statusMsg
            self.updateOrder(order)
            log.info("server open order fail,msg:{}".format(rtnExecOrder.errmsg))
            #symbol, longshort, openclose, vol, msg
            self.notifyApi.notify_trade_fail(spread, strategy["op1"]["symbol"], action, comm.OFFSET_OPEN, strategy["op1"]["rate"], rtnExecOrder.errmsg + rtnExecOrder.order.statusMsg)


        return openOrderStatus

    def closeOrder(self, order,strategy):
        # 根据order 状态平仓, 3 平全部,4 平mt5,5平外汇
        closeOrderStatus = False
        re_start_close = False
        ctp_p = 0.0
        xau_p = 0.0
        usd_p = 0.0
        if order.status == comm.PARENT_STATUS_OPEN_MT5_2:
            # 4 ctp平仓 /5 伦敦金平仓/ 6汇率平仓
            success, rtnExecOrder = util.send_order_to_server(self.zmqCtpClient, strategy["op1"]["symbol"], order.longShort, comm.OFFSET_CLOSE, 1, order.entrustNo)
            self.check_send_status(success, rtnExecOrder , "  ctp close order")
            if rtnExecOrder.req_success:
                if rtnExecOrder.order.status==models.AllTrade:
                    order.status = comm.PARENT_STATUS_CLOSE_CTP
                    ctp_p = rtnExecOrder.order.bidPrice
                    self.store_child_order(rtnExecOrder.order)
                    self.updateOrder(order)
                    self.order_max_limit.not_cancel()
                    log.info("server close ctp order success,msg:{}".format(rtnExecOrder))
                else:
                    log.info("server close ctp fail,msg:{}".format(rtnExecOrder))
                    re_start_close=True
                    return closeOrderStatus,re_start_close
            else:
                log.info("server req close ctp order fail,msg::{}".format(rtnExecOrder.errmsg))
                self.notifyApi.notify_trade_fail(order.spread, strategy["op1"]["symbol"], order.longShort, comm.OFFSET_CLOSE, 1, rtnExecOrder.errmsg + rtnExecOrder.order.statusMsg)
                return closeOrderStatus,re_start_close

        mt5Action = util.get_longShort_from_ctp_longShort(order.longShort)
        if order.status == comm.PARENT_STATUS_CLOSE_CTP:
            # 平 伦敦金
            xauusd_askQty = strategy["op2"]["rate"]
            xauusd_bidQty = 0
            while True:
                success, rtnMt5Exec1 = util.send_order_to_server(self.zmqXAUClient, strategy["op2"]["symbol"],  mt5Action, comm.OFFSET_CLOSE, strategy["op2"]["rate"],order.entrustNo)
                if not success:
                    self.strategyStatus = False
                    self.trade_zmq_req_timeout(success, rtnMt5Exec1, "伦敦金平仓失败,方向: {}".format(order.longShort))
                    break
                if rtnMt5Exec1.req_success:
                    xau_p = rtnMt5Exec1.order.bidPrice
                    if rtnMt5Exec1.order.status == models.AllTrade or rtnMt5Exec1.order.status == models.PARTTRADE:
                        xauusd_askQty -= rtnMt5Exec1.order.bidVol
                        xauusd_bidQty += rtnMt5Exec1.order.bidVol
                        log.info("mt5 close success,{}  {}  vol:{} ,price:{}".format(strategy["op2"]["symbol"], mt5Action, xauusd_bidQty, xau_p))
                    if float_equal(xauusd_askQty, 0):
                        order.status = comm.PARENT_STATUS_CLOSE_MT5_1
                        self.store_child_order(rtnMt5Exec1.order)
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, xauusd_askQty, xauusd_bidQty)
                        break

                else:
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread, strategy["op2"]["symbol"], mt5Action, xauusd_askQty, rtnMt5Exec1.errmsg))
                    #  发送成交失败通知
                    self.notifyApi.notify_trade_fail(order.spread,strategy["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, strategy["op2"]["rate"], rtnMt5Exec1.errmsg)

        if order.status == comm.PARENT_STATUS_CLOSE_MT5_1:
            #  mt5 汇率成交  订单成交通知
            usdcnh_askQty = strategy["op3"]["rate"]
            usdcnh_bidQty = 0
            while True:
                success, rtnMt5Exec2 = util.send_order_to_server(self.zmqUSDClient, strategy["op3"]["symbol"], mt5Action, comm.OFFSET_CLOSE, strategy["op3"]["rate"], order.entrustNo)
                if not success:
                    self.strategyStatus = False
                    self.trade_zmq_req_timeout(success, rtnMt5Exec2, "汇率金平仓失败,方向: {}".format(order.longShort))
                    break
                if rtnMt5Exec2.req_success:
                    usd_p = rtnMt5Exec2.order.bidPrice
                    if rtnMt5Exec2.order.status == models.AllTrade or rtnMt5Exec2.order.status == models.PARTTRADE:
                        usdcnh_askQty -= rtnMt5Exec2.order.bidVol
                        usdcnh_bidQty += rtnMt5Exec2.order.bidVol
                        log.info("mt5 open success,{}  {}  vol:{} ,price:{}".format(strategy["op3"]["symbol"], mt5Action, usdcnh_bidQty, usd_p))
                    if float_equal(usdcnh_askQty, 0):
                        order.status =comm.PARENT_STATUS_CLOSE_MT5_2
                        self.store_child_order(rtnMt5Exec2.order)
                        self.updateOrder(order)
                        self.notifyApi.notify_trade_success()
                        # self.notifyApi.notify_trade_success(order.spread, self.strategyConfig["op3"]["symbol"], mt5Action, comm.OFFSET_OPEN, usdcnh_askQty, usdcnh_bidQty)
                        break
                else:
                    #  发送成交失败通知
                    log.warning("mt5 close fail,spread:{}, {}  {}  {}  {}".format(order.spread, strategy["op3"]["symbol"], mt5Action, usdcnh_askQty, rtnMt5Exec2.errmsg))
                    self.notifyApi.notify_trade_fail(order.spread,strategy["op2"]["symbol"], mt5Action, comm.OFFSET_CLOSE, strategy["op2"]["rate"], rtnMt5Exec2.errmsg)

            # 计算实际点差
            order.realCloseSpread = util.get_caculate_spread_from_price(ctp_p, xau_p, usd_p)
            order.closed_at = datetime.datetime.now()
            self.updateOrder(order)
            closeOrderStatus = True

        return closeOrderStatus,re_start_close

        # 清仓指令开始时先 关闭策略 按策略清
                # 清仓 ctp

    def check_position_limit(self,strategy,vol):
        longshort=strategy["base"]["longShort"]
        current_positon = self.get_strategy_position_numbers(longshort)
        #检查总的最大值
        if current_positon >= strategy["base"]["maxVol"]:
            self.notifyApi.notify_monitor_positions_above_limit(current_positon,strategy["base"]["maxVol"],longshort)
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
                if order.is_manual and not strategy["base"]["closeByStrategy"]:
                    continue
                is_close, spread = util.should_close_order(ctpMarket, XAUUSDm, USDCNHm, order)
                log_msg+=f"\n\t\t\t\tentrustNo:{order.entrustNo}, need_to_close:{is_close} ,open_spread:{order.realOpenSpread:.2f},close_spread:{order.closeSpread:.2f}"
                if is_close == True:
                    need_to_close_orders.append(order)
        log.info(log_msg)

        for order in need_to_close_orders:
            self.trade_limit[longShort][comm.OFFSET_CLOSE].post_commit()
            allow_trade,order_count=self.order_max_limit.pre_check()
            if not allow_trade:
                log.info("今天下单笔数超过限制,策略禁止下单,转人工处理,当前为笔数为:[{}]".format(order_count))
                return
            success,re_start = self.closeOrder(order,strategy)
            if success:
                current_position = self.get_position_numbers(order.longShort)
                log.info("close order success,{}  current positions={}".format(longShort,current_position))
                # 汇总通知 start, strategy_range, spread,longshort, openClose, position
                self.notifyApi.send_trade_result(strategy["base"]["startSpread"], strategy["base"]["rangeSpread"], order.spread, order.longShort, comm.OFFSET_CLOSE, current_position)
            else:
                log.warning("平仓失败,entrustNO:{} ".format(order.entrustNo))

    def checkShouldOpenOrder(self, ctpMarket, XAUUSDm, USDCNHm,strategy):
        longshort=strategy["base"]["longShort"]
        is_open, vol,  spread = util.should_open_order_longshort(ctpMarket, XAUUSDm, USDCNHm, strategy["base"]["startSpread"], strategy["base"]["rangeSpread"],longshort)
        # is_open 为False,spread 为[空点差,多点差], True 为点差
        if  not is_open:
            log.info("this market not to open {},[short_spread,long_spread]==>{}".format(longshort,spread))
            return

        is_open,current_position,max_position=self.check_position_limit(strategy,vol)
        log.info("this market could to open order {}:{} ,current_positions:[{}],max_positions:[{}],current_spread:{:.2f},start_spread:{:.2f},range_spread:{:.2f}".format(longshort,is_open,current_position,max_position, spread, strategy["base"]["startSpread"], strategy["base"]["rangeSpread"]))
        # 区间最大手数=区间倍数*区间手数
        if not is_open:
            log.info("not should to open {},current_position[{}] >= max_position[{}], not to open order".format(longshort,current_position, max_position))
            return

        if is_open:
            self.trade_limit[longshort][comm.OFFSET_OPEN].post_commit()
            allow_trade,order_count=self.order_max_limit.pre_check()
            if not allow_trade:
                log.info("今天下单笔数超过限制,策略禁止下单,转人工处理,当前为笔数为:[{}]".format(order_count))
                return
            success=self.openOrder(longshort, spread, ctpMarket.askPrice1, ctpMarket.bidPrice1, XAUUSDm.askPrice1, XAUUSDm.bidPrice1, USDCNHm.askPrice1, USDCNHm.bidPrice1,strategy)
            if success:
                current_position=self.get_position_numbers(longshort)
                log.info("open order {} success,current positions={}".format(longshort,current_position))
                self.notifyApi.send_trade_result(strategy["base"]["startSpread"], strategy["base"]["rangeSpread"], spread, longshort, comm.OFFSET_OPEN,current_position)
            else:
                log.warning("{} 开仓失败".format(longshort))


    def loadOrdersFromDB(self):
        # 导入本地mysql 所有委托,
        # 查询ctp/mt5 委托

        # 获取entrustNo
        # 整理数据,检查持仓(清理异常持仓)
        # maxEntrustNo
        self.entrustNo = self.db.get_max_id(self.dbConfig["table"])
        pOrders = self.db.load_parent_orders(self.dbConfig["table"])
        for porder in pOrders:
            with self.order_lock:
                self.OrdersDict[porder.entrustNo]=porder
            # 获取maxEntrustNo
            if porder.entrustNo > self.entrustNo:
                self.entrustNo = porder.entrustNo

            #  平仓 非终态 5<=status<8  ,校验平常
            #  开仓 非终态 1<status<3   ,校验开仓
            #  状态为 未知,待开仓,已开仓,已平仓,错误 直接跳过
            # 整理数据,检查持仓(清理异常持仓)
            if porder.status == comm.PARENT_STATUS_UNKWON  or porder.status == comm.PARENT_STATUS_CLOSE_MT5_2  or porder.status==comm.PARENT_STATUS_OPEN_FAIL:
                continue
            # #pid-->symbol-->open_close-->orders  orders[11]=symbol_order  symbol_order[symbol]=childOrders
            operation_plan = [(self.zmqCtpClient,porder.entrustNo),(self.zmqXAUClient,porder.entrustNo),(self.zmqUSDClient,porder.entrustNo)]
            child_orders=[]
            for client, pid in operation_plan:
            # 校验 CTP
            # 设置初始状态, 不补单则代表该子单已完成,状态+1 ,由于状态以 CTP 开平做标准,故CTP状态不做处理
                success, resOrOrders=util.qry_child_order_from_pid(client, pid)
                self.check_search_status(success, resOrOrders, "child order search")
                self.strategyStatus =False
                if success:
                    child_orders.extend(resOrOrders.orders)
            for child_order in child_orders:
                self.store_child_order(child_order)
            strategy = self.get_longshort_strategy(porder.longShort)
            ctpQty, xauQty, usdQty = self.get_position_from_pid(porder.entrustNo,strategy)
            if util.float_equal(ctpQty, 0) and util.float_equal(xauQty, 0) and util.float_equal(usdQty, 0):
                porder.status = comm.PARENT_STATUS_CLOSE_MT5_2

            # 校验MT5-1
                ctp_order = self.get_child_order(porder.entrustNo, comm.OFFSET_CLOSE, strategy["op1"]["symbol"])
                xau_order = self.get_child_order(porder.entrustNo, comm.OFFSET_CLOSE, strategy["op2"]["symbol"])
                usd_order = self.get_child_order(porder.entrustNo, comm.OFFSET_CLOSE, strategy["op3"]["symbol"])
                porder.realCloseSpread = util.get_caculate_spread_from_price(ctp_order.bidPrice, xau_order.bidPrice, usd_order.bidPrice)
                porder.closed_at = datetime.datetime.now()
            elif not util.float_equal(ctpQty, 0) and not util.float_equal(xauQty, 0) and not util.float_equal(usdQty, 0):
                porder.status = comm.PARENT_STATUS_OPEN_MT5_2

                ctp_order = self.get_child_order(porder.entrustNo, comm.OFFSET_OPEN, strategy["op1"]["symbol"])
                xau_order = self.get_child_order(porder.entrustNo, comm.OFFSET_OPEN, strategy["op2"]["symbol"])
                usd_order = self.get_child_order(porder.entrustNo, comm.OFFSET_OPEN, strategy["op3"]["symbol"])
                porder.realOpenSpread = util.get_caculate_spread_from_price(ctp_order.bidPrice, xau_order.bidPrice, usd_order.bidPrice)
                porder.closeSpread = util.get_caculate_close_spread(porder.realOpenSpread, porder.longShort, strategy["base"]["closeSpread"])

            self.updateOrder(porder)



    def exc_add_error_orders(self,web_error_orders):
        self.strategyStatus = False
        new_error_orders = []
        success_n=0
        # 遍历 网页的订单, 然后和系统内的委托 标的+委托号对比,找到系统内对应的异常委托, 再进行下单
        for web_error_order in web_error_orders:
            for old_error_order in self.ErrorOrders:
                if old_error_order.symbol ==  web_error_order["symbol"] and old_error_order.entrustNo ==  web_error_order["entrustNo"]:
                    success, rtnExecOrder = util.send_order_to_server(old_error_order.zmqClient, old_error_order.symbol, old_error_order.long_short, old_error_order.open_close, old_error_order.vol, old_error_order.entrustNo)
                    self.check_send_status(success, rtnExecOrder, f"{old_error_order.symbol}  {old_error_order.long_short} {old_error_order.open_close} order")
                    if rtnExecOrder.req_success:
                        with self.order_lock:
                            porder = self.OrdersDict[old_error_order.entrustNo]
                        porder.status += 1
                        log.info("parent add order success,{}  {}  {}  vol:{} entrustNo:{}".format(old_error_order.symbol, old_error_order.long_short, old_error_order.open_close, old_error_order.vol, old_error_order.entrustNo))
                        self.updateOrder(porder)
                        self.POrderToChildOrdersDict[old_error_order.entrustNo][old_error_order.symbol].append(rtnExecOrder.order)  # 补仓成功通知  # self.notifyApi.notify_trade_success(spread, self.strategyConfig["op1"]["symbol"], action, comm.OFFSET_OPEN, self.strategyConfig["op1"]["rate"], self.strategyConfig["op1"]["rate"])  # TODO 通知一次持仓
                        success_n+=1
                    else:
                        new_error_orders.append(old_error_order)
                        self.strategyStatus = True
                        log.warning(f"add order fail,{old_error_order.symbol} {old_error_order.long_short} {old_error_order.open_close} {old_error_order.vol} entrustNo:{old_error_order.entrustNo},errmsg:{rtnExecOrder.errmsg}")
                        self.notifyApi.notify_add_orders_fail(f"{old_error_order.symbol} {old_error_order.long_short} {old_error_order.open_close} {old_error_order.vol} entrustNo:{old_error_order.entrustNo},errmsg:{rtnExecOrder.errmsg}")
        self.ErrorOrders=new_error_orders
        self.cacluAddOrderSpread()
        self.strategyStatus = True
        return success_n
    def update_close_spread_type(self,entrust_no,fixed_spread,update_type):
        self.strategyStatus=False
        with self.order_lock:
            order=self.OrdersDict.get(entrust_no)
        if update_type:
            order.fixedCloseSpread=True
            order.closeSpread=fixed_spread
        else:
            order.fixedCloseSpread=False
            order.closeSpread=util.get_caculate_close_spread(order.realOpenSpread, order.longShort, self.strategyConfig[order.longShort]["base"]["closeSpread"])
        self.updateOrder(order)
        self.strategyStatus=True
    def addOrder(self):
        # 异常委托补单
        for order in self.ErrorOrders:
            success, rtnExecOrder = util.send_order_to_server(order.zmqClient, order.symbol, order.long_short, order.open_close, order.vol, order.entrustNo)
            self.check_send_status(success, rtnExecOrder,f"{order.symbol}  {order.long_short} {order.open_close} order")

            if rtnExecOrder.req_success:
                # 状态累计,故成交成功 状态 +1 即可
                with self.order_lock:
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
            with self.order_lock:
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
                if not pOrder.fixedCloseSpread:
                    if longshort ==comm.ACTION_LONG:
                        pOrder.closeSpread=util.get_caculate_close_spread(realSpread,pOrder.longShort,self.strategyConfig["LONG"]["base"]["closeSpread"])
                    else:
                        pOrder.closeSpread = util.get_caculate_close_spread(realSpread, pOrder.longShort, self.strategyConfig["SHORT"]["base"]["closeSpread"])

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
    def preprocess_market_data(self,ctpMarketData,xauMarketData,usdMarketData):
        long_spread, short_spread = util.get_caculate_long_short_spread(ctpMarketData, xauMarketData, usdMarketData)
        safe,limit_numbers=self.risk.spread_is_safe(long_spread)
        if not safe:
            self.notifyApi.notify_monitor_number_not_in_range("空点差",short_spread,limit_numbers,notify.spread_safety_range_notify_type)
        safe,limit_numbers=self.risk.usd_is_safe(usdMarketData.bidPrice1)
        if not safe:
            self.notifyApi.notify_monitor_number_not_in_range("美元", usdMarketData.bidPrice1, limit_numbers,notify.usd_price_safety_range_notify_type)
        safe,limit_numbers=self.risk.xau_is_safe(xauMarketData.bidPrice1)
        if not safe:
            self.notifyApi.notify_monitor_number_not_in_range("伦敦金", xauMarketData.bidPrice1, limit_numbers,notify.xau_price_safety_range_notify_type)
        safe,errmsg=self.risk.check_price_alert_is_safe( xauMarketData.bidPrice1)
        if not safe:
            self.notifyApi.notify_monitor_market_volatility_above_limit(errmsg)
        self.push_market_data_to_web_socket(long_spread,short_spread)
    def push_market_data_to_web_socket(self, long_spread,short_spread):
        strategy_short = self.get_longshort_strategy(comm.ACTION_SHORT)
        with self.AccountInfos_lock:
            xau_accountInfo=self.AccountInfos_dict.get(strategy_short["op2"]["symbol"],models.AccountInfo())
            usd_accountInfo=self.AccountInfos_dict.get(strategy_short["op3"]["symbol"],models.AccountInfo())
            ctp_accountInfo=self.AccountInfos_dict.get(strategy_short["op1"]["symbol"],models.AccountInfo())
            sum_balance=self.usd_rate*(xau_accountInfo.equity+usd_accountInfo.equity)+ctp_accountInfo.equity
            data={
                "long_spread":round(long_spread, 0),
                "short_spread":round(short_spread, 0),
                "current_xau_equity":round(xau_accountInfo.equity,0),
                "current_usd_equity":round(usd_accountInfo.equity,0),
                "current_ctp_balance":round(ctp_accountInfo.equity,0),
                "current_sum_balance":round(sum_balance,0),
                "total_cost":round(self.total_cost,0),
                "diff_balance":round(sum_balance-self.total_cost,0),
            }
        try:
            self.market_websocket_queue.put_nowait(data)
        except queue.Full:
            self.market_websocket_queue.get_nowait()  # 丢弃旧数据
            self.market_websocket_queue.put_nowait(data)

    def runStrategy(self):
        # self.ctpmdApi.runSubMarket()
        # task = Process(target=self.ctpmdApi.run, args=self.queueCtpMD)
        # task.start()
        log.debug("!!!!!!!!!!!!! strategy get ctp market start !!!!!!!!!!!!!!")
        # 查询行情超时后,丢弃行情开始下一次查询,10次后,再超时等待5分钟再次开始
        first_resend=10
        timeout_wait=60*5
        timeout_notify=60
        timeout_begin_time=time.time()
        log.info("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!time now:{}".format(datetime.datetime.now()))
        while True:
            # 先平,再开

            topic,rspCtpMarket = self.zmqCtpMarket.get_data()
            if topic is None:
                continue
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
                    success1,rsp_XAUUSDm = future1.result()
                    success2,rsp_USDCNHm = future2.result()
                if not (success1 and success2):
                    first_resend -= 1
                    fail_symbol= self.strategyConfig["LONG"]["op2"]['symbol'] if success1 else self.strategyConfig["LONG"]["op3"]["symbol"]
                    log.warning("qry mt5 [{}] market timeout,resend now[{}]".format(fail_symbol,10-first_resend))
                    if first_resend <= 0:
                        time.sleep(timeout_wait)
                    if time.time()-timeout_begin_time>timeout_notify:
                        self.notifyApi.notify_market_timeout("行情超时")
                    continue
                else:
                    first_resend = 10
                timeout_begin_time = time.time()
                # success,rsp_XAUUSDm = util.mt5_api_get_tick_price_from_symbol(self.zmqXAUClient, self.strategyConfig["op2"]['symbol'])
                self.check_send_status(success1 and rsp_XAUUSDm.req_success,rsp_XAUUSDm, "mt5 {} qry tick".format(self.strategyConfig["LONG"]["op2"]['symbol']))

                # success,rsp_USDCNHm = util.mt5_api_get_tick_price_from_symbol(self.zmqUSDClient, self.strategyConfig["op3"]["symbol"])
                self.check_send_status(success2 and rsp_USDCNHm.req_success, rsp_USDCNHm,"mt5 {} qry tick".format(self.strategyConfig["LONG"]["op3"]['symbol']))
                self.preprocess_market_data(ctpMarket,rsp_XAUUSDm.market,rsp_USDCNHm.market)
                # log.info("get market ctp:{} ,mt5-XAUUSDm:{} ,mt5-USDCNHm:{}".format(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market))

                strategy_long = self.get_longshort_strategy(comm.ACTION_LONG)
                strategy_short = self.get_longshort_strategy(comm.ACTION_SHORT)

                # log.info("strategy LONG is {}".format(strategy_long["base"]["isRun"]))
                if strategy_long["base"]["isRun"]:
                    is_trade_time = util.check_is_trade_time(strategy_long["base"]["stopDate"], strategy_long["base"]["stopDateTime"])
                    if is_trade_time is False:
                        log.debug("!!!!!!!!!!!!!!! check time,stopDate:{} ,stopDateTime:{} not trade ".format(strategy_long["base"]["stopDate"],  strategy_long["base"]["stopDateTime"]))
                        continue
                    long_openclose_limit = self.trade_limit[comm.ACTION_LONG]
                    if long_openclose_limit[comm.OFFSET_CLOSE].pre_check():
                        self.checkShouldCloseOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market ,strategy_long)
                    if self.strategy_allow_open:
                        if long_openclose_limit[comm.OFFSET_OPEN].pre_check():
                            self.checkShouldOpenOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market,strategy_long)
                    else:
                        log.info("不允许开仓,保证金或预付款维持比例太低!!!!!!!!!!!")


                # log.info("strategy SHORT is {}".format(strategy_short["base"]["isRun"]))
                if strategy_short["base"]["isRun"]:
                    is_trade_time = util.check_is_trade_time(strategy_short["base"]["stopDate"], strategy_short["base"]["stopDateTime"])
                    if is_trade_time is False:
                        log.debug("!!!!!!!!!!!!!!! check time,stopDate:{} ,stopDateTime:{} not trade ".format(strategy_short["base"]["stopDate"], strategy_short["base"]["stopDateTime"]))
                        continue
                    long_openclose_limit = self.trade_limit[comm.ACTION_SHORT]
                    if long_openclose_limit[comm.OFFSET_CLOSE].pre_check():
                        self.checkShouldCloseOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market,strategy_short)
                    if self.strategy_allow_open:
                        if long_openclose_limit[comm.OFFSET_OPEN].pre_check():
                            self.checkShouldOpenOrder(ctpMarket, rsp_XAUUSDm.market, rsp_USDCNHm.market,strategy_short)
                    else:
                        log.info("不允许开仓,保证金或预付款维持比例太低!!!!!!!!!!!")

    def monitor_account_money(self):
        log.info("监控 账户线程启动~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        strategy_short = self.get_longshort_strategy(comm.ACTION_SHORT)
        allow_open_ctp=True
        allow_open_xau=True
        allow_open_usd=True
        msg=""
        while True:
            topic, accountInfo = self.zmqAccountServer.get_data()
            if topic is None:
                continue
            # log.info("accountInfo:{}".format(accountInfo))
            self.db.upsert_account(accountInfo)
            with self.AccountInfos_lock:
                self.AccountInfos_dict[accountInfo.symbol]=accountInfo
            if accountInfo.symbol==strategy_short["op1"]["symbol"]:
                safe,limit_number=self.risk.ctp_margin_free_is_above_safe(accountInfo.margin_free)
                if not safe:
                    self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol,"可用资金",accountInfo.margin_free,limit_number,notify.ctp_margin_free_notify_type)
                safe,limit_number = self.risk.ctp_margin_free_is_above_stop_trade_safe(accountInfo.margin_free)
                allow_open_ctp=safe
                if not safe:
                    err_msg=f"期货账户资金不足,低于阈值,{accountInfo.margin_free} < {limit_number}"
                    self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol,"可用资金",accountInfo.margin_free,limit_number,notify.ctp_margin_free_stop_trade_notify_type,"停止多空交易")
            if accountInfo.symbol==strategy_short["op2"]["symbol"]:
                if not float_equal(accountInfo.margin_level, 0):
                    safe,limit_number=self.risk.xau_margin_is_above_safe(accountInfo.margin_level)
                    if not safe:
                        self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol,"预付款比例",accountInfo.margin_level,limit_number,notify.xau_margin_level_notify_type)
                    safe,limit_number=self.risk.xau_margin_level_is_above_stop_trade_safe(accountInfo.margin_level)
                    allow_open_xau=safe
                    if not safe:
                        err_msg = f"伦敦金账户资金不足,低于阈值,{accountInfo.margin_level} < {limit_number}"
                        self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol, "预付款比例", accountInfo.margin_level, limit_number, notify.xau_margin_level_stop_trade_notify_type, "停止多空交易")
                safe,limit_number=self.risk.xau_equity_is_above_safe(accountInfo.equity)
                if not safe:
                    self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol,"净值",accountInfo.equity,limit_number,notify.xau_equity_notify_type)

            if accountInfo.symbol==strategy_short["op3"]["symbol"]:
                if not float_equal(accountInfo.margin_level,0):
                    safe,limit_number=self.risk.usd_margin_is_above_safe(accountInfo.margin_level)
                    if not safe:
                        self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol,"预付款比例",accountInfo.margin_level,limit_number,notify.usd_margin_level_notify_type)
                    safe,limit_number=self.risk.usd_margin_level_is_above_stop_trade_safe(accountInfo.margin_level)
                    allow_open_usd=safe
                    if not safe:
                        err_msg = f"伦敦金账户资金不足,低于阈值,{accountInfo.margin_level} < {limit_number}"
                        self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol, "预付款比例", accountInfo.margin_level, limit_number, notify.usd_margin_level_stop_trade_notify_type, "停止多空交易")
                safe,limit_number=self.risk.usd_margin_is_above_safe(accountInfo.equity)
                if not safe:
                    self.notifyApi.notify_monitor_number_is_low_limit(accountInfo.symbol,"净值",accountInfo.equity,limit_number,notify.usd_equity_notify_type)

                # 只要有一方资金不足,均停交易
                if allow_open_ctp and allow_open_usd and allow_open_xau:
                    if not self.strategy_allow_open:
                        log.warning("资金正常,允许交易~~~~~~~~~~~~~~~~~~~~~~~~")
                    self.strategy_allow_open = True
                else:
                    self.strategy_allow_open = False
                    log.warning(f"资金不安全,停止交易!!!!!!!!!!!!!!!!!!!!!!!,{err_msg}")
    def runApi(self):
        self.zmqAccountProxy.run_proxy()
        if not self.long_thread or not self.long_thread.is_alive():
            self.long_thread = threading.Thread(target=self.runStrategy,daemon=True)
            self.long_thread.start()
        if not self.monitor_thread or not self.monitor_thread.is_alive():
            self.monitor_thread=threading.Thread(target=self.monitor_account_money,daemon=True)
            self.monitor_thread.start()
        self.loadOrdersFromDB()
        # self.addOrder()
        # self.cacluAddOrderSpread()
        self.strategyStatus = True


    def notify(self):
        # 检查当前持仓和点差
        pass
