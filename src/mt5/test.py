import datetime

import MetaTrader5 as mt5
from src.mt5 import mt5 as api
account =7333022
pwd =""
broker_host ="ICMarketsSC-MT5-2"
path=1
side={'buy':mt5.ORDER_TYPE_BUY,'sell':mt5.ORDER_TYPE_SELL}

symbol= 'USDCNH'
action= mt5.TRADE_ACTION_DEAL  #市价委托
magic = 1
lot =0.01
timeType=mt5.ORDER_TIME_DAY  # 当前交易日有效
orderType=side['buy']
filling_type=mt5.ORDER_FILLING_IOC  # 立即成交,剩余撤销
entrust_no=0

if __name__ == '__main__':
    a=api.Mt5Api(path,account,pwd,broker_host,0)
    a.run()
    account_info=mt5.account_info()
    if account_info!=None:
        print(f"balance:{account_info.balance}")
        print(f"profit:{account_info.profit}")
        print(f"equity:{account_info.equity}")
        print(f"margin:{account_info.margin}")
        print(f"margin_free:{account_info.margin_free}")
        print(f"margin_level:{account_info.margin_level}")

    #                action, symbol, lot, side, magic, comment, order_type_filling, position=''
    rt=mt5.order_send(action, symbol, lot, side, magic, entrust_no,     filling_type, "")
    if rt is None:
        print(mt5.last_error())
    print(rt)

 # balance=99511.4：账户余额（不含当前持仓盈亏）
# profit=41.82：当前持仓总浮动盈亏
# equity=99553.22：净值（余额+浮动盈亏）
# credit=0.0：信用额度（无负债）
# 保证金参数
# leverage=100：杠杆比例1:100
# margin=98.18：已用保证金金额
# margin_free=99455.04：可用保证金
# margin_level=101398.67：保证金水平百分比（净值/保证金*100%）
# margin_so_call=50.0：保证金追加警戒线比例
# margin_so_so=30.0：强制平仓线比例


    # print(a.getHistoryOrders("123"))
    #action, symbol, lot, side, magic, comment, position=''

    # a.closeOrder(magic)
    # a.