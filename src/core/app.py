# src/app.py
import os
from flask_restful import Api, Resource
from flask import Flask, request, render_template
import json
import logging

from package.logger.logger import setup_logger

log = logging.getLogger('root')


def create_app(server):
    # 创建 Flask 实例
    template_folder = os.path.join(os.path.dirname(__file__), '../../templates')
    app = Flask(__name__, template_folder=template_folder)
    app.config.update(SESSION_COOKIE_NAME="ctp-mt5")
    app.secret_key = '+=*&^%$#@!..>?'

    # 初始化 Flask-RESTFUL
    api = Api(app)

    # 定义策略资源路由
    class StrategyResource(Resource):
        def get(self):
            action = request.args.get('action')
            log.info(["get request get :: ", {'ip': request.remote_addr, 'url': request.url}, request.form])

            if action == 'strategy':
                data = server.getStrategy()
                return json.dumps(data), 200

            if action == 'checkPosition':
                server.reCheckPosition()
                return {'message': '持仓检查成功'}, 200

            if action == 'closePositions':
                closeStatus = server.closeAllOrders()
                msg = "清仓成功" if closeStatus else "清仓失败"
                return {'message': msg}, 200

            return {'message': 'Invalid action'}, 404

        def post(self):
            log.info(["get request post :: ", {'ip': request.remote_addr, 'url': request.url}, request.form])
            action = request.args.get('action')
            if action == 'strategy':
                data = request.get_json()
                server.updateStrategy(data)
                return {'message': '更新成功'}, 200
            return {'message': 'Invalid action'}, 404

    # 注册路由
    api.add_resource(StrategyResource, '/strategy')

    @app.route('/')
    def index():
        return render_template('index.html')

    return app
