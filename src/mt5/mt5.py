import time

import MetaTrader5 as mt5
import os
import sys
from datetime import datetime
from src.mt5 import comm
from src.mt5 import utils
from package.zmq import models

import logging
from package.logger.logger import setup_logger

log = logging.getLogger('root')

# Python Self-Defined Packages
pwd = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, pwd + '/../')
# 显示有关MetaTrader 5程序包的数据
log.info("MetaTrader5 package version: {}".format(mt5.__version__))


class Mt5Api:
    def __init__(self, path, account, pwd, broker_host, order_type_filling):
        self.path = path
        self.account = account
        self.pwd = pwd
        self.broker_host = broker_host
        self.order_type_filling = order_type_filling

    def run(self):
        if not mt5.initialize(path=self.path):
            log.error("mt5initialize() failed, error code ={}".format(mt5.last_error()))
            sys.exit(-1)
        else:
            log.info("mt5 init success!!!")

        authorized = mt5.login(self.account, password=self.pwd, server=self.broker_host)
        if authorized:
            account_info = mt5.account_info()
            if account_info != None:
                log.info("mt5 login  success #{}".format(account_info))
        else:
            log.error("failed to connect at account #{}, error code: {}".format(self.account, mt5.last_error()))
            sys.exit(-1)

    def get_tick_price_from_symbol(self, symbol):
        # 获取行情 外汇 几秒更新/ 伦敦金1秒更新
        # models MqlTick
        # datetime     time;          // 价格更新的最近时间
        # double       bid;           // 当前卖价
        # double       ask;           // 当前买价
        # double       last;          // 最后交易的价格(Last)
        # ulong        volume;        // 当前最后价格的交易量
        # long         time_msc;      // 价格最后一次更新的时间，以毫秒计算
        # uint         flags;         // 报价标识
        # double       volume_real;   // 精确度更高的当前最后价格的交易量
        symbol_info = mt5.symbol_info_tick(symbol)
        if symbol_info == None:
            log.warning("mt5 symbol_info_tick fail,{} ,error:{} ".format(symbol, mt5.last_error()))
            return None
        log.debug("mt5 md [{}] ask:{},bid:{}".format(symbol, symbol_info.ask, symbol_info.bid))
        md = comm.RtnRsp()
        md.req_success = True
        market = models.Market()
        market.askPrice1 = symbol_info.ask
        market.bidPrice1 = symbol_info.bid
        market.instrumentID = symbol
        md.market = market
        return md

    def sendOrder(self, action, symbol, lot, side, magic, comment, order_type_filling, position=''):
        # 填写了position的为平仓  comment: open/close-entrustno
        r = {
            "action": action,  # 写死市价即可
            "symbol": symbol,
            "volume": lot,
            "type": side,
            "magic": magic,  # EA ID  用作 mt5 每次执行的策略号
            "comment": comment,
            "type_filling": order_type_filling,
        }
        if position != '':
            r["position"] = position
        rtn = mt5.order_send(r)
        if rtn == None:
            log.error("send order fail,error={}".format(mt5.last_error()))
        # OrderSendResult(retcode=10013, deal=0, order=0, volume=0.0, price=0.0, bid=0.0, ask=0.0, comment='Invalid request', request_id=0, retcode_external=0,
        # request=TradeRequest(action=1, magic=1, order=0, symbol='XAUUSDm', volume=0.01, price=0.0, stoplimit=0.0, sl=0.0, tp=0.0, deviation=0, type=0, type_filling=0, type_time=0, expiration=0, comment='1', position=382841937, position_by=0))
        # TradePosition(ticket=382841937, type=0, magic=1, identifier=382841937, reason=3, volume=0.01, price_open=2911.637,  price_current=2934.834, , profit=23.19, symbol='XAUUSDm', comment='3', external_id='')
        return rtn
        # OrderSendResult(retcode=10009,  回执码  10009已成
        # deal=223587875,  成交号
        # order=382821397, 订单号
        # volume=0.01,   成交量
        # price=2911.723,  成交价
        # bid=2911.563,  当前买入价
        # ask=2911.723,  当前卖出价
        # comment='1',  注释代码
        # request_id=1287740709,
        # retcode_external=0,
        # request=TradeRequest(action=1, magic=1, order=0, symbol='XAUUSDm', volume=0.01, price=0.0, stoplimit=0.0, sl=0.0, tp=0.0, deviation=0, type=0, type_filling=0, type_time=0, expiration=0, comment='1', position=0, position_by=0))

    def reCloseOrder(self, order):
        position = self.getPositionID(order.pEntrustNo, order.symbol, order.longShort)
        result = self.sendOrder(comm.ACTION, order.symbol, order.askQty, comm.getSide(order.longShort, comm.TRADE_TYPE_CLOSE), order.pEntrustNo, str(order.entrustNo), self.order_type_filling, position.ticket)
        return result

    def getTradeInfo(self, orderId):
        while True:
            positions = mt5.positions_get(ticket=orderId)
            if len(positions) == 1:
                return True, positions[0].volume, positions[0].price_open
            if positions == None:
                return False, 0, 0
            time.sleep(0.05)


    def ExecOrder(self, order):
        RtnRsp = comm.RtnRsp()
        result = None
        if order.openClose == comm.TRADE_TYPE_CLOSE:
            isClosed = False
            while not isClosed:
                # 平仓触发仓位已平，则重新平
                # if result is None or result.retcode == mt5.TRADE_RETCODE_POSITION_CLOSED:
                result = self.reCloseOrder(order)
                if result is not None and result.retcode != mt5.TRADE_RETCODE_POSITION_CLOSED:
                    isClosed = True
                    continue
                if result is None:
                    order.status = comm.ORDER_STATUS_REJECTED
                    order.statusMsg = "sendOrder fail,code {}".format(mt5.last_error())
                    isClosed = True

        elif order.openClose == comm.TRADE_TYPE_OPEN:
            result = self.sendOrder(comm.ACTION, order.symbol, order.askQty, comm.getSide(order.longShort, comm.TRADE_TYPE_OPEN), order.pEntrustNo, str(order.entrustNo), self.order_type_filling, "")
            if result is None:
                order.status = comm.ORDER_STATUS_REJECTED
                order.statusMsg = "sendOrder fail,code {}".format(mt5.last_error())
        if result is not None:
            log.info(f"mt5 get entrustNo:{order.entrustNo},pId:{order.pEntrustNo} trade result {result}")
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                order.status = comm.ORDER_STATUS_AllTrade
                order.orderSysID = str(result.order)
                order.statusMsg = "全部成交"
                order.bidVol = result.volume
                order.bidPrice = result.price
                if utils.float_equal(result.volume, 0) or utils.float_equal(result.price, 0):
                    if order.openClose == comm.TRADE_TYPE_OPEN:
                        success, volume, price = self.getTradeInfo(result.order)
                        if success:
                            order.bidVol = volume
                            order.bidPrice = price
                        else:
                            log.error(f"entrustNo:{order.entrustNo},pId:{order.pEntrustNo},orderID:{result.order} qry trade info fail")
                            time.sleep(2)
                            exit(-2)

                    else:
                        order.bidVol = order.askQty
                        mkt = self.get_tick_price_from_symbol(order.symbol)
                        order.bidPrice = mkt.market.askPrice1 if order.openClose == comm.ACTION_SHORT else mkt.market.bidPrice1
                RtnRsp.req_success = True
            elif result.retcode == mt5.TRADE_RETCODE_DONE_PARTIAL:
                order.status = comm.ORDER_STATUS_PARTTRADE
                order.orderSysID = str(result.order)
                order.statusMsg = "部分成交"
                order.bidVol = result.volume
                order.bidPrice = result.price
                RtnRsp.req_success = True
            else:
                order.status = comm.ORDER_STATUS_REJECTED
                order.statusMsg = "拒单,code {}".format(result.retcode)
                RtnRsp.req_success = False
            order.orderSysID = result.order

        order.rspTime = datetime.now()
        RtnRsp.order = order
        log.info("mt5 order entrustNo:{} ,magic:{} ,symbol:{} ,vol:{} ,price:{} ,status:{} ,msg:{},".format(order.entrustNo, order.pEntrustNo, order.symbol, order.bidVol, order.bidPrice, order.status, order.statusMsg))

        return RtnRsp

    # def ExecChildOrder(self, order):
    #     # 作为一个执行策略,再次生成多个子单执行
    #     RtnRsp = comm.RtnRsp()
    #     result = None
    #     if order.openClose == comm.TRADE_TYPE_CLOSE:
    #         isClosed = False
    #         while not isClosed:
    #             # 平仓触发仓位已平，则重新平
    #             if result is None or result.retcode == mt5.TRADE_RETCODE_POSITION_CLOSED:
    #                 result = self.reCloseOrder(order)
    #                 if result is not None and result.retcode != mt5.TRADE_RETCODE_POSITION_CLOSED:
    #                     isClosed=True
    #                 if result is None:
    #                     order.status = comm.ORDER_STATUS_REJECTED
    #                     order.statusMsg = "sendOrder fail,code {}".format(mt5.last_error())
    #                     isClosed = True
    #
    #
    #     elif order.openClose == comm.TRADE_TYPE_OPEN:
    #         result = self.sendOrder(comm.ACTION, order.symbol, order.askQty,
    #                                 comm.getSide(order.longShort, comm.TRADE_TYPE_OPEN),
    #                                 order.pEntrustNo,
    #                                 str(order.entrustNo), self.order_type_filling, "")
    #         if result is None:
    #             order.status = comm.ORDER_STATUS_REJECTED
    #             order.statusMsg = "sendOrder fail,code {}".format(mt5.last_error())
    #     if result is not None:
    #         if result.retcode == mt5.TRADE_RETCODE_DONE:
    #             order.status = comm.ORDER_STATUS_AllTrade
    #             order.orderSysID = str(result.order)
    #             order.statusMsg = "全部成交"
    #             order.bidVol = result.volume
    #             order.bidPrice = result.bidPrice
    #             RtnRsp.req_success = True
    #         elif result.retcode == mt5.TRADE_RETCODE_DONE_PARTIAL:
    #             order.status = comm.ORDER_STATUS_PARTTRADE
    #             order.orderSysID = str(result.order)
    #             order.statusMsg = "部分成交"
    #             order.bidVol = result.volume
    #             order.bidPrice = result.bidPrice
    #             RtnRsp.req_success = True
    #         else:
    #             order.status = comm.ORDER_STATUS_REJECTED
    #             order.statusMsg = "拒单,code {}".format(result.retcode)
    #             RtnRsp.req_success = False
    #         order.orderSysID = result.order
    #
    #     order.rspTime = datetime.now()
    #     RtnRsp.order = order
    #     log.info(
    #         "mt5 order entrustNo:{} ,magic:{} ,symbol:{} ,vol:{} ,price:{} ,status:{} ,msg:{},".format(
    #             order.entrustNo, order.pEntrustNo, order.symbol, order.bidVol, order.bidPrice, order.status,
    #             order.statusMsg))
    #
    #     return RtnRsp

    def getPostions(self):
        # 返回的是元组 tuple
        # TradePosition(ticket=341998127,  //持仓ID
        # type=0,
        # magic=0,
        # identifier=341998127,
        # reason=0,
        # volume=0.01,
        # price_open=2596.504,
        # sl=0.0, tp=0.0, price_current=2627.112, swap=0.0, profit=30.61,
        # symbol='XAUUSDm', comment='', external_id=''),

        positions = mt5.positions_get()
        if positions == None:
            log.info("mt5 No positions on all, error code={}".format(mt5.last_error()))
        elif len(positions) > 0:
            return positions

    def getPositionID(self, magic, symbol, longShort):
        side = mt5.ORDER_TYPE_BUY if longShort == comm.ACTION_LONG else mt5.ORDER_TYPE_SELL
        positions = self.getPostions()
        for position in positions:
            if position.magic == magic and position.symbol == symbol and position.type == side:
                return position

    def getPositionsFromSymbol(self, symbol, longShort):
        side = mt5.ORDER_TYPE_BUY if longShort == comm.ACTION_LONG else mt5.ORDER_TYPE_SELL
        positions = self.getPostions()
        return [pos for pos in positions if pos.symbol == symbol and pos.type == side]

    def getPositionsVolumeFromSymbol(self, symbol, longshort):
        positions = self.getPositionsFromSymbol(symbol, longshort)
        return sum(pos.volume for pos in positions)

    def getHistoryOrders(self, orderID):
        orderSysID = int(orderID)
        rtn = mt5.history_orders_get(ticket=orderSysID)
        if rtn == None:
            return False, mt5.last_error(), None
        return True, "", rtn


