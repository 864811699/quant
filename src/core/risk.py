import threading
import copy
from collections import deque
import time

from package.config import config


# 美元/伦敦金/点差 是否在区间
def check_in_safe_range(price,safe_range):
    a,b,c = safe_range[0], safe_range[1], price
    if a <= b:
        return a <= c <= b
    else:
        return b <= c <= a

# 美元/黄金 的保证金比例/净值 是否 > 预警值
# 期货可用资金 是否> 预警值
def is_safe_equity_or_margin(number,safe_number):
    if number >= safe_number:
        return True
    return False

class PriceAlert:
    def __init__(self, window_seconds, threshold):
        self.window = window_seconds  # 时间窗口（秒）
        self.threshold = threshold    # 波动阈值
        self.prices = deque()         # 存储 (timestamp, price)

    def update(self, price: float):
        now = time.time()
        self.prices.append((now, price))

        while self.prices and now - self.prices[0][0] > self.window:
            self.prices.popleft()

        if not self.prices:
            return True ,""# 避免 max/min 空列表报错

        prices_only = [p for _, p in self.prices]
        max_price = max(prices_only)
        min_price = min(prices_only)

        if max_price - min_price > self.threshold:
            return False,self.trigger_alert(max_price, min_price)
        return True,""
    def trigger_alert(self, high, low):
        return f"报警：{self.window} 秒内最高价:{high:.2f} 最低价:{low:.2f}波动 {high - low:.2f} 超过阈值 {self.threshold} "

class Monitor:
    def __init__(self, interval_seconds):
        """
        初始化监控器。
        :param interval_seconds: 间隔时间（秒），限制报警频率
        """
        self.interval = interval_seconds
        self.last_alert_time = 0

    def should_alert(self):
        """
        判断当前是否应该触发报警。
        :return: True 表示可以报警，False 表示还在间隔期内
        """
        current_time = time.time()
        if current_time - self.last_alert_time >= self.interval:
            self.last_alert_time = current_time
            return True
        return False


class Risk:
    def __init__(self,risk_file_path):
        self.risk_file_path = risk_file_path
        self.risk_lock = threading.Lock()
        self.riskConfig = None
        self.risk_cfg=None

        self._load_risk_config()
        self.priceAlert=PriceAlert(self.riskConfig["xau_volatility_time"],self.riskConfig["xau_volatility_price"]) # 初始化 滑动窗口

        self.max_position_monitor=Monitor(5*60)

        self.ctp_margin_free_is_low_stop_monitor=Monitor(10*60)

    def _load_risk_config(self):
        cfg = config.Config("", self.risk_file_path)
        self.risk_cfg = cfg
        cfg.read_strategy()
        self.riskConfig = cfg.getStrategyConfig()

    def get_risk(self):
        with self.risk_lock:
            risk_bak = copy.deepcopy(self.riskConfig)
            return risk_bak


    def update_risk(self,data):
        with self.risk_lock:
            self.riskConfig["spread_safety_range"]=[data["spread_start"],data["stop_spread"]]
            self.riskConfig["xau_margin_level"]=data["xau_margin_level"]
            self.riskConfig["xau_margin_level_stop_trade"]=data["xau_margin_level_stop_trade"]
            self.riskConfig["usd_margin_level"]=data["usd_margin_level"]
            self.riskConfig["usd_margin_level_stop_trade"]=data["usd_margin_level_stop_trade"]
            self.riskConfig["xau_equity"]=data["xau_equity"]
            self.riskConfig["usd_equity"]=data["usd_equity"]
            self.riskConfig["ctp_margin_free"]=data["ctp_margin_free"]
            self.riskConfig["ctp_margin_free_stop_trade"]=data["ctp_margin_free_stop_trade"]
            self.riskConfig["xau_price_safety_range"]=[data["xua_price_start"],data["xua_price_stop"]]
            self.riskConfig["usd_price_safety_range"]=[data["usd_price_start"],data["usd_price_stop"]]
            self.riskConfig["xau_volatility_time"]=data["xau_volatility_time"]
            self.riskConfig["xau_volatility_price"]=data["xau_volatility_price"]
            self.risk_cfg.write_strategy(self.riskConfig)

    def spread_is_safe(self,spread):
        return check_in_safe_range(spread, self.riskConfig["spread_safety_range"]),self.riskConfig["spread_safety_range"]

    def usd_is_safe(self,price):
        return check_in_safe_range(price, self.riskConfig["usd_price_safety_range"]),self.riskConfig["usd_price_safety_range"]

    def xau_is_safe(self,price):
        return check_in_safe_range(price, self.riskConfig["xau_price_safety_range"]),self.riskConfig["xau_price_safety_range"]

    def xau_margin_is_above_safe(self,margin):
        return margin>=self.riskConfig["xau_margin_level"],self.riskConfig["xau_margin_level"]

    def xau_margin_level_is_above_stop_trade_safe(self,margin):
        return margin >= self.riskConfig["xau_margin_level_stop_trade"],self.riskConfig["xau_margin_level_stop_trade"]
    def usd_margin_is_above_safe(self,margin):
        return margin >= self.riskConfig["usd_margin_level"],self.riskConfig["usd_margin_level"]

    def usd_margin_level_is_above_stop_trade_safe(self, margin):
        return margin >= self.riskConfig["usd_margin_level_stop_trade"], self.riskConfig["usd_margin_level_stop_trade"]
    def xau_equity_is_above_safe(self,equity):
        return equity >= self.riskConfig["xau_equity"],self.riskConfig["xau_equity"]

    def usd_equity_is_above_safe(self,equity):
        return equity >= self.riskConfig["usd_equity"],self.riskConfig["usd_equity"]

    def ctp_margin_free_is_above_safe(self,margin):
        return margin >= self.riskConfig["ctp_margin_free"],self.riskConfig["ctp_margin_free"]

    def ctp_margin_free_is_above_stop_trade_safe(self,margin):
        return margin >= self.riskConfig["ctp_margin_free_stop_trade"],self.riskConfig["ctp_margin_free_stop_trade"]

    def check_price_alert_is_safe(self,price):
        return self.priceAlert.update(price)

    def is_above_max_positions(self,current_positions,max_positions):
        return current_positions>=max_positions