# src/app.py
import os

from flask_restful import Api, Resource,reqparse
from flask import Flask, request, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from threading import Event, Lock
import json
import logging
import time
import subprocess
from threading import Event
from package.logger.logger import setup_logger

log = logging.getLogger('root')



def get_db_config_str(cfg):
    user = cfg['user']
    pwd = cfg['pwd']
    host = cfg['host']
    db = cfg['db']
    charset = cfg['charset']
    return 'mysql+pymysql://{}:{}@{}/{}?charset={}'.format(user, pwd, host, db, charset)


def restart_edge(url="http://127.0.0.1:30000"):
    os.system("taskkill /f /im msedge.exe >nul 2>&1")

    time.sleep(1.5)
    edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(edge_path):
        edge_path = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    if os.path.exists(edge_path):
        subprocess.Popen([edge_path, url])
    else:
        print("❌ 找不到 Edge 浏览器路径，请确认是否安装 Edge")



def create_app(server):
    # 创建 Flask 实例
    template_folder = os.path.join(os.path.dirname(__file__), '../../templates')
    app = Flask(__name__, template_folder=template_folder)
    app.config['SQLALCHEMY_DATABASE_URI'] = get_db_config_str(server.dbConfig)  # 修改为实际数据库配置
    app.config.update(SESSION_COOKIE_NAME="ctp-mt5")
    app.secret_key = '+=*&^%$#@!..>?'

    db = SQLAlchemy(app)

    # 初始化 Flask-RESTFUL
    api = Api(app)
    # socketio = SocketIO(app,cors_allowed_origins="*")
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    class ParentOrder(db.Model):
        __tablename__ = server.dbConfig['table']  # 与用户表名保持一致
        __table_args__ = {'mysql_charset': 'gb2312'}

        id = db.Column(db.Integer, primary_key=True)
        # uuid = db.Column(db.String(36), unique=True)
        entrustNo = db.Column(db.Integer, nullable=False)
        longShort = db.Column(db.String(30))

        # 点差字段
        spread = db.Column(db.Float, nullable=False)
        realOpenSpread = db.Column(db.Float, nullable=False)
        closeSpread = db.Column(db.Float, nullable=False)
        realCloseSpread = db.Column(db.Float)

        # 状态字段
        status = db.Column(db.Integer)
        created_at = db.Column(db.DateTime)
        closed_at = db.Column(db.DateTime)

        # 数量字段
        askCtpQty = db.Column(db.Integer, nullable=False)
        askMt51Qty = db.Column(db.Float, nullable=False)
        askMt52Qty = db.Column(db.Float, nullable=False)

        fixedCloseSpread = db.Column(db.Boolean, nullable=False, default=False)
        is_manual = db.Column(db.Boolean, nullable=False, default=False)
    # 定义策略资源路由
    class StrategyResource(Resource):
        def get(self):
            action = request.args.get('action')
            log.info(["get request get :: ", {'ip': request.remote_addr, 'url': request.url}, request.get_json])

            if action == 'strategy':
                longShort = request.args.get('longShort')
                data = server.get_longshort_strategy(longShort)
                return json.dumps(data), 200

            if action == 'closePositions':
                closeStatus = server.closeAllOrders()
                msg = "清仓成功" if closeStatus else "清仓失败"
                return {'message': msg}, 200

            return {'message': 'Invalid action'}, 404

        def post(self):
            log.info(["get request post :: ", {'ip': request.remote_addr, 'url': request.url}, request.get_json])
            action = request.args.get('action')
            if action == 'strategy':
                data = request.get_json()
                server.updateStrategy(data)
                return {'message': '更新成功'}, 200

            if action == 'update_base_strategy':
                data = request.get_json()
                server.update_base_strategy(data)
                return {'message': '策略更新成功'}, 200

            if action == 'update_core_strategy':
                data = request.get_json()
                server.update_core_strategy(data)
                return {'message': '策略更新成功'}, 200

            if action == 'update_time_strategy':
                data = request.get_json()
                server.update_time_strategy(data)
                return {'message': '策略更新成功'}, 200

            if action == 'stop_strategy':
                data = request.get_json()
                status=server.stop_strategy(data)
                return {'message': '策略停止','status':status}, 200

            if action == 'start_strategy':
                data = request.get_json()
                status=server.start_strategy(data)
                return {'message': '策略启动','status':status}, 200

            if action=='update_is_manual_close_flow_strategy':
                data= request.get_json()
                server.update_is_manual_close_flow_strategy(data['longShort'],data['follow'])
                return {'message': '策略启动', 'status': True}, 200
            if action=='update_total_cost':
                data= request.get_json()
                server.update_total_cost(data['total_cost'])
                return {'message': '策略启动', 'status': True}, 200
            if action=='reconnect_mt5':
                mt5_ter=request.args.get('mt5')
                server.web_reconnect_mt5(mt5_ter)
                return {'message': '重连成功', 'status': True}, 200

            return {'message': 'Invalid action'}, 404

    class ParentOrders(Resource):
        # 查询订单 get (终态/非终态)
        # 自动刷新 非终态委托
        def get(self):
            log.info(["get request get :: ", {'ip': request.remote_addr, 'url': request.url}, request.get_json])
            action = request.args.get('action')
            if action == "qry_orders":
                parser = reqparse.RequestParser()
                parser.add_argument('status', type=int, location='args', required=True) # 0 非终态 , 1终态
                args = parser.parse_args()
                query = ParentOrder.query
                if args.get('status', None) in (0, 1):  # 显式设置默认值None
                    status_filter = ParentOrder.status < 8 if args['status'] == 0 else ParentOrder.status >= 8
                    query = query.filter(status_filter)
                if args.get('status', None) == 0:
                    results = query.order_by(ParentOrder.entrustNo.desc()).all()
                if args.get('status', None) == 1:
                    results = query.order_by(ParentOrder.closed_at.desc()).all()

                return {'data': [{'entrustNo': order.entrustNo,'longShort':order.longShort,
                                  'spread':order.spread,
                                  'realOpenSpread':order.realOpenSpread,
                                  'closeSpread':order.closeSpread,
                                  'realCloseSpread':order.realCloseSpread,
                                  'askCtpQty':order.askCtpQty,
                                  'askMt51Qty':order.askMt51Qty,
                                  'askMt52Qty':order.askMt52Qty,
                                  'status': order.status,
                                  'fixedCloseSpread': order.fixedCloseSpread,
                                  'is_manual': order.is_manual,
                                  'created_at': order.created_at.isoformat(),
                                  'closed_at': order.closed_at.isoformat() if order.created_at else None} for order in results]},200
            if action == "qry_error_order":
                error_orders=server.get_error_orders()
                return {'data': error_orders}, 200
        def post(self):
            log.info(["get request post :: ", {'ip': request.remote_addr, 'url': request.url}, request.get_json()])
            action = request.args.get('action')
            if action == "delete_history_orders":
                try:
                    # 直接获取整型数组参数
                    entrust_nos = request.json  # 前端直接发送数组时无需用get('key')
                    log.info("delete request post :: entrustNos:{}".format(entrust_nos))
                    # 执行批量删除
                    delete_count = ParentOrder.query.filter(ParentOrder.entrustNo.in_(entrust_nos)).delete()

                    db.session.commit()
                    return {"status": True, "message": f"成功删除{delete_count}条历史委托", "deleted": int(delete_count)}, 200

                except Exception as e:
                    db.session.rollback()
                    return {"status": False, "message": f"删除失败: {str(e)}"}, 500

                return {"status": False}, 400
            if action == 'update_orders':
                # 修改订单 post , 修改委托为 终态,直接更新数据库和内存
                # 传入pid 数组
                try:
                    # 直接获取整型数组参数
                    entrust_nos = request.json  # 前端直接发送数组时无需用get('key')
                    log.info("update order  request post :: entrustNos:{}".format(entrust_nos))
                    # 执行批量删除
                    update_count = ParentOrder.query.filter(ParentOrder.entrustNo.in_(entrust_nos)).update({'status': 8}, synchronize_session='fetch')

                    db.session.commit()
                    server.web_update_orders(entrust_nos)
                    return {"status": True, "message": f"成功更新{update_count}条委托"}, 200

                except Exception as e:
                    db.session.rollback()
                    return {"status": False, "message": f"删除失败: {str(e)}"}, 500

                return {"status": False}, 400

            if action == 'clear_positions':
                longShort = request.args.get('longShort')
                success,successed_n=server.close_all_positions(longShort)
                return {"status": success, 'message': f'清仓失败,只清仓完成{successed_n}'}, 200
            if action == 'close_orders':
                # 平订单 post ,可能 平仓失败
                # 传入pid 数组
                entrust_nos = request.get_json()
                success,n=server.close_orders(entrust_nos)
                msg = f"需要平仓{len(entrust_nos)}个,失败{len(entrust_nos)-n}"
                #更新成功几个,失败几个
                return {"status": success, 'message': msg}, 200
            if action == 'open_order':
                data= request.get_json()
                success,msg=server.web_open_order(data['long_short'],data['open_spread'])
                return {"status": success, 'message': msg}, 200
            if action == 'clear_all_data':
                # 清仓 post ,可能清仓失败(是否轮询)
                success,errmsg=server.clear_all_data()
                if success:
                #更新成功几个,失败几个
                    msg='更新成功'
                else:
                    msg=errmsg

                return {"status": success,'message': msg}, 200

            if action == 'exc_error_orders':
                error_orders = request.get_json()
                success_n=server.exc_add_error_orders(error_orders)
                return {"status": True, "message": f"成功处理 {success_n} ,失败{len(error_orders)-success_n}"}, 200
            if action == 'update_close_spread_type':
                data = request.get_json()
                entrust_no = data.get('entrustNo')
                fixed_spread = data.get('fixedCloseSpread')
                update_type = data.get('updateType')
                server.update_close_spread_type(entrust_no,fixed_spread,update_type)
                return {"status": True, "message": f"成功处理"}, 200
            if action == 'deal_error_orders':
                data = request.get_json()
                entrust_no = data.get('entrustNo')
                deal_type = data.get('deal_type')
                success,errmsg=server.deal_error_orders(entrust_no,deal_type)
                return {"status": success, "message": errmsg}, 200
    class Risk(Resource):
        def get(self):
            log.info(["get request get :: ", {'ip': request.remote_addr, 'url': request.url}, request.get_json])
            action = request.args.get('action')
            if action == "get_risk":
                data = server.get_risk_config()
                return data,200
            return {"status": False,"message":"未找到页面"}, 404
        def post(self):
            log.info(["get request post :: ", {'ip': request.remote_addr, 'url': request.url}, request.get_json])
            action = request.args.get('action')
            if action == "update_risk":
                data = request.get_json()
                server.update_risk_config(data)
                return {"status": True,"message":"更新成功"},200
            return {"status": False, "message": "未找到页面"}, 404
    # 注册路由
    api.add_resource(StrategyResource, '/strategy')
    api.add_resource(ParentOrders, '/parent_orders')
    api.add_resource(Risk, '/risk')

    @app.route('/')
    def index():
        return render_template('index.html')

    client_events = {}  # {sid: Event()}
    client_events_lock = Lock()
    def generate_data_loop(sid):
        with app.app_context():
            try:
                while True:
                    with client_events_lock:
                        event = client_events.get(sid)
                        if event is None or event.is_set():
                            break
                    data = server.market_websocket_queue.get()
                    # log.info(f"market::{data}")
                    socketio.emit('update', data, namespace='/realtime', room=sid)
                    time.sleep(0.01)
            except Exception as e:
                log.info(f"[ERROR] Data loop for {sid} crashed: {e}")
    @socketio.on('connect', namespace='/realtime')
    def handle_connect():
        sid = request.sid
        with client_events_lock:
            if sid in client_events:
                client_events[sid].set()  # 确保旧线程退出
                time.sleep(0.1)
            client_events[sid] = Event()
        socketio.start_background_task(generate_data_loop, sid)
        log.info(f"[INFO] Client connected: {sid}")
    @socketio.on('disconnect', namespace='/realtime')
    def handle_disconnect():
        sid = request.sid
        with client_events_lock:
            event = client_events.get(sid)
            if event:
                event.set()
        def delayed_cleanup(sid_to_remove):
            time.sleep(1)
            with client_events_lock:
                client_events.pop(sid_to_remove, None)
            log.info(f"[INFO] Client {sid_to_remove} cleaned up.")
        socketio.start_background_task(delayed_cleanup, sid)
        log.info(f"[INFO] Client disconnected: {sid}")
    return socketio,app
