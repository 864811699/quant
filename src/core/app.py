# src/app.py
import os
from flask_restful import Api, Resource
from flask import Flask, request, render_template

import  json


from package.logger.logger import log

def create_app(server):
    # 创建 Flask 实例
    template_folder=os.path.join(os.path.dirname(__file__), '../../templates')
    app = Flask(__name__, template_folder=template_folder)
    app.config.update(SESSION_COOKIE_NAME="ctp-mt5")
    app.secret_key = '+=*&^%$#@!..>?'

    # 初始化 Flask-RESTful
    api = Api(app)

    # 定义策略资源路由
    class StrategyResource(Resource):
        def get(self):
            action = request.args.get('action')
            if action == 'strategy':
                log.info(["get request get :: ", {'ip': request.remote_addr, 'url': request.url}, request.form])
                data = server.getStrategy()
                return json.dumps(data), 200
            if action == 'checkPosition':
                log.info(["get request get :: ", {'ip': request.remote_addr, 'url': request.url}, request.form])
                server.reCheckPosition()
                return {'message': '重启策略成功'}, 200
            return {'message': 'Invalid action'}, 404
        def post(self):
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
