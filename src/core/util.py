from datetime import datetime, timedelta

from src.core import comm
from src.mt5 import comm as mt5comm
from package.zmq import models


def get_caculate_spread(ctpP, mt5P, rateP, action):
    # 计算点差值
    if action == comm.ACTION_LONG:
        return ctpP.bidPrice1 - mt5P.askPrice1 / 31.1035 * rateP.askPrice1
    else:
        return ctpP.askPrice1 - mt5P.bidPrice1 / 31.1035 * rateP.bidPrice1


def get_caculate_spread_from_price(ctpP, mt5P, rateP):
    if ctpP == 0 or mt5P == 0 or rateP == 0:
        return 0
    return ctpP - mt5P / 31.1035 * rateP


def get_caculate_close_spread(spread, longShort, closeRangeSpread):
    if longShort == comm.ACTION_LONG:
        return spread - closeRangeSpread
    if longShort == comm.ACTION_SHORT:
        return spread + closeRangeSpread


def is_open_long(ctpP, mt5P, rateP, base, range):
    # 检查是否需要开多仓,区间值倍数
    spread = get_caculate_spread(ctpP, mt5P, rateP, comm.ACTION_LONG)
    vol = int((base - spread) / range)
    if vol >= 1:
        return True, vol, spread
    return False, 0, 0


def is_open_short(ctpP, mt5P, rateP, base, range):
    # 检查是否需要开空,返回: 区间值倍数 ,点差
    spread = get_caculate_spread(ctpP, mt5P, rateP, comm.ACTION_SHORT)
    vol = int((spread - base) / range)
    if vol >= 1:
        return True, vol, spread
    return False, 0, 0


def should_open_order(ctpP, mt5P, rateP, base, range):
    # 是否需要开仓,返回 bool,区间值倍数,多/空 ,点差
    is_open, vol, spread = is_open_short(ctpP, mt5P, rateP, base, range)
    if is_open:
        return is_open, vol, comm.ACTION_SHORT, spread
    is_open, vol, spread = is_open_long(ctpP, mt5P, rateP, base, range)
    if is_open:
        return is_open, vol, comm.ACTION_SHORT, spread
    return False, 0, "", 0


def should_close_order(ctpP, mt5P, rateP, pOrder):
    # 是否需要平仓
    spread = get_caculate_spread(ctpP, mt5P, rateP, pOrder.longShort)
    if pOrder.longShort == comm.ACTION_SHORT and pOrder.closeSpread <= spread:
        return True, spread
    if pOrder.longShort == comm.ACTION_LONG and pOrder.closeSpread >= spread:
        return True, spread
    return False, 0


def check_time_is_valid(t):
    current_time = datetime.now()
    time_difference = abs(current_time - t)
    if time_difference <= timedelta(seconds=2):
        return True, current_time
    return False, current_time


# def mt5_api_exec_order(mt5ApiConn,symbol,entrustNo,longShort,openClose,rate):
#     req = mt5comm.Request()
#     req.reqType = mt5comm.REQ_ORDER
#     req.symbol = symbol
#     req.longShort = longShort
#     req.openClose = openClose
#     req.magic = entrustNo
#     req.volume = rate
#     mt5ApiConn.send(req)
#     # if mt5ApiConn.poll():
#     rsp = mt5ApiConn.recv()
#     return rsp

# def mt5_api_get_tick_price_from_symbol(mt5ApiConn,symbol):
#     req = mt5comm.Request()
#     req.reqType = mt5comm.REQ_MARKET
#     req.symbol = symbol
#     mt5ApiConn.send(req)
#     rsp = mt5ApiConn.recv()
#     return rsp
#
# def mt5_get_finished_order_from_pid(mt5ApiConn,pid):
#     req = mt5comm.Request()
#     req.reqType = mt5comm.REQ_SEARCH
#     req.pid = pid
#     mt5ApiConn.send(req)
#     rsp = mt5ApiConn.recv()
#     return rsp

def check_is_trade_time(stopDate, stopTime, stopDateTime):
    now = datetime.now()
    nowDate = now.strftime('%Y/%m/%d')
    nowTime = now.strftime('%H:%M:%S')
    nowDateTime = now.strftime('%Y/%m/%d %H:%M:%S')

    if nowDate >= stopDate[0] and nowDate <= stopDate[1]:
        return False
    if nowTime >= stopTime[0] and nowTime <= stopTime[1]:
        return False
    if nowDateTime >= stopDateTime[0] and nowDateTime <= stopDateTime[1]:
        return False
    return True


def get_longShort_from_ctp_longShort(ctp_longShort):
    return comm.ACTION_SHORT if ctp_longShort == comm.ACTION_LONG else comm.ACTION_LONG


# def mt5_api_close_all_position(mt5ApiConn,symbol,magic):
#     req = mt5comm.Request()
#     req.reqType = mt5comm.REQ_LIQUIDATE
#     req.symbol=symbol
#     req.magic=magic
#     mt5ApiConn.send(req)
#     rsp = mt5ApiConn.recv()
#     return rsp

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

def float_equal(a, b, tol=1e-4):
    return abs(a - b) <= tol