def getOrders(symbol):
    # 只返回 没终态的委托 数组 for order in orders:
    # (TradeOrder(
    # ticket=344970050,
    # time_setup=1735182586,
    # time_setup_msc=1735182586250,
    # time_done=0, time_done_msc=0,
    # time_expiration=0,
    # type=2,
    # type_time=0,
    # type_filling=2,
    # state=1,
    # magic=0,
    # position_id=0,mt5
    # position_by_id=0,
    # reason=0,
    # volume_initial=0.1,
    # volume_current=0.1,
    # price_open=2627.0,
    # sl=0.0, tp=0.0,
    # price_current=2628.321,
    # price_stoplimit=0.0,
    # symbol='XAUUSDm',
    # comment='',
    # external_id=''),
    # TradeOrder(ticket=344970234, time_setup=1735182601, time_setup_msc=1735182601886, time_done=0, time_done_msc=0, time_expiration=0, type=2, type_time=0, type_filling=2, state=1, magic=0, position_id=0, position_by_id=0, reason=0, volume_initial=0.1, volume_current=0.1, price_open=2627.0, sl=0.0, tp=0.0, price_current=2628.321, price_stoplimit=0.0, symbol='XAUUSDm', comment='', external_id=''))
    # 返回的是元组 tuple
    orders = mt5.orders_get(symbol=symbol)
