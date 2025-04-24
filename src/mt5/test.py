import MetaTrader5 as mt5
from src.mt5 import mt5 as api
account =76884201
pwd = 'Aa123456@'
broker_host = 'Exness-MT5Trial5'
path='D:\\app\\MT5_EX\\terminal64.exe'
side={'buy':mt5.ORDER_TYPE_BUY,'sell':mt5.ORDER_TYPE_SELL}

symbol= 'USDCNH'
action= mt5.TRADE_ACTION_DEAL  #市价委托
magic = 1
lot =0.01
timeType=mt5.ORDER_TIME_DAY  # 当前交易日有效
orderType=side['buy']
filling_type=mt5.ORDER_FILLING_IOC  # 立即成交,剩余撤销
entrust_no="0"

if __name__ == '__main__':
    a=api.Mt5Api(path,account,pwd,broker_host,0)
    a.run()
    # print(a.getHistoryOrders("123"))
    #action, symbol, lot, side, magic, comment, position=''

    rtn=a.sendOrder(action,symbol,lot,side['sell'],magic,entrust_no,0)
    print(rtn)
    # a.closeOrder(magic)
    # a.