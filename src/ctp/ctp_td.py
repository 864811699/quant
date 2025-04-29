import queue
import sys
import time
import os
import threading
from datetime import datetime
# from openctp_ctp import tdapi # pip install mode
import thosttraderapi as tdapi  # manual mode
from src.ctp import comm
from package.zmq import models



import logging
from package.logger.logger import setup_logger
log = logging.getLogger('root')

class TdImpl(tdapi.CThostFtdcTraderSpi):
    def __init__(self, host, broker, user, password, appid, authcode,symbol):
        super().__init__()

        self.broker = broker
        self.user = user
        self.password = password
        self.appid = appid
        self.authcode = authcode
        self.symbol = symbol
        self.isLogin = False
        self.isConnected = False
        self.authenticate = False
        self.event = threading.Event()
        self.semaphore = threading.Semaphore(0)
        self.queueRtnOrder = queue.Queue()
        # self.queueRtnTrade = queue.Queue()
        self.entrustNo = 0
        # self.OrderDict = {}  # entrustno-->models.order
        self.OrderDict = {}  # OrderSysID-->models.order
        self.lock=threading.Lock()
        self.PositionDict = {}  # symbol->longshort-> dict{今/昨->持仓数}

        self.TradingDay = ""
        self.FrontID = 0
        self.SessionID = 0
        self.OrderRef = 0

        self.priceDict = {comm.MARKET_INSTRUMENTID: '', comm.MARKET_BUY1: 0, comm.MARKET_SELL1: 0}

        self.api: tdapi.CThostFtdcTraderApi = tdapi.CThostFtdcTraderApi.CreateFtdcTraderApi()
        self.api.RegisterSpi(self)
        self.api.RegisterFront(host)
        self.api.SubscribePrivateTopic(tdapi.THOST_TERT_QUICK)
        self.api.SubscribePublicTopic(tdapi.THOST_TERT_QUICK)

    def getEntrustNo(self):
        self.entrustNo += 1
        return str(self.entrustNo)

    def qry_ctp_update_position(self, position):
            # position.longShortType = comm.LONG_SHORT_DICT[pInvestorPosition.PosiDirection]  # 多空
            # position.PositionDate=pInvestorPosition.PositionDate   #  持仓日期(今日 THOST_FTDC_PSD_Today '1',昨日 THOST_FTDC_PSD_History '2')
            # position.position = pInvestorPosition.Position                  # 动态(今/昨)仓数量
        with self.lock:
            if position.symbol in self.PositionDict:
                self.PositionDict[position.symbol][position.longShortType][position.PositionDate]=position.position
            else:
                symbol_position_detail=comm.create_symbol_position_detail()
                symbol_position_detail[position.longShortType][position.PositionDate]=position.position
                self.PositionDict[position.symbol]=symbol_position_detail

    def readPosition(self,symbol):
        with self.lock:
            position_vol_dict = {comm.ACTION_LONG:0,comm.ACTION_SHORT:0}
            if symbol in self.PositionDict:
                for k,v in self.PositionDict[symbol].items():
                    vol=0
                    for v2 in v.values():
                        vol+=v2
                    position_vol_dict[k]=vol
            return position_vol_dict

    def OnFrontConnected(self):
        log.info("ctp connected!!!")
        self.isConnected = True

    def OnFrontDisconnected(self, nReason: int):
        self.isLogin = False
        self.isConnected = False
        log.error(f" ctp OnFrontDisconnected.[nReason={nReason}]")

    # 撤单录入请求响应(有字段填写不对之类的CTP报错则通过此接口返回)
    def OnRspOrderAction(self, pInputOrderAction: "CThostFtdcInputOrderActionField", pRspInfo: "CThostFtdcRspInfoField",
                         nRequestID: "int", bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"cancel order  failed: {pRspInfo.ErrorMsg}")

        if pInputOrderAction is not None:
            log.warning(f"cancel order  failed:"
                     f"UserID={pInputOrderAction.UserID} "
                     f"ActionFlag={pInputOrderAction.ActionFlag} "
                     f"OrderActionRef={pInputOrderAction.OrderActionRef} "
                     f"BrokerID={pInputOrderAction.BrokerID} "
                     f"InvestorID={pInputOrderAction.InvestorID} "
                     f"ExchangeID={pInputOrderAction.ExchangeID} "
                     f"InstrumentID={pInputOrderAction.InstrumentID} "
                     f"FrontID={pInputOrderAction.FrontID} "
                     f"SessionID={pInputOrderAction.SessionID} "
                     f"OrderRef={pInputOrderAction.OrderRef} "
                     f"OrderSysID={pInputOrderAction.OrderSysID} "
                     f"InvestUnitID={pInputOrderAction.InvestUnitID} "
                     f"IPAddress={pInputOrderAction.IPAddress} "
                     f"MacAddress={pInputOrderAction.MacAddress} "
                     )

    def OnErrRtnOrderAction(self, pOrderAction: "CThostFtdcOrderActionField",
                            pRspInfo: "CThostFtdcRspInfoField") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"OnErrRtnOrderAction failed: {pRspInfo.ErrorMsg}")

        if pOrderAction is not None:
            log.warning(f"OnErrRtnOrderAction:"
                     f"UserID={pOrderAction.UserID} "
                     f"ActionFlag={pOrderAction.ActionFlag} "
                     f"OrderActionRef={pOrderAction.OrderActionRef} "
                     f"BrokerID={pOrderAction.BrokerID} "
                     f"InvestorID={pOrderAction.InvestorID} "
                     f"ExchangeID={pOrderAction.ExchangeID} "
                     f"InstrumentID={pOrderAction.InstrumentID} "
                     f"FrontID={pOrderAction.FrontID} "
                     f"SessionID={pOrderAction.SessionID} "
                     f"OrderRef={pOrderAction.OrderRef} "
                     f"OrderSysID={pOrderAction.OrderSysID} "
                     f"InvestUnitID={pOrderAction.InvestUnitID} "
                     f"IPAddress={pOrderAction.IPAddress} "
                     f"MacAddress={pOrderAction.MacAddress} "
                     )

    # 持仓汇总
    def OnRspQryInvestorPosition(self, pInvestorPosition: tdapi.CThostFtdcInvestorPositionField,
                                 pRspInfo: "CThostFtdcRspInfoField", nRequestID: "int", bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"ctp OnRspQryInvestorPosition failed: {pRspInfo.ErrorMsg}")
            self.semaphore.release()
            return

        if pInvestorPosition is not None:
            """
            如果持仓日期为THOST_FTDC_PSD_History，一定是SHFE或INE的合约，Position表示动态昨仓总数量，TodayPosition始终为0，YdPosition表示开盘前静态总持仓数量。
            持仓日期为THOST_FTDC_PSD_Today时，分两种情况：
                如果是SHFE或INE的合约，Position与TodayPosition相同，表示动态今仓数量，YdPosition始终为0。
                其他交易所，Position表示动态总仓数量，TodayPosition表示动态今仓数量，YdPosition表示开盘前静态总持仓数量。动态昨仓数量为Position-TodayPosition。
            """
            position = models.CtpPosition()
            position.PositionDate=pInvestorPosition.PositionDate   #  持仓日期(今日 THOST_FTDC_PSD_Today '1',昨日 THOST_FTDC_PSD_History '2')
            position.symbol = pInvestorPosition.InstrumentID
            position.longShortType = comm.LONG_SHORT_DICT[pInvestorPosition.PosiDirection]  # 多空
            position.position = pInvestorPosition.Position                  # 动态(今/昨)仓数量
            position.CloseProfit = pInvestorPosition.CloseProfit  # 平仓盈亏
            position.PositionCost = pInvestorPosition.PositionCost  # 持仓成本
            self.qry_ctp_update_position(position)

            log.info(f"ctp OnRspInvestorPosition:{pInvestorPosition.InstrumentID} "
                     f"ExchangeID={pInvestorPosition.ExchangeID} "
                     f"InstrumentID={pInvestorPosition.InstrumentID} "
                     f"HedgeFlag={pInvestorPosition.HedgeFlag} "
                     f"PositionDate={pInvestorPosition.PositionDate} "
                     f"PosiDirection={pInvestorPosition.PosiDirection} "
                     f"Position={pInvestorPosition.Position} "
                     f"YdPosition={pInvestorPosition.YdPosition} "
                     f"TodayPosition={pInvestorPosition.TodayPosition} "
                     f"UseMargin={pInvestorPosition.UseMargin} "
                     f"PreMargin={pInvestorPosition.PreMargin} "
                     f"FrozenMargin={pInvestorPosition.FrozenMargin} "
                     f"Commission={pInvestorPosition.Commission} "
                     f"FrozenCommission={pInvestorPosition.FrozenCommission} "
                     f"CloseProfit={pInvestorPosition.CloseProfit} "
                     f"LongFrozen={pInvestorPosition.LongFrozen} "
                     f"ShortFrozen={pInvestorPosition.ShortFrozen} "
                     f"PositionCost={pInvestorPosition.PositionCost} "
                     f"OpenCost={pInvestorPosition.OpenCost} "
                     f"SettlementPrice={pInvestorPosition.SettlementPrice} "
                     )

        if bIsLast == True:
            self.semaphore.release()

    # 持仓明细
    def OnRspQryInvestorPositionDetail(self, pInvestorPositionDetail: tdapi.CThostFtdcInvestorPositionDetailField,pRspInfo: "CThostFtdcRspInfoField", nRequestID: "int",bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            print(f"OnRspQryInvestorPositionDetail failed: {pRspInfo.ErrorMsg}")
            return

        if pInvestorPositionDetail is not None:
            print(f"OnRspQryInvestorPositionDetail:{pInvestorPositionDetail.InstrumentID} "
                  f"Direction={pInvestorPositionDetail.Direction} "
                  f"HedgeFlag={pInvestorPositionDetail.HedgeFlag} "
                  f"Volume={pInvestorPositionDetail.Volume} "
                  f"OpenPrice={pInvestorPositionDetail.OpenPrice} "
                  f"Margin={pInvestorPositionDetail.Margin} "
                  f"CloseVolume={pInvestorPositionDetail.CloseVolume} "
                  f"CloseAmount={pInvestorPositionDetail.CloseAmount} "
                  f"OpenDate={pInvestorPositionDetail.OpenDate} "
                  f"TradingDay={pInvestorPositionDetail.TradingDay} "
                  )

        if bIsLast == True:
            self.semaphore.release()

    def OnRspQryTradingAccount(self, pTradingAccount: tdapi.CThostFtdcTradingAccountField,
                               pRspInfo: "CThostFtdcRspInfoField", nRequestID: "int", bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            print(f"OnRspQryTradingAccount failed: {pRspInfo.ErrorMsg}")
            return

        if pTradingAccount is not None:
            print(f"OnRspQryTradingAccount: "
                  f"PreBalance={pTradingAccount.PreBalance} "
                  f"PreMargin={pTradingAccount.PreMargin} "
                  f"FrozenMargin={pTradingAccount.FrozenMargin} "
                  f"CurrMargin={pTradingAccount.CurrMargin} "
                  f"Commission={pTradingAccount.Commission} "
                  f"FrozenCommission={pTradingAccount.FrozenCommission} "
                  f"Available={pTradingAccount.Available} "
                  f"Balance={pTradingAccount.Balance} "
                  f"CloseProfit={pTradingAccount.CloseProfit} "
                  f"CurrencyID={pTradingAccount.CurrencyID} "
                  )

        if bIsLast == True:
            self.semaphore.release()

    def QryAccount(self):
        # 查询资金账户
        req = tdapi.CThostFtdcQryTradingAccountField()
        req.BrokerID = self.broker
        req.InvestorID = self.user
        self.api.ReqQryTradingAccount(req, 0)

    def QryPosition(self):
        req = tdapi.CThostFtdcQryInvestorPositionField()
        req.InvestorID = self.user
        req.BrokerID = self.broker
        # req.InstrumentID = InstrumentID
        return self.api.ReqQryInvestorPosition(req, 0)

    def load_positions_from_ctp(self):
        rtnNo = self.QryPosition()
        if rtnNo != 0:
            errmsg = comm.ErrorCodeDict[rtnNo]
            log.warning("ctp qry positions  fail, errmsg:{}".format(errmsg))
            return False
        success = self.semaphore.acquire(timeout=10)
        if success is False:
            log.error("ctp qry positions failed ,timeout!!")
            return False
        log.info("ctp qry positions  done")
        return True

    def getPosition(self,symbol):
        return True, self.readPosition()


    def get_close_position_type(self,longshort,symbol):
        # 默认平昨,除非昨日持仓为0
        # self.PositionDict[position.longShortType][position.PositionDate].
        with self.lock:
            return comm.OFFSET_CLOSE_PREV if self.PositionDict[symbol][longshort][comm.POSITION_YESTERDAY] > 0 else comm.OFFSET_CLOSE_TODAY

    def after_trade_update_position(self,order,offset):
        with self.lock:
            # 1 开 : 只能增加今  +1
            # 2 平 : 今/昨 减   -1
            if order.openClose == comm.OFFSET_OPEN:
                if order.symbol not in self.PositionDict:
                    self.PositionDict[order.symbol]=comm.create_symbol_position_detail()
                self.PositionDict[order.symbol][order.longShort][comm.POSITION_TODAY] += 1
            elif order.openClose == comm.OFFSET_CLOSE:
                if offset == comm.OFFSET_CLOSE_TODAY:
                    self.PositionDictPositionDict[order.symbol][order.longShort][comm.POSITION_TODAY] -= 1
                elif offset == comm.OFFSET_CLOSE_PREV:
                    self.PositionDictPositionDict[order.symbol][order.longShort][comm.POSITION_YESTERDAY] -= 1
            log.info("ctp update positions after trade :{}".format(self.PositionDict))

    def QryPositionDetail(self, InstrumentID):
        # 查询持仓- 明细
        req = tdapi.CThostFtdcQryInvestorPositionDetailField()
        req.InvestorID = self.user
        req.BrokerID = self.broker
        req.InstrumentID = InstrumentID
        self.api.ReqQryInvestorPositionDetail(req, 0)

    # 价格查询回调
    def OnRspQryDepthMarketData(self, pDepthMarketData: tdapi.CThostFtdcDepthMarketDataField,
                                pRspInfo: "CThostFtdcRspInfoField", nRequestID: "int", bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            print(f"OnRspQryDepthMarketData failed: {pRspInfo.ErrorMsg}")
            self.semaphore.release()
            return
        if pDepthMarketData is not None and pDepthMarketData.InstrumentID == self.priceDict[comm.MARKET_INSTRUMENTID]:
            self.priceDict[comm.MARKET_BUY1] = pDepthMarketData.AskPrice1
            self.priceDict[comm.MARKET_SELL1] = pDepthMarketData.BidPrice1
            self.priceDict[comm.MARKET_ACTIONDAY] = pDepthMarketData.ActionDay
            self.priceDict[comm.MARKET_UPDATETIME] = pDepthMarketData.UpdateTime
            self.priceDict[comm.MARKET_TRADINGDAY] = pDepthMarketData.TradingDay
            self.priceDict[comm.MARKET_BUY1] = pDepthMarketData.AskPrice1
            self.semaphore.release()

    def QryPrice(self, ExchangeID, InstrumentID):
        # 查询行情
        req = tdapi.CThostFtdcQryDepthMarketDataField()
        req.ExchangeID = ExchangeID
        req.InstrumentID = InstrumentID
        self.priceDict[comm.MARKET_INSTRUMENTID] = InstrumentID
        rtnNo = self.api.ReqQryDepthMarketData(req, 0)
        if rtnNo != 0:
            # self.semaphore.release()
            errmsg = comm.ErrorCodeDict[rtnNo]
            log.warning("qry Price [{}] fail,req ctp errmsg:{}".format(InstrumentID, errmsg))
            return False
        return True

    def GetPrice(self, ExchangeID, InstrumentID, longshort,openclose):
        price = 0
        side = comm.getSide(longshort, openclose)
        self.priceDict = {comm.MARKET_SELL1:0,comm.MARKET_BUY1:0}
        success=self.QryPrice(ExchangeID, InstrumentID)
        if not success:
            return price

        success = self.semaphore.acquire(timeout=2)
        if success == True:
            price = self.priceDict[comm.MARKET_SELL1] if side == comm.SIDE_DICT["BUY"] else self.priceDict[comm.MARKET_BUY1]
            log.info("Qry Price [{}] success, price.buy1:{} ,price.sell1:{}".format(InstrumentID,self.priceDict[comm.MARKET_BUY1],self.priceDict[comm.MARKET_SELL1]))
        else:
            log.warning("qry price [{}] fail,req ctp timeout".format(InstrumentID))
        return price

    def Authenticate(self):
        req = tdapi.CThostFtdcReqAuthenticateField()
        req.BrokerID = self.broker
        req.UserID = self.user
        req.AppID = self.appid
        req.AuthCode = self.authcode
        return self.api.ReqAuthenticate(req, 0)

    def OnRspAuthenticate(self, pRspAuthenticateField: tdapi.CThostFtdcRspAuthenticateField,
                          pRspInfo: tdapi.CThostFtdcRspInfoField, nRequestID: int, bIsLast: bool, ):
        """客户端认证响应"""
        log.debug(f"OnRspAuthenticate")
        if pRspInfo and pRspInfo.ErrorID != 0:
            log.error("认证失败：{}".format(pRspInfo.ErrorMsg))
            self.semaphore.release()
            exit(-1)
        self.authenticate = True
        log.info("ctp Authenticate succeed.")
        self.semaphore.release()

    def GetCTPAuthenticateStatus(self):
        rtn = self.Authenticate()
        if rtn != 0:
            self.semaphore.release()
            errmsg = comm.ErrorCodeDict[rtn]
            log.warning("Authenticate [{}] fail, errmsg:{}".format(self.UserID, errmsg))
            exit(-1)
        log.info("Authenticate req send success!!!")
        success = self.semaphore.acquire(timeout=5)
        if success is False:
            log.error("Authenticate failed ,timeout!!")
            exit(-1)

    def OnRspUserLogin(self, pRspUserLogin: tdapi.CThostFtdcRspUserLoginField, pRspInfo: tdapi.CThostFtdcRspInfoField,
                       nRequestID: int, bIsLast: bool):
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.error(f"ctp Login failed. {pRspInfo.ErrorMsg}")
            self.semaphore.release()
            exit(-1)
        self.TradingDay = pRspUserLogin.TradingDay
        self.FrontID = pRspUserLogin.FrontID
        self.SessionID = pRspUserLogin.SessionID
        self.OrderRef = 1
        self.isLogin = True
        self.semaphore.release()
        log.info(
            f"ctp Login succeed. TradingDay: {pRspUserLogin.TradingDay}, MaxOrderRef: {pRspUserLogin.MaxOrderRef}, SystemName: {pRspUserLogin.SystemName}")

    def Login(self):
        # 登录
        req = tdapi.CThostFtdcReqUserLoginField()
        req.BrokerID = self.broker
        req.UserID = self.user
        req.Password = self.password
        req.UserProductInfo = "demo"
        return self.api.ReqUserLogin(req, 0)

    def GetCTPLoginStatus(self):
        rtn = self.Login()
        if rtn != 0:
            errmsg = comm.ErrorCodeDict[rtn]
            log.warning("Login [{}] fail, errmsg:{}".format(self.UserID, errmsg))
            exit(-1)
        log.info("Login req send success!!!")
        success = self.semaphore.acquire(timeout=5)
        if success is False:
            log.error("Login failed ,timeout!!")
            exit(-1)
        return self.isLogin

    def OnRspSettlementInfoConfirm(self, pSettlementInfoConfirm: "CThostFtdcSettlementInfoConfirmField",
                                   pRspInfo: "CThostFtdcRspInfoField", nRequestID: "int", bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.error(f"OnRspSettlementInfoConfirm failed. {pRspInfo.ErrorMsg}")
            self.semaphore.release()
            exit(-1)
        log.info(
            f"OnRspSettlementInfoConfirm: BrokerID:{pSettlementInfoConfirm.BrokerID}, InvestorID:{pSettlementInfoConfirm.InvestorID}, ConfirmDate:{pSettlementInfoConfirm.ConfirmDate}, ConfirmTime:{pSettlementInfoConfirm.ConfirmTime}, CurrencyID:{pSettlementInfoConfirm.CurrencyID}")

        if bIsLast == True:
            self.semaphore.release()

    def ConfirmSettlementInfo(self):
        req = tdapi.CThostFtdcSettlementInfoConfirmField()
        req.BrokerID = self.broker
        req.InvestorID = self.user
        return self.api.ReqSettlementInfoConfirm(req, 0)

    def CheckSettlementInfo(self):
        rtn = self.ConfirmSettlementInfo()
        if rtn != 0:
            log.error("send settlement info confirm failed,errorMsg:{}".format(comm.ErrorCodeDict[rtn]))
            exit(-1)
        log.info("send settlement info confirm success")
        success = self.semaphore.acquire(timeout=5)
        if success is False:
            log.error("CheckSettlementInfo failed ,timeout!!")
            exit(-1)
        return True

    # 报单通知
    def OnRtnOrder(self, pOrder):
        """
        报单通知
        CThostFtdcTraderSpi_OnRtnOrder
        """
        order = comm.OrderTrade()
        order.symbol=pOrder.InstrumentID
        order.dataType = comm.DATA_TYPE_ORDER
        order.account = pOrder.UserID
        order.orderRef = pOrder.OrderRef
        order.entrustNo=int(order.orderRef)
        order.orderSysID = pOrder.OrderSysID.lstrip()
        order.askPrice = pOrder.LimitPrice
        order.askQty = pOrder.VolumeTotalOriginal
        order.status = pOrder.OrderStatus
        order.statusMsg = pOrder.StatusMsg
        self.queueRtnOrder.put(order)
        log.info(f"OnRtnOrder:"
                 f"UserID={pOrder.UserID} "
                 f"BrokerID={pOrder.BrokerID} "
                 f"InvestorID={pOrder.InvestorID} "
                 f"ExchangeID={pOrder.ExchangeID} "
                 f"InstrumentID={pOrder.InstrumentID} "
                 f"Direction={pOrder.Direction} "
                 f"CombOffsetFlag={pOrder.CombOffsetFlag} "
                 f"CombHedgeFlag={pOrder.CombHedgeFlag} "
                 f"OrderPriceType={pOrder.OrderPriceType} "
                 f"LimitPrice={pOrder.LimitPrice} "
                 f"VolumeTotalOriginal={pOrder.VolumeTotalOriginal} "
                 f"FrontID={pOrder.FrontID} "
                 f"SessionID={pOrder.SessionID} "
                 f"OrderRef={pOrder.OrderRef} "
                 f"TimeCondition={pOrder.TimeCondition} "
                 f"GTDDate={pOrder.GTDDate} "
                 f"VolumeCondition={pOrder.VolumeCondition} "
                 f"MinVolume={pOrder.MinVolume} "
                 f"RequestID={pOrder.RequestID} "
                 f"InvestUnitID={pOrder.InvestUnitID} "
                 f"CurrencyID={pOrder.CurrencyID} "
                 f"AccountID={pOrder.AccountID} "
                 f"ClientID={pOrder.ClientID} "
                 f"IPAddress={pOrder.IPAddress} "
                 f"MacAddress={pOrder.MacAddress} "
                 f"OrderSysID={pOrder.OrderSysID.lstrip()} "
                 f"OrderStatus={pOrder.OrderStatus} "
                 f"StatusMsg={pOrder.StatusMsg} "
                 f"VolumeTotal={pOrder.VolumeTotal} "
                 f"VolumeTraded={pOrder.VolumeTraded} "
                 f"OrderSubmitStatus={pOrder.OrderSubmitStatus} "
                 f"TradingDay={pOrder.TradingDay} "
                 f"InsertDate={pOrder.InsertDate} "
                 f"InsertTime={pOrder.InsertTime} "
                 f"UpdateTime={pOrder.UpdateTime} "
                 f"CancelTime={pOrder.CancelTime} "
                 f"UserProductInfo={pOrder.UserProductInfo} "
                 f"ActiveUserID={pOrder.ActiveUserID} "
                 f"BrokerOrderSeq={pOrder.BrokerOrderSeq} "
                 f"TraderID={pOrder.TraderID} "
                 f"ClientID={pOrder.ClientID} "
                 f"ParticipantID={pOrder.ParticipantID} "
                 f"OrderLocalID={pOrder.OrderLocalID} "
                 )

    # 成交通知
    def OnRtnTrade(self, pTrade):
        """
        成交通知
        CThostFtdcTraderSpi_OnRtnTrade
        """
        trade = comm.OrderTrade()
        trade.symbol = pTrade.InstrumentID
        longshort, openclose = comm.getLongShortOpenClose(pTrade.Direction, pTrade.OffsetFlag)
        trade.longShort = longshort
        trade.openClose = openclose
        trade.dataType = comm.DATA_TYPE_TRADE
        trade.account = pTrade.UserID
        trade.tradeTime = datetime.strptime(pTrade.TradeDate + pTrade.TradeTime,"%Y%m%d%H:%M:%S")  # date char[9]   time char[9]
        trade.bidPrice = pTrade.Price
        trade.bidVol = pTrade.Volume
        trade.orderRef = pTrade.OrderRef

        self.queueRtnOrder.put(trade)
        log.info(f"OnRtnTrade:"
                 f"BrokerID={pTrade.BrokerID} "
                 f"InvestorID={pTrade.InvestorID} "
                 f"ExchangeID={pTrade.ExchangeID} "
                 f"InstrumentID={pTrade.InstrumentID} "
                 f"Direction={pTrade.Direction} "
                 f"OffsetFlag={pTrade.OffsetFlag} "
                 f"HedgeFlag={pTrade.HedgeFlag} "
                 f"Price={pTrade.Price}  "
                 f"Volume={pTrade.Volume} "
                 f"OrderSysID={pTrade.OrderSysID.lstrip()} "
                 f"OrderRef={pTrade.OrderRef} "
                 f'TradeID={pTrade.TradeID} '
                 f'TradeDate={pTrade.TradeDate} '
                 f'TradeTime={pTrade.TradeTime} '
                 f'ClientID={pTrade.ClientID} '
                 f'TradingDay={pTrade.TradingDay} '
                 f'OrderLocalID={pTrade.OrderLocalID} '
                 f'BrokerOrderSeq={pTrade.BrokerOrderSeq} '
                 f'InvestUnitID={pTrade.InvestUnitID} '
                 f'ParticipantID={pTrade.ParticipantID} '
                 )

    # 下单废单
    def OnErrRtnOrderInsert(self, pInputOrder: "CThostFtdcInputOrderField",
                            pRspInfo: "CThostFtdcRspInfoField") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"OnErrRtnOrderInsert failed: {pRspInfo.ErrorMsg}")
            order = comm.OrderTrade()
            order.dataType = comm.DATA_TYPE_ORDER
            order.account = pInputOrder.UserID
            order.orderRef = pInputOrder.OrderRef
            order.orderSysID = ""
            order.askPrice = pInputOrder.LimitPrice
            order.askQty = pInputOrder.VolumeTotalOriginal
            order.status = -1
            order.statusMsg = pRspInfo.ErrorMsg
            self.queueRtnOrder.put(order)
        if pInputOrder is not None:
            log.warning(f"OnErrRtnOrderInsert:"
                     f"UserID={pInputOrder.UserID} "
                     f"BrokerID={pInputOrder.BrokerID} "
                     f"InvestorID={pInputOrder.InvestorID} "
                     f"ExchangeID={pInputOrder.ExchangeID} "
                     f"InstrumentID={pInputOrder.InstrumentID} "
                     f"Direction={pInputOrder.Direction} "
                     f"CombOffsetFlag={pInputOrder.CombOffsetFlag} "
                     f"CombHedgeFlag={pInputOrder.CombHedgeFlag} "
                     f"OrderPriceType={pInputOrder.OrderPriceType} "
                     f"LimitPrice={pInputOrder.LimitPrice} "
                     f"VolumeTotalOriginal={pInputOrder.VolumeTotalOriginal} "
                     f"OrderRef={pInputOrder.OrderRef} "
                     f"TimeCondition={pInputOrder.TimeCondition} "
                     f"GTDDate={pInputOrder.GTDDate} "
                     f"VolumeCondition={pInputOrder.VolumeCondition} "
                     f"MinVolume={pInputOrder.MinVolume} "
                     f"RequestID={pInputOrder.RequestID} "
                     f"InvestUnitID={pInputOrder.InvestUnitID} "
                     f"CurrencyID={pInputOrder.CurrencyID} "
                     f"AccountID={pInputOrder.AccountID} "
                     f"ClientID={pInputOrder.ClientID} "
                     f"IPAddress={pInputOrder.IPAddress} "
                     f"MacAddress={pInputOrder.MacAddress} "
                     )

    # 报单录入请求响应(有字段填写不对之类的CTP报错则通过此接口返回)
    def OnRspOrderInsert(self, pInputOrder: "CThostFtdcInputOrderField", pRspInfo: "CThostFtdcRspInfoField",
                         nRequestID: "int", bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"OnRspOrderInsert failed: {pRspInfo.ErrorMsg}")
            order = comm.OrderTrade()
            order.dataType = comm.DATA_TYPE_ORDER
            order.account = pInputOrder.UserID
            order.orderRef = pInputOrder.OrderRef
            order.orderSysID = ""
            order.askPrice = pInputOrder.LimitPrice
            order.askQty = pInputOrder.VolumeTotalOriginal
            order.status = '-1'
            order.statusMsg = pRspInfo.ErrorMsg
            self.queueRtnOrder.put(order)

        if pInputOrder is not None:
            log.warning(f"OnRspOrderInsert:"
                     f"UserID={pInputOrder.UserID} "
                     f"BrokerID={pInputOrder.BrokerID} "
                     f"InvestorID={pInputOrder.InvestorID} "
                     f"ExchangeID={pInputOrder.ExchangeID} "
                     f"InstrumentID={pInputOrder.InstrumentID} "
                     f"Direction={pInputOrder.Direction} "
                     f"CombOffsetFlag={pInputOrder.CombOffsetFlag} "
                     f"CombHedgeFlag={pInputOrder.CombHedgeFlag} "
                     f"OrderPriceType={pInputOrder.OrderPriceType} "
                     f"LimitPrice={pInputOrder.LimitPrice} "
                     f"VolumeTotalOriginal={pInputOrder.VolumeTotalOriginal} "
                     f"OrderRef={pInputOrder.OrderRef} "
                     f"TimeCondition={pInputOrder.TimeCondition} "
                     f"GTDDate={pInputOrder.GTDDate} "
                     f"VolumeCondition={pInputOrder.VolumeCondition} "
                     f"MinVolume={pInputOrder.MinVolume} "
                     f"RequestID={pInputOrder.RequestID} "
                     f"InvestUnitID={pInputOrder.InvestUnitID} "
                     f"CurrencyID={pInputOrder.CurrencyID} "
                     f"AccountID={pInputOrder.AccountID} "
                     f"ClientID={pInputOrder.ClientID} "
                     f"IPAddress={pInputOrder.IPAddress} "
                     f"MacAddress={pInputOrder.MacAddress} "
                     )

    def OrderInsert(self, ExchangeID, InstrumentID, Direction, Offset, Price, Volume, EntrustNo):
        """ 平仓指令  当天建的仓只能用“平今”指令才能平掉
        THOST_FTDC_OFEN_Open '0'
        THOST_FTDC_OFEN_Close '1'
        THOST_FTDC_OFEN_CloseToday '3'
        THOST_FTDC_OFEN_CloseYesterday '4'
        """
        req = tdapi.CThostFtdcInputOrderField()
        req.BrokerID = self.broker
        req.UserID = self.user
        req.InvestorID = self.user
        req.InstrumentID = InstrumentID  # 合约代码
        req.OrderRef = str(EntrustNo)  # 报单引用   本地会话全局唯一编号，必须保持递增；可由用户维护，也可由系统自动填写。一定为数字。
        req.CombOffsetFlag = Offset  # 开平标志 THOST_FTDC_OFEN_Open
        req.CombHedgeFlag = tdapi.THOST_FTDC_HF_Speculation  # 投机套保--投机   THOST_FTDC_HF_Speculation   ???
        req.ExchangeID = ExchangeID
        req.VolumeTotalOriginal = int(Volume)  # 报单数量
        req.RequestID = EntrustNo  # int
        # req.IsSwapOrder = False   #互换单标志 bool类型
        req.OrderPriceType = tdapi.THOST_FTDC_OPT_LimitPrice  # 价格类型--限价     上期所只支持限价
        req.Direction = Direction  # 买卖  THOST_FTDC_D_Buy

        req.TimeCondition = tdapi.THOST_FTDC_TC_IOC  # 有效期 --立即完成,否则撤销
        # req.TimeCondition = tdapi.THOST_FTDC_TC_GFD  # 有效期 --当日有效
        req.VolumeCondition = tdapi.THOST_FTDC_VC_CV  # 成交量类型  -- 全部数量
        # req.VolumeCondition = tdapi.THOST_FTDC_VC_AV  # 成交量类型  -- 任意数量

        req.ForceCloseReason = tdapi.THOST_FTDC_FCC_NotForceClose  # 强平原因--非强平
        req.ContingentCondition = tdapi.THOST_FTDC_CC_Immediately  # 触发条件类型--立即
        req.LimitPrice = Price  # ???
        req.StopPrice = 0  # ???
        rtn = self.api.ReqOrderInsert(req, 0)
        return rtn

    def OrderCancel(self, ExchangeID, InstrumentID, OrderSysID, FrontID, SessionID, OrderRef):
        req = tdapi.CThostFtdcInputOrderActionField()
        req.BrokerID = self.broker
        req.UserID = self.user
        req.InvestorID = self.user
        req.ExchangeID = ExchangeID
        req.InstrumentID = InstrumentID
        req.OrderSysID = OrderSysID
        if FrontID != "":
            req.FrontID = int(FrontID)
        if SessionID != "":
            req.SessionID = int(SessionID)
        req.OrderRef = OrderRef
        req.ActionFlag = tdapi.THOST_FTDC_AF_Delete
        self.api.ReqOrderAction(req, 0)

    def ExecOrder(self,order):
        # 返回 : 是否成交 |  错误信息| 完整订单
        ExchangeID = "SHFE"
        side = comm.getSide(order.longShort, order.openClose)
        offset = comm.OFFSET_OPEN if order.openClose==models.TRADE_TYPE_OPEN else self.get_close_position_type(order.longShort,order.symbol)
        rtnExecO = comm.RtnExecOrder()

        rtn = self.OrderInsert(ExchangeID, order.symbol, side, offset, order.askPrice, order.askQty, order.entrustNo)
        #  盘口就1手,极容易下单失败
        if rtn != 0:
            log.warning(
                "OrderInsert failed,req:ExchangeID:{},   order.symbol:{},   side:{},   offset:{},   askPrice:{},   Volume:{},   EntrustNo:{} ,msg:{}".format(
                    ExchangeID, order.symbol, side, offset, order.askPrice, order.askQty, order.entrustNo, comm.ErrorCodeDict[rtn]))
            order.status = comm.REJECTED
            order.tmpStatus = comm.REJECTED
            rtnExecO.reqSuccess = False
            rtnExecO.msg = "ctp执行下单失败 entrustno:{},无法下单,error:{}".format(order.entrustNo, comm.ErrorCodeDict[rtn])
            rtnExecO.order=order
        else:
            log.info(
                "OrderInsert succeeded,req:ExchangeID:{},   InstrumentID:{},   side:{},   offset:{},   askPrice:{},   Volume:{},   EntrustNo:{} ".format(
                    ExchangeID, order.symbol, side, offset, order.askPrice, order.askQty, order.entrustNo))
            # 订单存入字典

            order.tmpStatus = comm.NEW_ORDER
            order.status = comm.NEW_ORDER
            while True:
                orderTrade = self.queueRtnOrder.get()
                log.info("ctp td receive order from ctp,{}".format(orderTrade))
                if orderTrade.orderRef != order.orderRef:
                    log.info("order entrustno error,new order:{}  recv order:{}".format(order.entrustNo, orderTrade.orderRef))
                    continue
                if orderTrade.dataType == comm.DATA_TYPE_ORDER:
                    order.orderSysID = orderTrade.orderSysID
                    order.tmpStatus = comm.STATUS_DICT[orderTrade.status]
                    order.statusMsg = orderTrade.statusMsg
                elif orderTrade.dataType == comm.DATA_TYPE_TRADE:
                    order.bidPrice = orderTrade.bidPrice
                    order.bidVol = orderTrade.bidVol

                if order.tmpStatus == comm.REJECTED or order.tmpStatus == comm.CANCELED or (
                        order.tmpStatus == comm.AllTrade and order.askQty == order.bidVol):
                    # 订单逐手交易, 废单和 撤单(全撤/部撤) 以及全部成交为终态
                    order.status = order.tmpStatus
                # 取订单,校验状态,达成终态后返回
                if order.status > 3:
                    if order.status == 5:
                        rtnExecO.reqSuccess = True
                        rtnExecO.msg = "未成已撤"
                    elif order.status == 6:
                        rtnExecO.reqSuccess = False
                        rtnExecO.msg = "废单"
                    elif order.status == 4:
                        rtnExecO.reqSuccess = True
                        rtnExecO.msg = "全成"
                        self.after_trade_update_position(order,offset)
                    break
            log.info("ctp order exec success,order:{}".format(order))
            rtnExecO.order = order

        return rtnExecO




    def OnRspQryOrder(self, pOrder: tdapi.CThostFtdcOrderField, pRspInfo: "CThostFtdcRspInfoField", nRequestID: "int",
                      bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"ctp OnRspQryOrder failed: {pRspInfo.ErrorMsg}")
            self.semaphore.release()
            return

        if pOrder is not None:
            order=models.Order()
            entrustNo = int(pOrder.OrderRef)
            order.entrustNo=entrustNo
            order.orderRef=pOrder.OrderRef
            order.orderSysID=pOrder.OrderSysID.lstrip()
            order.status=comm.STATUS_DICT[pOrder.OrderStatus]
            order.statusMsg=pOrder.StatusMsg
            self.OrderDict[order.orderSysID]=order
            log.info(f"ctp qry orders,OnRspQryOrder:"
                     f"UserID={pOrder.UserID} "
                     f"BrokerID={pOrder.BrokerID} "
                     f"InvestorID={pOrder.InvestorID} "
                     f"ExchangeID={pOrder.ExchangeID} "
                     f"InstrumentID={pOrder.InstrumentID} "
                     f"Direction={pOrder.Direction} "
                     f"CombOffsetFlag={pOrder.CombOffsetFlag} "
                     f"CombHedgeFlag={pOrder.CombHedgeFlag} "
                     f"OrderPriceType={pOrder.OrderPriceType} "
                     f"LimitPrice={pOrder.LimitPrice} "
                     f"VolumeTotalOriginal={pOrder.VolumeTotalOriginal} "
                     f"FrontID={pOrder.FrontID} "
                     f"SessionID={pOrder.SessionID} "
                     f"OrderRef={pOrder.OrderRef} "
                     f"TimeCondition={pOrder.TimeCondition} "
                     f"GTDDate={pOrder.GTDDate} "
                     f"VolumeCondition={pOrder.VolumeCondition} "
                     f"MinVolume={pOrder.MinVolume} "
                     f"RequestID={pOrder.RequestID} "
                     f"InvestUnitID={pOrder.InvestUnitID} "
                     f"CurrencyID={pOrder.CurrencyID} "
                     f"AccountID={pOrder.AccountID} "
                     f"ClientID={pOrder.ClientID} "
                     f"IPAddress={pOrder.IPAddress} "
                     f"MacAddress={pOrder.MacAddress} "
                     f"OrderSysID={pOrder.OrderSysID.lstrip()} "
                     f"OrderStatus={pOrder.OrderStatus} "
                     f"StatusMsg={pOrder.StatusMsg} "
                     f"VolumeTotal={pOrder.VolumeTotal} "
                     f"VolumeTraded={pOrder.VolumeTraded} "
                     f"OrderSubmitStatus={pOrder.OrderSubmitStatus} "
                     f"TradingDay={pOrder.TradingDay} "
                     f"InsertDate={pOrder.InsertDate} "
                     f"InsertTime={pOrder.InsertTime} "
                     f"UpdateTime={pOrder.UpdateTime} "
                     f"CancelTime={pOrder.CancelTime} "
                     f"UserProductInfo={pOrder.UserProductInfo} "
                     f"ActiveUserID={pOrder.ActiveUserID} "
                     f"BrokerOrderSeq={pOrder.BrokerOrderSeq} "
                     f"TraderID={pOrder.TraderID} "
                     f"ClientID={pOrder.ClientID} "
                     f"ParticipantID={pOrder.ParticipantID} "
                     f"OrderLocalID={pOrder.OrderLocalID} "
                     )

        if bIsLast == True:
            self.semaphore.release()

    def QryOrder(self):
        # 查询订单
        req = tdapi.CThostFtdcQryOrderField()
        req.BrokerID = self.broker
        req.InvestorID = self.user
        # req.InstrumentID = InstrumentID
        return self.api.ReqQryOrder(req, 0)

    def getQryOrders(self):

        rtnNo = self.QryOrder()
        if rtnNo != 0:
            # self.semaphore.release()
            errmsg = comm.ErrorCodeDict[rtnNo]
            log.warning("ctp qry orders  fail, errmsg:{}".format(errmsg))
            return False
        success = self.semaphore.acquire(timeout=30)
        if success is False:
            log.error("ctp qry orders failed ,timeout!!")
            return False
        log.info("ctp qry orders  done")
        return True

    def OnRspQryTrade(self, pTrade: tdapi.CThostFtdcTradeField, pRspInfo: "CThostFtdcRspInfoField", nRequestID: "int",
                      bIsLast: "bool") -> "void":
        if pRspInfo is not None and pRspInfo.ErrorID != 0:
            log.warning(f"ctp OnRspQryTrade failed: {pRspInfo.ErrorMsg}")
            self.semaphore.release()
            return
        if pTrade is not None:
            OrderSysID=pTrade.OrderSysID.lstrip()
            if OrderSysID in self.OrderDict:
                self.OrderDict[OrderSysID].bidPrice = pTrade.Price
                self.OrderDict[OrderSysID].bidVol = pTrade.Volume
            log.info(f"ctp qry OnRspQryTrade:"
                     f"BrokerID={pTrade.BrokerID} "
                     f"InvestorID={pTrade.InvestorID} "
                     f"ExchangeID={pTrade.ExchangeID} "
                     f"InstrumentID={pTrade.InstrumentID} "
                     f"Direction={pTrade.Direction} "
                     f"OffsetFlag={pTrade.OffsetFlag} "
                     f"HedgeFlag={pTrade.HedgeFlag} "
                     f"Price={pTrade.Price}  "
                     f"Volume={pTrade.Volume} "
                     f"OrderSysID={OrderSysID} "
                     f"OrderRef={pTrade.OrderRef} "
                     f'TradeID={pTrade.TradeID} '
                     f'TradeDate={pTrade.TradeDate} '
                     f'TradeTime={pTrade.TradeTime} '
                     f'ClientID={pTrade.ClientID} '
                     f'TradingDay={pTrade.TradingDay} '
                     f'OrderLocalID={pTrade.OrderLocalID} '
                     f'BrokerOrderSeq={pTrade.BrokerOrderSeq} '
                     f'InvestUnitID={pTrade.InvestUnitID} '
                     f'ParticipantID={pTrade.ParticipantID} '
                     )

        if bIsLast == True:
            self.semaphore.release()

    def QryTrade(self):
        req = tdapi.CThostFtdcQryTradeField()
        req.BrokerID = self.broker
        req.InvestorID = self.user
        return self.api.ReqQryTrade(req, 0)

    def getQryTrades(self):
        rtnNo = self.QryTrade()
        if rtnNo != 0:
            errmsg = comm.ErrorCodeDict[rtnNo]
            log.warning("ctp qry trades  fail, errmsg:{}".format(errmsg))
            return False
        success = self.semaphore.acquire(timeout=30)
        if success is False:
            log.error("ctp qry trade failed ,timeout!!")
            return False
        log.info("ctp qry trade  done")
        return True

    def load_orders_from_ctp(self):
        qryStatus = self.getQryOrders()
        if qryStatus is False:
            return False,{}
        qryStatus = self.getQryTrades()
        if qryStatus is False:
            return False,{}
        return True,self.OrderDict

    def Run(self):
        self.api.Init()
        while self.isConnected != True:
            log.warning("ctp connect failed, wait!!!")
            time.sleep(1)
            # 认证
        self.GetCTPAuthenticateStatus()

        # 登入
        self.GetCTPLoginStatus()

        # 检查结算单
        self.CheckSettlementInfo()

        # 查询 ctp持仓
        success=self.load_positions_from_ctp()
        if not success:
            log.error("ctp load positions fail!!!!!!!!!!!!!!!!!!!")
            time.sleep(2)
            exit(-1)
        # self.GetCtpPosition()
        # self.getPosition("au2506")
        # self.Join()
        # self.GetPrice("SHFE","au2506",comm.ACTION_SHORT,comm.OFFSET_OPEN)
        # self.QryPrice("SHFE","au2506")
