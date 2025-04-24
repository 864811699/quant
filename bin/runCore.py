# coding=utf-8

if __name__ == '__main__':
    import multiprocessing

    multiprocessing.freeze_support()  # 关键代码：防止多进程重复执行

    import datetime as dt
    import os
    import sys

    pwd = os.path.dirname(os.path.realpath(__file__))
    sys.path.insert(0, pwd + '/../')
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/ctp/')))

    from src.core import server
    from src.core import app
    from package.logger import logger
    logger.setup_logger("core")

    now = dt.datetime.now()
    print("\n\n\n-" + "-" * 80)
    print("{}  running ... ".format(now))
    s = server.Server("../etc/server.toml", "../etc/strategy.toml")
    s.init_api()
    s.runApi()
    app = app.create_app(s)
    app.run(host=s.webConfig["host"], port=s.webConfig["port"], debug=False)
