import os
from flask import Flask, request,jsonify
import  json

from src.ctp.api import ctp_td
from src.ctp.api import ctp_md

from package.logger.logger import setup_logger
import logging

# 获取已经配置好的日志记录器
logger = logging.getLogger('root')

def create_app(server):
    app = Flask(__name__)

    @app.route('/get_position', methods=['GET'])
    def get_position():
        positions=server.get_positions()
        return jsonify(positions)

    @app.route('/exec_order', methods=['POST'])
    def exec_order():
        pass

    @app.route('/get_finished_order_from_pid', methods=['get'])
    def get_finished_order_from_pid():
        pass
    return app