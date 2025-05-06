from datetime import datetime, timedelta,time

from src.core import comm
from package.zmq import models

import logging

from package.logger.logger import setup_logger

log = logging.getLogger('root')



def get_caculate_close_spread(open_spread,base_spread,range_spread):
    # 平仓逻辑为 逼近基准点差时平仓
    return open_spread-range_spread if open_spread>base_spread else open_spread+range_spread


def get_caculate_spread_from_price(ctpP, mt5P, rateP):
    if ctpP == 0 or mt5P == 0 or rateP == 0:
        return 0
    return 100*(ctpP - mt5P / 31.1035 * rateP)


def get_caculate_spread(ctpP, mt5P, rateP, action):
    # 计算点差值
    if action == comm.ACTION_LONG:
        return get_caculate_spread_from_price(ctpP.bidPrice1,mt5P.askPrice1,rateP.askPrice1)
    else:
        return get_caculate_spread_from_price(ctpP.askPrice1,mt5P.bidPrice1,rateP.bidPrice1)

def get_caculate_long_short_spread(ctpP, mt5P, rateP):
    # 计算点差值 多,空
    long_spread = get_caculate_spread_from_price(ctpP.bidPrice1,mt5P.askPrice1,rateP.askPrice1)
    short_spread = get_caculate_spread_from_price(ctpP.askPrice1,mt5P.bidPrice1,rateP.bidPrice1)
    return long_spread,short_spread


#______________________________________________________________________________________


def is_open_long(ctpP, mt5P, rateP, base, range):
    # 检查是否需要开多仓,区间值倍数
    spread = get_caculate_spread(ctpP, mt5P, rateP, comm.ACTION_LONG)
    vol = int((base - spread) / range)
    if vol >= 1:
        return True, vol, spread
    return False, 0, spread


def is_open_short(ctpP, mt5P, rateP, base, range):
    # 检查是否需要开空,返回: 区间值倍数 ,点差
    spread = get_caculate_spread(ctpP, mt5P, rateP, comm.ACTION_SHORT)
    vol = int((spread - base) / range)
    if vol >= 1:
        return True, vol, spread
    return False, 0, spread

def should_open_order_longshort(ctpP, mt5P, rateP, base, range,longshort):
    spread=0.0
    if longshort==comm.ACTION_SHORT:
        is_open, vol, spread = is_open_short(ctpP, mt5P, rateP, base, range)
        if is_open:
            return is_open, vol,  spread

    if longshort==comm.ACTION_LONG:
        is_open, vol, spread = is_open_long(ctpP, mt5P, rateP, base, range)
        if is_open:
            return is_open, vol,  spread
    return False, 0,  spread

def should_open_order(ctpP, mt5P, rateP, base, range):
    # 是否需要开仓,返回 bool,区间值倍数,多/空 ,点差
    is_open, vol, short_spread = is_open_short(ctpP, mt5P, rateP, base, range)
    if is_open:
        return is_open, vol, comm.ACTION_SHORT, short_spread
    is_open, vol, long_spread = is_open_long(ctpP, mt5P, rateP, base, range)
    if is_open:
        return is_open, vol, comm.ACTION_SHORT, long_spread
    return False, 0, "", [short_spread,long_spread]

def should_close_order(ctpP, mt5P, rateP, pOrder):
    # 是否需要平仓
    # 当前点差<=平仓点差  平空
    # 当前点差>=平仓点差  平多
    spread = get_caculate_spread(ctpP, mt5P, rateP, pOrder.longShort)
    if pOrder.longShort == comm.ACTION_SHORT and pOrder.closeSpread >= spread:
        return True, spread
    if pOrder.longShort == comm.ACTION_LONG and pOrder.closeSpread <= spread:
        return True, spread
    return False, spread


def check_time_is_valid(t):
    current_time = datetime.now()
    time_difference = abs(current_time - t)
    if time_difference <= timedelta(seconds=2):
        return True, current_time
    return False, current_time

def time_is_trade_time(now):
    """判断当前是否是上期所黄金交易时间（含夜盘）"""
    # 交易时间段定义
    sessions = [
        (time(9, 0,5), time(10, 14,40)),   # 白盘上午
        (time(10, 15,5), time(11, 29,40)),   # 白盘上午
        (time(13, 30,5), time(14, 59,40)),  # 白盘下午
        (time(21, 0,5), time(23, 59, 59)), # 夜盘当天
        (time(0, 0,0), time(2, 29,40))     # 夜盘次日凌晨
    ]

    now_time = now.time()

    # 检查是否在交易时间段内
    for start, end in sessions:
        if start <= now_time <= end:
            return True
    return False

