from logging.handlers import RotatingFileHandler
import logging
import datetime as dt

def setup_logger(filename):
    today = dt.datetime.today().strftime('%Y%m%d')

    #创建日志记录器
    log = logging.getLogger('root')
    log.setLevel(logging.DEBUG)  # 设置日志级别

    #日志格式
    log_format = logging.Formatter(
        "%(asctime)s  %(levelname)s %(filename)s:%(lineno)d: %(message)s")

    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)  # 设置控制台日志级别
    console_handler.setFormatter(log_format)

    #创建文件日志
    file_log_handler = RotatingFileHandler("./{}-{}.log".format(filename,today), mode='a', maxBytes=2000*1024*1024,
                                          backupCount=100, encoding=None, delay=0)
    file_log_handler.setFormatter(log_format)
    file_log_handler.setLevel(logging.INFO)

    log.addHandler(console_handler)
    log.addHandler(file_log_handler)
    return log
