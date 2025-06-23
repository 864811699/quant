import requests
import json
import time
import winsound
import threading

from package.db import db


def float_compare(a: float, b: float, eps: float = 1e-8) -> int:
    """
    比较两个浮点数 a 和 b
    :param a: 第一个数
    :param b: 第二个数
    :param eps: 允许误差范围
    :return:
        -1 if a < b
         0 if a ≈ b（在误差范围内认为相等）
         1 if a > b
    """
    if abs(a - b) < eps:
        return 0
    elif a < b:
        return -1
    else:
        return 1

def float_equal(a: float, b: float, eps: float = 1e-8) -> bool:
    return abs(a - b) < eps

def float_greater(a: float, b: float, eps: float = 1e-8) -> bool:
    return a - b > eps

def float_less(a: float, b: float, eps: float = 1e-8) -> bool:
    return b - a > eps


with open("../etc/monitor.json") as f:
    configs = json.load(f)

dbServer=db.dbServer(configs["db"])


def _send_wechat(content):
    headers = {"content-type": "application/json"}
    msg = {"msgtype": "text", "text": {"content": content, "mentioned_list": "", "mentioned_mobile_list": ""}}  # 发送文本消息27     # 发送请求

    requests.post(configs["url"], headers=headers, json=msg, proxies={"http": None, "https": None})
    return True

def stop_sound():
    winsound.PlaySound(None, winsound.SND_PURGE)

def _notify_audio(audio_file):
        # 播放循环音频
        winsound.PlaySound(audio_file,
                           winsound.SND_FILENAME | winsound.SND_LOOP | winsound.SND_ASYNC)
        threading.Timer(1, stop_sound).start()

def notify(msg,audio_file):
    _send_wechat(msg)
    _notify_audio(audio_file)


if __name__ == '__main__':
    while True:
        account_info_dict=dbServer.read_accountInfo()

        for monitor in configs["monitor"]:
            if monitor["account"] in account_info_dict.keys():
                account_info = account_info_dict[monitor["account"]]
                notify_msg=f"账户({account_info.name}): "+ monitor["account"] +"\n"
                tmp_msg = notify_msg
                if float_less(account_info.margin_level,monitor["margin_level_is_low"]):
                    notify_msg+=f"预付款维持比率阈值:{monitor['margin_level_is_low']} , 当前值:{account_info.margin_level} \n"
                if float_less(account_info.equity,monitor["equity_is_low"]):
                    notify_msg+=f"净值阈值:{monitor['equity_is_low']} , 当前值:{account_info.equity} \n"
                if float_less(account_info.margin_free,monitor["margin_free_is_low"]):
                    notify_msg+=f"预付款阈值:{monitor['margin_free_is_low']} , 当前值:{account_info.margin_free} "
                if tmp_msg != notify_msg:
                    notify(notify_msg,monitor["audio_path"])
            else:
                _send_wechat(f"未找到这个账户:[{monitor['account']}] 请检查该账户是否和交易程序的账户匹配")
        time.sleep(configs["interval_seconds"])