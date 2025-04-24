import threading
import winsound
import ctypes
import requests

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


class Notify(object):
    def __init__(self, url, successAudio, failAudio, mentioned_list):
        self._url = url
        self._successAudio = successAudio
        self._failAudio = failAudio
        self._mentioned_list = mentioned_list

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

        requests.post(self._url, headers=headers, json=msg)
        return True

    def _notify_audio(self, audio_file):
        # 播放循环音频
        winsound.PlaySound(audio_file,
                           winsound.SND_FILENAME | winsound.SND_LOOP | winsound.SND_ASYNC)
        threading.Timer(1, stop_sound).start()

    def notify_trade_fail(self,spread, symbol, longshort, openclose, vol, msg):
        notify_msg = f"交易异常！异常信息为: spread:{spread} {symbol} | {longshort} | {openclose} | {vol} | fail msg :{msg}"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        # 弹出阻塞式消息框
        ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)

    def notify_trade_success(self):
        self._notify_audio(self._successAudio)

    def notify_trade_part(self, symbol, longshort, openclose, vol, tradedVol):
        notify_msg = f"部分成交,请检查持仓是否对齐, {symbol} | {longshort} | {openclose} | need to trade {vol},real trade {tradedVol} "
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

    def notify_close_all_order_fail(self,msg):
        notify_msg = f"{msg} 清仓失败， 尽快手动处理！！！！！"
        self._send_wechat(notify_msg)
        self._notify_audio(self._failAudio)
        # 弹出阻塞式消息框
        ctypes.windll.user32.MessageBoxW(0, notify_msg, "警告", 0x40 | 0x1)
