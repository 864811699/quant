# coding=UTF-8

import toml
from threading import Lock


class Config(object):
    def __init__(self, baseFile, strategyFile):
        self.baseConfigFile = baseFile
        self.strategyFile = strategyFile
        self.cfg=None

        self.lock = Lock()
        self.strategyConfig = {}

    def load_config(self):
        with open(self.baseConfigFile, 'r',encoding='utf-8') as file:
            self.cfg = toml.load(file)

    def get_web_config(self):
        return self.cfg['webConfig']

    def get_base_config(self):
        return self.cfg['baseConfig']

    def get_db_config(self):
        return self.cfg['db']

    def get_notify_config(self):
        return self.cfg['notifyConfig']

    def get_zmq_config(self):
        return self.cfg['zmq']

    def getStrategyConfig(self):
        with self.lock:
            return self.strategyConfig

    def read_strategy(self):
        with open(self.strategyFile, 'r',encoding='utf-8') as file:
            cfg = toml.load(file)
            self.strategyConfig = cfg

    def write_strategy(self, strategy={}):
        with open(self.strategyFile, 'w') as file:
            with self.lock:
                toml.dump(strategy, file)

# webConfig = {
#     "host": "127.0.0.1",
#     "port": 12345,
# }
#
# ctpConfig = {
#     "mdhost": "tcp://180.168.146.187:10211",
#     "tdhost": "tcp://180.168.146.187:10130",
#     "broker": "9999",
#     "user": "235096",
#     "pwd": "Junge@265045",
#     "appid": "simnow_client_test",
#     "authcode": '0000000000000000',
#     "subSymbol": "au2504"
# }
#
# mt5Config = {
#     "host": "Exness-MT5Trial5",
#     "account": 76784022,
#     "pwd": "Junge@789",
#     "maxPosition": 0.0,
#     "subMarket": ["XAUUSDm", "USDCNHm"],  # 勿动顺序
#
# }
#
# strategyConfig = {
#     "op1": {"symbol": "au2504"},
#     "op2": {"symbol": "XAUUSDm", "rate": 0.32},
#     "op3": {"symbol": "USDCNHm", "rate": 0.85},
# }
