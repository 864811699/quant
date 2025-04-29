from dataclasses import dataclass
from sqlalchemy import create_engine, text
from package.zmq import models


class dbServer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.engine = self._create_engine()
    def _create_engine(self):
        user = self.cfg['user']
        pwd = self.cfg['pwd']
        host = self.cfg['host']
        db = self.cfg['db']
        charset = self.cfg['charset']
        connect_str = 'mysql+pymysql://{}:{}@{}/{}?charset={}'.format(user, pwd, host, db, charset)
        engine = create_engine(connect_str, pool_size=10, max_overflow=20, pool_recycle=3600)
        return engine
    def get_db(self):
        return self.engine

    def create_child_table(self):
        dest = self.get_db()

        schema = """
            CREATE TABLE IF NOT EXISTS `{}` (
            `id` int unsigned NOT NULL auto_increment,
            `uuid` VARCHAR(36) NOT NULL UNIQUE COMMENT '订单UUID',
            `account`  varchar(30) NOT NULL COMMENT '交易账号',
            `symbol`  varchar(30) NOT NULL COMMENT '交易标的',
            `orderRef`  varchar(30) NOT NULL COMMENT '订单引用',
            `pEntrustNo` int unsigned NOT NULL  COMMENT '母单编号',
            `entrustNo` int unsigned NOT NULL  COMMENT '子单编号',
            `longShort`  varchar(30) NOT NULL   COMMENT 'ACTION_LONG / ACTION_SHORT',
            `openClose` varchar(30) NOT NULL    COMMENT 'OFFSET_OPEN /OFFSET_CLOSE',
            `askPrice`  float NOT NULL    COMMENT '委托价',
            `parentAskQty`    float unsigned NOT  NULL  COMMENT '委托量',
            `askQty`    float unsigned NOT  NULL  COMMENT '委托量',
            `orderSysID` varchar(30) DEFAULT NULL    COMMENT '委托ID(撤单用)',
            `bidPrice` float DEFAULT NULL    COMMENT '成交价',
            `bidVol` float unsigned DEFAULT NULL    COMMENT '成交量',
            `Status` int unsigned DEFAULT NULL    COMMENT '委托状态',
            `statusMsg` varchar(256) DEFAULT NULL    COMMENT '状态信息',
            `reqTime` datetime DEFAULT NULL    COMMENT  '请求时间',
            `rspTime` datetime DEFAULT NULL    COMMENT '回报时间',
            primary key (id),
            UNIQUE KEY `uuid_UNIQUE` (`uuid`),
            KEY `pEntrustNo` (`pEntrustNo`),
            KEY `entrustNo` (`entrustNo`)
            ) ENGINE=INNODB DEFAULT CHARSET=utf8;
        """.format(self.cfg['table'])

        with dest.connect() as connection:
            # 执行 SQL 语句
            res = connection.execute(text(schema))
        return res

    def create_parent_table(self):
        dest = self.get_db()

        schema = """
            CREATE TABLE IF NOT EXISTS `{}` (
            `id` int unsigned NOT NULL auto_increment,
            `uuid` VARCHAR(36) NOT NULL UNIQUE COMMENT '订单UUID',
            `entrustNo` int unsigned NOT NULL  COMMENT '子单编号',
            `longShort`  varchar(30) NOT NULL   COMMENT 'ACTION_LONG / ACTION_SHORT',
            `CTPAUAskPrice`  float NOT NULL    COMMENT 'ctp黄金当前价格',
            `CTPAUBidPrice`  float NOT NULL    COMMENT 'ctp黄金当前价格',
            `MT5AUAskPrice`  float NOT NULL    COMMENT '伦敦金当前价格',
            `MT5AUBidPrice`  float NOT NULL    COMMENT '伦敦金当前价格',
            `USDAskPrice`  float NOT NULL    COMMENT '汇率价格',
            `USDBidPrice`  float NOT NULL    COMMENT '汇率价格',
            `spread`  float NOT NULL    COMMENT '当前点差',
            `realOpenSpread`  float NOT NULL    COMMENT '实际开仓点差',
            `closeSpread`  float NOT NULL    COMMENT '预期平仓点差',
            `realCloseSpread`  float DEFAULT NULL    COMMENT '实际平仓点差',
            `status` int unsigned DEFAULT NULL    COMMENT '委托状态',
            `created_at` datetime DEFAULT NULL    COMMENT  '请求时间',
            `closed_at` datetime DEFAULT NULL    COMMENT '回报时间',
            `askCtpQty`    int unsigned NOT  NULL  COMMENT '期货 委托量',
            `askMt51Qty`    float  NOT  NULL  COMMENT '伦敦金 委托量',
            `askMt52Qty`    float  NOT  NULL  COMMENT '汇率 委托量',
            primary key (id),
            UNIQUE KEY `uuid_UNIQUE` (`uuid`),
            KEY `entrustNo` (`entrustNo`),
            KEY `status` (`status`)
            ) ENGINE=INNODB DEFAULT CHARSET=gb2312;
        """.format(self.cfg['table'])

        with dest.connect() as connection:
            # 执行 SQL 语句
            res = connection.execute(text(schema))

        return res

    def save_child_order(self, table, order):
        dest = self.get_db()
        sql = text("""
                INSERT INTO {} (
            uuid,account, symbol, orderRef, pEntrustNo, entrustNo, longShort, openClose, 
            askPrice,parentAskQty, askQty, orderSysID, bidPrice, bidVol, Status, statusMsg, reqTime, rspTime
        ) VALUES (
            :uuid,:account, :symbol, :orderRef, :pEntrustNo, :entrustNo, :longShort, :openClose, 
            :askPrice,:parentAskQty, :askQty, :orderSysID, :bidPrice, :bidVol, :status, :statusMsg, :reqTime, :rspTime
        )
            """.format(table))
        with dest.begin() as connection:
            # 执行 SQL 语句
            result = connection.execute(sql, {
                'uuid': order.uuid,
                "account": order.account,
                "symbol": order.symbol,
                "orderRef": order.orderRef,
                "pEntrustNo": order.pEntrustNo,
                "entrustNo": order.entrustNo,
                "longShort": order.longShort,
                "openClose": order.openClose,
                "askPrice": order.askPrice,
                "parentAskQty": order.parentAskQty,
                "askQty": order.askQty,
                "orderSysID": order.orderSysID,
                "bidPrice": order.bidPrice,
                "bidVol": order.bidVol,
                "status": order.status,
                "statusMsg": order.statusMsg,
                "reqTime": order.reqTime,
                "rspTime": order.rspTime,
            })

        return True

    def update_child_order(self, table, order):
        dest = self.get_db()
        sql = text("""
            UPDATE {}
            SET orderSysID = :orderSysID, 
                bidPrice = :bidPrice,
                bidVol = :bidVol,
                status = :status,
                statusMsg = :statusMsg,
                rspTime = :rspTime
            WHERE uuid = :uuid
        """.format(table))
        update_data = {
            "uuid": order.uuid,
            "orderSysID": order.orderSysID,
            "bidPrice": order.bidPrice,
            "bidVol": order.bidVol,
            "status": order.status,
            "statusMsg": order.statusMsg,
            "rspTime": order.rspTime,
        }
        with dest.begin() as connection:
            result = connection.execute(sql, update_data)
        return

    def save_parent_order(self, table, order):
        dest = self.get_db()
        sql = text("""
            INSERT INTO {} (
                uuid, entrustNo, longShort, 
                CTPAUAskPrice, CTPAUBidPrice, MT5AUAskPrice, MT5AUBidPrice, 
                USDAskPrice, USDBidPrice, spread, realOpenSpread, 
                closeSpread, realCloseSpread, status, created_at, closed_at,
                askCtpQty,askMt51Qty,askMt52Qty
            ) VALUES (
                :uuid, :entrustNo, :longShort, 
                :CTPAUAskPrice, :CTPAUBidPrice, :MT5AUAskPrice, :MT5AUBidPrice, 
                :USDAskPrice, :USDBidPrice, :spread, :realOpenSpread, 
                :closeSpread, :realCloseSpread, :status, :created_at, :closed_at,
                :askCtpQty,:askMt51Qty,:askMt52Qty
            )
        """.format(table))
        with dest.begin() as connection:
            # 执行 SQL 语句
            result = connection.execute(sql, {
                "uuid": order.uuid,
                "entrustNo": order.entrustNo,
                "longShort": order.longShort,
                "CTPAUAskPrice": order.CTPAUAskPrice,
                "CTPAUBidPrice": order.CTPAUBidPrice,
                "MT5AUAskPrice": order.MT5AUAskPrice,
                "MT5AUBidPrice": order.MT5AUBidPrice,
                "USDAskPrice": order.USDAskPrice,
                "USDBidPrice": order.USDBidPrice,
                "spread": order.spread,
                "realOpenSpread": order.realOpenSpread,
                "closeSpread": order.closeSpread,
                "realCloseSpread": order.realCloseSpread,
                "status": order.status,
                "created_at": order.created_at,
                "closed_at": order.closed_at,
                "askCtpQty": order.askCtpQty,
                "askMt51Qty": order.askMt51Qty,
                "askMt52Qty": order.askMt52Qty,
            })

        return True

    def update_parent_order(self, table, order):
        dest = self.get_db()
        sql = text("""
            UPDATE {} 
            SET 
                realOpenSpread = :realOpenSpread, 
                closeSpread = :closeSpread, 
                realCloseSpread = :realCloseSpread, 
                status = :status, 
                closed_at = :closed_at
            WHERE uuid = :uuid
        """.format(table))
        update_data = {
            "uuid": order.uuid,
            "realOpenSpread": order.realOpenSpread,
            "closeSpread": order.closeSpread,
            "realCloseSpread": order.realCloseSpread,
            "status": order.status,
            "closed_at": order.closed_at,
        }
        with dest.begin() as connection:
            result = connection.execute(sql, update_data)
        return

    def load_parent_orders(self, table):
        dest = self.get_db()
        orders = []
        with dest.begin() as connection:
            sql = text("SELECT * FROM {}".format(table))
            rows = connection.execute(sql).fetchall()
            if rows is not None and len(rows) > 0:
                for row in rows:
                    order = models.POrder()
                    order.uuid = row[1]
                    order.entrustNo = row[2]
                    order.longShort = row[3]
                    order.CTPAUAskPrice = row[4]
                    order.CTPAUBidPrice = row[5]
                    order.MT5AUAskPrice = row[6]
                    order.MT5AUBidPrice = row[7]
                    order.USDAskPrice = row[8]
                    order.USDBidPrice = row[9]
                    order.spread = row[10]
                    order.realOpenSpread = row[11]
                    order.closeSpread = row[12]
                    order.realCloseSpread = row[13]
                    order.status = row[14]
                    order.created_at = row[15]
                    order.closed_at = row[16]
                    order.askCtpQty=row[17]
                    order.askMt51Qty=row[18]
                    order.askMt52Qty=row[19]
                    orders.append(order)
        return orders

    def load_child_all_orders(self, table, symbol):
        dest = self.get_db()
        orders = []
        with dest.begin() as connection:
            sql = text("SELECT * FROM {} WHERE symbol = :symbol".format(table))
            rows = connection.execute(sql, {"symbol": symbol}).fetchall()
            if rows is not None and len(rows) > 0:
                for row in rows:
                    order = models.Order()
                    order.uuid = row[1]
                    order.account = row[2]
                    order.symbol = row[3]
                    order.orderRef = row[4]
                    order.pEntrustNo = row[5]
                    order.entrustNo = row[6]
                    order.longShort = row[7]
                    order.openClose = row[8]
                    order.askPrice = row[9]
                    order.parentAskQty = row[10]
                    order.askQty = row[11]
                    order.orderSysID = row[12]
                    order.bidPrice = row[13]
                    order.bidVol = row[14]
                    order.status = row[15]
                    order.statusMsg = row[16]
                    order.rspTime = row[17]
                    order.reqTime = row[18]
                    orders.append(order)
        return orders

    def load_orders_from_pEntrustNo(self, table, pEntrustNo):
        dest = self.get_db()
        orders = []
        with dest.begin() as connection:
            sql = text("SELECT * FROM {} WHERE pEntrustNo = :pEntrustNo".format(table))
            rows = connection.execute(sql, {"pEntrustNo": pEntrustNo}).fetchall()
            if rows is not None and len(rows) > 0:
                for row in rows:
                    order = models.Order()
                    order.uuid = row[1]
                    order.account = row[2]
                    order.symbol = row[3]
                    order.orderRef = row[4]
                    order.pEntrustNo = row[5]
                    order.entrustNo = row[6]
                    order.longShort = row[7]
                    order.openClose = row[8]
                    order.askPrice = row[9]
                    order.parentAskQty = row[10]
                    order.askQty = row[11]
                    order.orderSysID = row[12]
                    order.bidVol = row[13]
                    order.bidPrice = row[14]
                    order.status = row[15]
                    order.statusMsg = row[16]
                    order.rspTime = row[17]
                    order.reqTime = row[18]
                    orders.append(order)
        return orders