def check_is_trade_time(stopDate,  stopDateTime):
    now = datetime.now()
    if not time_is_trade_time(now):
        return False

    nowDate = now.strftime('%Y-%m-%d')
    nowDateTime = now.strftime('%Y-%m-%dT%H:%M:%S')

    if nowDate >= stopDate[0] and nowDate <= stopDate[1]:
        return False
    if nowDateTime >= stopDateTime[0] and nowDateTime <= stopDateTime[1]:
        return False
    return True


def get_longShort_from_ctp_longShort(ctp_longShort):
    return comm.ACTION_SHORT if ctp_longShort == comm.ACTION_LONG else comm.ACTION_LONG




def get_trade_vol_from_order(orders):
    # 获取成交次数
    n = 0
    for order in orders:
        if order.status == 4:
            n += 1
    return n


def qry_child_order_from_pid(c, pid):
    # 通过PID查询委托
    order = models.Request()
    order.request_type = models.REQ_SEARCH
    order.pid = pid
    msg = order.to_json()
    success, response = c.request(message=msg)
    return success, response

def send_order_to_server(c,symbol,longShort,openClose,vol,pid):
    # 发送请求到 API端
    order = models.Request()
    order.request_type = models.REQ_ORDER
    order.symbol=symbol
    order.longShort=longShort
    order.openClose=openClose
    order.pid=pid
    order.volume=vol
    msg = order.to_json()
    log.info("send request: {}".format(order))
    success, response = c.request(message=msg)
    return success, response

def sed_close_all_to_server(c,symbol,pid):
    # 发送请求到 API端
    order = models.Request()
    order.request_type = models.REQ_LIQUIDATE
    order.symbol=symbol
    order.pid=pid
    msg = order.to_json()
    success, response = c.request(message=msg)
    return success, response


def mt5_api_get_tick_price_from_symbol(c,symbol):
    order = models.Request()
    order.request_type = models.REQ_MARKET
    order.symbol=symbol
    msg = order.to_json()
    success, response = c.request(message=msg)
    return success, response


def float_equal(a, b, tol=1e-4):
    return abs(a - b) <= tol


def caclu_ask_qty_and_traded_qty(orders,status,parent_ask_qty):
    #   开仓阶段校验  /平仓阶段校验
    #   平仓 非终态 5<=status<8  ,校验平常
    #   开仓 非终态 1<status<3   ,校验开仓
    traded_qty=0
    openClose=comm.OFFSET_OPEN if status<3 else comm.OFFSET_CLOSE
    for order in orders:
        if status<3 and status>=1 and  order.openClose==comm.OFFSET_OPEN:
            traded_qty+=order.bidVol
        if status>5 and status<8 and order.openClose==comm.OFFSET_CLOSE:
            traded_qty += order.bidVol
    diff_qty=parent_ask_qty-traded_qty
    return float_equal(diff_qty,0),diff_qty,openClose

def get_open_trade_bid_price(orders):
    for child in orders:
        if child.status == models.AllTrade and child.openClose==models.TRADE_TYPE_OPEN:
            return child.bidPrice
    return 0

def get_close_trade_bid_price(orders):
    for child in orders:
        if child.status == models.AllTrade and child.openClose==models.TRADE_TYPE_CLOSE:
            return child.bidPrice
    return 0

def get_err_orders(c,porder,parent_ask_qty,symbol,longshort,status):
    # 检查子母单状态,看是否需要 +1
    success, rsp = qry_child_order_from_pid(c, porder.entrustNo)
    error_orders=[]
    completeOrder, diff, open_close = caclu_ask_qty_and_traded_qty(rsp.orders, status,parent_ask_qty)
    if not completeOrder:
        order = comm.ErrorOrder(zmqClient=c, symbol=symbol, long_short=longshort, open_close=open_close, vol=diff, entrustNo=porder.entrustNo)
        error_orders.append(order)

    return success,rsp.orders,error_orders

def get_porder_status_from_child_order(orders):
    status=comm.PARENT_STATUS_UNKWON


    return status

def get_porder_openclose_from_ctp(c,porder):
    # 母单是否开平仓以CTP是否开平仓为依据,所以CTP不用补单
    status = comm.PARENT_STATUS_UNKWON
    success, rsp = qry_child_order_from_pid(c, porder.entrustNo)
    if not success:
        return success ,rsp,status
    if len(rsp.orders)==0:
        status=comm.PARENT_STATUS_OPEN_FAIL
    elif len(rsp.orders)==1:
        status=comm.PARENT_STATUS_OPEN_CTP
    else:
        status=comm.PARENT_STATUS_CLOSE_CTP
    return success,rsp.orders,status