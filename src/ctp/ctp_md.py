from datetime import datetime
import sys
import thostmduserapi as mdapi  # manual mode
import time
import queue
import json
from dataclasses import  asdict
import logging
from package.zmq import publisher
from package.zmq import models

from package.logger.logger import setup_logger
log = logging.getLogger('root')
from src.ctp import comm

class CMdImpl(mdapi.CThostFtdcMdSpi):
    def __init__(self, md_front, InstrumentIDs,port,topic):
        mdapi.CThostFtdcMdSpi.__init__(self)
        self.md_front = md_front
        self.api = None
        self.InstrumentIDs = InstrumentIDs
        self.IsLogin = False
        self.queueCtpMD=queue.Queue()
        self.zmq=publisher.ZmqPublisher(port,topic)
        self.msgID=1


    # def getCTPMDQueue(self):
    #     return self.queueCtpMD


    def Run(self):
        self.api = mdapi.CThostFtdcMdApi.CreateFtdcMdApi()
        self.api.RegisterFront(self.md_front)
        self.api.RegisterSpi(self)
        self.api.Init()
        self.subMarketDateReq()



    def OnFrontConnected(self) -> "void":
        log.info("ctp md OnFrontConnected")

        # Market channel doesn't check userid and password.
        req = mdapi.CThostFtdcReqUserLoginField()
        self.api.ReqUserLogin(req, 0)

    def OnFrontDisconnected(self, nReason: int) -> "void":
        #TODO  企业微信通知
        log.warning(f"ctp md OnFrontDisconnected.[nReason={nReason}]")

    def OnRspUserLogin(self, pRspUserLogin: 'CThostFtdcRspUserLoginField', pRspInfo: 'CThostFtdcRspInfoField',
                       nRequestID: 'int', bIsLast: 'bool') -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"ctp md Login failed. {pRspInfo.ErrorMsg}")
            return
        self.IsLogin = True
        log.info(
            f"ctp md Login succeed TradingDay: {pRspUserLogin.TradingDay}  BrokerID:{pRspUserLogin.BrokerID} UserID: {pRspUserLogin.UserID}  SysVersion:{pRspUserLogin.SysVersion}")

    def OnRtnDepthMarketData(self, pDepthMarketData: 'CThostFtdcDepthMarketDataField') -> "void":
        # 行情推送
        # log.info(f"ctp md time:{pDepthMarketData.UpdateTime} - {pDepthMarketData.InstrumentID} - bidPrice:{pDepthMarketData.BidPrice1} - bidVol:{pDepthMarketData.BidVolume1}- askPrice:{pDepthMarketData.AskPrice1}  - askVol:{pDepthMarketData.AskVolume1} ")
        rsp=models.Response()
        ctpmd=models.Market()
        ctpmd.instrumentID=pDepthMarketData.InstrumentID
        ctpmd.bidPrice1=pDepthMarketData.BidPrice1
        ctpmd.askPrice1=pDepthMarketData.AskPrice1
        current_time = datetime.now()
        time_obj=datetime.strptime(pDepthMarketData.UpdateTime,"%H:%M:%S")
        ctpmd.updateTime = time_obj.replace(year=current_time.year, month=current_time.month, day=current_time.day)
        rsp.market=ctpmd
        json_str = rsp.to_json()
        self.zmq.publish(json_str)

    def OnRspSubMarketData(self, pSpecificInstrument: 'CThostFtdcSpecificInstrumentField',
                           pRspInfo: 'CThostFtdcRspInfoField', nRequestID: 'int', bIsLast: 'bool') -> "void":
        # 订阅行情 SubscribeMarketData 的响应
        # 成功后在  OnRtnDepthMarketData 推送
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"ctp md Subscribe failed. [{pSpecificInstrument.InstrumentID}] {pRspInfo.ErrorMsg}")
            exit(-1)
        log.info(f"ctp md Subscribe succeed.{pSpecificInstrument.InstrumentID}")

    def subMarketDateReq(self):
        while self.IsLogin == False:
            time.sleep(0.1)
        InstrumentID_encode_list=[]
        for InstrumentID in self.InstrumentIDs:
            InstrumentID_encode_list.append(InstrumentID.encode('utf-8'))
        rsp = self.api.SubscribeMarketData(InstrumentID_encode_list, len(InstrumentID_encode_list))
        # 0 成功/-1网络链接失败/-2 未处理请求超过许可数/-3 美标发送请求超过许可数
        if rsp == 0:
            log.info("ctp md Subscribe market {} req send succeed".format(self.InstrumentIDs))
        else:
            sys.exit("ctp md Subscribe market {} fail: {}".format(self.InstrumentIDs,rsp))

if __name__ == '__main__':
    md = CMdImpl("tcp://180.168.146.187:10131",["au2502"],12345,"markrt")
    md.Run()
    print("md version: {}".format(md.api.GetApiVersion()))
    md.api.Join()
