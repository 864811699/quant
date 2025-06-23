import threading
import winsound
import ctypes
import requests
import time

"""
异常逻辑就是：
1 暂停交易
2 弹出需要点击的窗口 "错误信息XXXXXXX \n 如果修复成功再点击确认,重新计算是否异常"，并且播放音频+发送企业微信
3 等待人工修复后， 执行操作 点击-上述弹出窗口  恢复交易
3  程序再次核对
4 确认OK后，程序继续运行, 还是有异常,再次轮询 异常逻辑

"""


def stop_sound():
    winsound.PlaySound(None, winsound.SND_PURGE)


describe_dict={
    "LONG":"做多",
    "SHORT":"做空",
    "OPEN":"开仓",
    "CLOSE":"平仓",
}
lock = threading.Lock()
spread_safety_range_notify_type="spread_safety_range_notify_type"
xau_margin_level_notify_type="xau_margin_level_notify_type"
xau_margin_level_stop_trade_notify_type = "xau_margin_level_stop_trade_notify_type"
usd_margin_level_notify_type = "usd_margin_level_notify_type"
usd_margin_level_stop_trade_notify_type = "usd_margin_level_stop_trade_notify_type"
xau_equity_notify_type ="xau_equity_notify_type"
usd_equity_notify_type = "usd_equity_notify_type"
ctp_margin_free_notify_type = "ctp_margin_free_notify_type"
ctp_margin_free_stop_trade_notify_type = "ctp_margin_free_stop_trade_notify_type"
max_vol_notify_type = "max_vol_notify_type"
usd_price_safety_range_notify_type = "usd_price_safety_range_notify_type"
xau_price_safety_range_notify_type = "xau_price_safety_range_notify_type"
xau_volatility_notify_type = "xau_volatility_time_notify_type"
class Notify(object):
    def __init__(self, url, successAudio, failAudio, mentioned_list ,interval_seconds=300):
        self._url = url
        self._successAudio = successAudio
        self._failAudio = failAudio
        self._mentioned_list = mentioned_list
        self.last_notify_time = {}
        self.interval = interval_seconds
    def _should_notify(self, notify_type: str) -> bool:
        now = time.time()
        last = self.last_notify_time.get(notify_type, 0)
        if now - last >= self.interval:
            self.last_notify_time[notify_type] = now
            return True
        return False

    def _send_wechat(self, content):
        headers = {
            "content-type": "application/json"
        }
        msg = {"msgtype": "text",
               "text": {
                   "content": content,
                   "mentioned_list": self._mentioned_list,
                   "mentioned_mobile_list": self._mentioned_list
               }}  # 发送文本消息27     # 发送请求

        requests.post(self._url, headers=headers, json=msg,proxies={"http": None, "https": None})
        return True

    def _notify_audio(self, audio_file):
        # 播放循环音频
        winsound.PlaySound(audio_file,
                           winsound.SND_FILENAME | winsound.SND_LOOP | winsound.SND_ASYNC)
        threading.Timer(1, stop_sound).start()

    def notify_trade_fail(self,spread, symbol, longshort, openclose, vol, msg):
        notify_msg = f"交易异常！异常信息为: 点差:{spread} {symbol} | {describe_dict.get(longshort,longshort)} | {describe_dict.get(openclose,openclose)} | {vol} | 错误信息 :{msg}"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        # 弹出阻塞式消息框
        ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)

    def notify_exec_error_order_fail(self,spread, symbol, longshort, openclose, vol, msg ,times=0):
        notify_msg = f"交易异常！异常信息为: 点差:{spread} {symbol} | {describe_dict.get(longshort,longshort)} | {describe_dict.get(openclose,openclose)} | {vol} | 错误信息 :{msg}"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        if times>=5:
            ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)
    def notify_trade_success(self):
        self._notify_audio(self._successAudio)

    def notify_trade_part(self, symbol, longshort, openclose, vol, tradedVol):
        notify_msg = f"部分成交,请检查持仓是否对齐, {symbol} | {describe_dict.get(longshort,longshort)} | {describe_dict.get(openclose,openclose)} | 需要交易: {vol},实际交易: {tradedVol} "
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)

    def notify_check_position(self, msg):
        self._send_wechat(msg)
        self._notify_audio(self._failAudio)
        ctypes.windll.user32.MessageBoxW(0, msg, "警告", 0x40 | 0x1)

    def notify_net_error(self, addr):
        notify_msg = f"网络 {addr} 异常！检查该服务是否启动"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        # 弹出阻塞式消息框
        ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)

    def notify_search_order_net_error(self, addr):
        notify_msg = f"查询委托失败 {addr} 异常！检查该服务是否启动"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
    def notify_close_all_order_fail(self,msg):
        notify_msg = f"{msg} 清仓失败， 尽快手动处理！！！！！"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        # 弹出阻塞式消息框
        ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)

    def notify_add_orders_fail(self,msg):
        notify_msg = f"{msg}\n 补单失败， 尽快手动处理！！！！！"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        # 弹出阻塞式消息框
        ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)

    def send_trade_result(self,startSpread,rangeSpread,spread,longShort,openclose,positions):
        notify_msg=(f"交易成功:\n "
                    f"开始点差: {startSpread} | 区间点差: {rangeSpread} | 点差: {spread}\n"
                    f"{describe_dict.get(openclose,openclose)} | {describe_dict.get(longShort,longShort)} | 持仓: {positions}")
        # self._send_wechat(notify_msg)
        self._notify_audio(self._successAudio)
    def notify_start_exe_fail(self,errmsg):
        self._send_wechat(errmsg)
        self._notify_audio(self._successAudio)
        ctypes.windll.user32.MessageBoxW(0, errmsg, "警告", 0x40 | 0x1)
    def notify_monitor_number_not_in_range(self,symbol,number,range_numbers,notify_type):
        if self._should_notify(notify_type):
            msg = f"{symbol} 的数值为 {number} 不在区间{range_numbers} 内"
            self._send_wechat(msg)
            self._notify_audio(self._failAudio)
    def notify_monitor_number_is_low_limit(self,symbol,date_type,current_number,limit ,notify_type,errmsg=""):
        if self._should_notify(notify_type):
            msg = f"{symbol}的 {date_type} 为 {current_number} 小于 {limit}  "+errmsg
            self._send_wechat(msg)
            self._notify_audio(self._failAudio)
    def notify_monitor_market_volatility_above_limit(self,msg):
        if self._should_notify(xau_volatility_notify_type):
            self._send_wechat(msg)
            self._notify_audio(self._failAudio)
    def notify_monitor_positions_above_limit(self,current_positions,max_positions,longshort):
        if self._should_notify(max_vol_notify_type):
            msg=f"{describe_dict[longshort]} 持仓 {current_positions} 已达最大持仓 {max_positions}"
            self._send_wechat(msg)
            self._notify_audio(self._failAudio)
    def notify_market_timeout(self,msg):
        self._send_wechat(msg)
        self._notify_audio(self._failAudio)
