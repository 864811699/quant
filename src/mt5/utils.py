from datetime import datetime, timedelta


def getLocalTimeFromMilliseconds(timestamp_ms ) :
    timestamp_sec = timestamp_ms / 1000
    dt_local = datetime.fromtimestamp(timestamp_sec)
    return dt_local


def float_equal(a, b, tol=1e-4):
    return abs(a - b) <= tol
def to_mt5_time(time_diff_hours):
    return datetime.now() - timedelta(hours=time_diff_hours)
def get_mt5_last_hours(qry_time_hours=16):
    now = datetime.now()
    start_time = now - timedelta(hours=qry_time_hours)
    end_time = now + timedelta(hours=qry_time_hours)
    return start_time,end_time

error_code_to_discribe=    {10004:    "报价请求",
    10006:    "拒绝请求",
    10007:    "交易者取消请求",
    10008:    "安置命令",
    10009:    "完成要求",
    10010:    "请求部分完成",
    10011:    "请求处理错误",
    10012:    "超时取消请求",
    10013:    "无效请求",
    10014:    "请求中无效成交量",
    10015:    "请求中的无效价格",
    10016:    "请求中的无效访问",
    10017:    "关闭交易",
    10018:    "收市",
    10019:    "没有足够的钱实现请求",
    10020:    "改变价格",
    10021:    "没有报价处理请求",
    10022:    "请求中的无效命令截止日期",
    10023:    "改变命令状态",
    10024:    "太频繁的请求",
    10025:    "不改变请求",
    10026:    "服务器无效自动交易",
    10027:    "客户端无效自动交易",
    10028:    "处理锁住请求",
    10029:    "命令或安置冻结",
    10030:    "无效命令填满字节",
    10031:    "与服务器无连接",
    10032:    "只在流水账允许操作",
    10033:    "待办订单数量达到限制",
    10034:    "订单成交量和交易品种位置达到限制",
    10035:    "错误或禁止的订单类型",
    10036:    "指定POSITION_IDENTIFIER的持仓已经被关闭",
    10038:    "平仓交易量超出当前持仓交易量"}
def discribe_error_code(code):
    return f"code:[{code}],"+error_code_to_discribe[code]
