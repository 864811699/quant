import time
from threading import Lock
import shelve
from datetime import datetime, timedelta
import os

def get_trade_date():
    now = datetime.now()
    if now.hour < 8:  # 假设 0~8 点为上一交易日
        trade_day = now.date() - timedelta(days=1)
    else:
        trade_day = now.date()
    return trade_day
class TradeThrottler:
    def __init__(self, interval=0.3,):
        self._lock = Lock()
        self._last_ok = 0.0  # 最近成功交易时间
        self.interval = interval  # 冷却间隔

    def pre_check(self) -> bool:
        """交易前检测，返回是否允许交易"""
        with self._lock:
            return time.monotonic() - self._last_ok >= self.interval

    def post_commit(self):
        """交易成功后调用"""
        with self._lock:
            self._last_ok = time.monotonic()

class OrderMaxLimit:
    def __init__(self,max_orders=400,):
        self._lock = Lock()
        self.max_orders_limit= max_orders
        self.db=self._open_db()
        self.count =self.db.get("count", 0)
    def _open_db(self,):
        trade_day = get_trade_date()
        trade_day_str = trade_day.isoformat()
        db_dir = "db"
        os.makedirs(db_dir, exist_ok=True)
        db_path=os.path.join(db_dir, f"trade_count_{trade_day_str}")
        return shelve.open(db_path, writeback=True)
    def pre_check(self):
        """交易前检测，返回是否允许交易"""
        with self._lock:
            if self.count<self.max_orders_limit:
                return True, self._increase_trade_count()
            else:
                return False,self.count
    def _increase_trade_count(self):
        self.count+=1
        self.db["count"] = self.count
        self.db.sync()  # 刷入磁盘
        return self.count
    def increase_trade_count(self):
        with self._lock:
            self.count += 1
            self.db["count"] = self.count
            self.db.sync()  # 刷入磁盘
            return self.count
    def not_cancel(self):
        with self._lock:
            self.count -= 1
            self.db["count"] = self.count
            self.db.sync()  # 刷入磁盘
            return self.count
