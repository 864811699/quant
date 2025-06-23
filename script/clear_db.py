import toml
import sys
from dataclasses import dataclass
from sqlalchemy import create_engine, text

child_cfg_path="../etc/ctp.toml"
parent_cfg_path="../etc/server.toml"
def check_fail_op():
    user_input = input("请输入 1 继续执行，输入其他任意键退出：")
    if user_input.strip() != '1':
        print("退出脚本。")
        sys.exit(0)

def clear_db(cfg_path):
    with open(cfg_path, 'r', encoding='utf-8') as file:
        cfg = toml.load(file)
        user = cfg['db']['user']
        pwd = cfg['db']['pwd']
        host = cfg['db']['host']
        db = cfg['db']['db']
        charset = cfg['db']['charset']
        table= cfg['db']['table']
        connect_str = 'mysql+pymysql://{}:{}@{}/{}?charset={}'.format(user, pwd, host, db, charset)
        engine = create_engine(connect_str, pool_size=10, max_overflow=20, pool_recycle=3600)
        try:
            with engine.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
                return True,""
        except Exception as e:
            return False,str(e)




if __name__ == '__main__':
    check_fail_op()
    clear_db(child_cfg_path)
    clear_db(parent_cfg_path)