from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_restful import Resource, Api, reqparse, abort
from flask_bcrypt import Bcrypt
import os
import subprocess as sp

app = Flask(__name__)

app.config.update(dict(
    DEBUG=True,
    SECRET_KEY=b"\xba\x04\x91Z.\x07[\x9b#Tt:m\xd6\x1a\xb4\xdb\x037\xa9\xac\xfc\xd6'",
    TEMPLATES_AUTO_RELOAD=True,
    SQLALCHEMY_DATABASE_URI='sqlite:///database.db',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    BUNDLE_ERRORS=True,
))

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
api = Api(app)


class Repo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    region = db.Column(db.String(50), nullable=True)
    endpoint = db.Column(db.String(200))
    bucket = db.Column(db.String(100))
    access_id = db.Column(db.String(200))
    access_key = db.Column(db.String(200))

    def __repr__(self):
        return '<Bucket {bucket}>'.format(bucket=self.bucket)

    def to_json(self):
        return {
            'id': self.id,
            'region': self.region,
            'endpoint': self.endpoint,
            'bucket': self.bucket,
        }


def repo_exist(repo_id):
    """
    检查数据库中是否存在仓库记录
    :param repo_id: 仓库 ID
    :return:
    """
    if not Repo.query.get(repo_id):
        abort(404, message="仓库不存在")


def repo_setenv(repo, password):
    """
    设置 restic 所需的仓库环境变量
    :param repo: 一个 Repo 对象
    :param password: 仓库密码
    :return:
    """
    os.putenv('AWS_ACCESS_KEY_ID', repo.access_id)
    os.putenv('AWS_SECRET_ACCESS_KEY', repo.access_key)
    os.putenv('RESTIC_REPOSITORY', 's3:' + repo.endpoint + '/' + repo.bucket)
    os.putenv('RESTIC_PASSWORD', password)


def repo_select(repo_id, password):
    """
    选择要操作的仓库
    :param repo_id: 仓库 ID
    :param password: 仓库密码
    :return:
    """
    repo_exist(repo_id)
    repo_setenv(Repo.query.get(repo_id), password)


class RepoManage(Resource):
    """
    管理存储仓库
    """

    def get(self):
        """
        从数据库获取仓库列表
        :return:
        """
        repos = Repo.query.all()
        if repos:
            return jsonify([x.to_json() for x in repos])
        else:
            return {"message": "暂无可用的仓库"}

    # 新增仓库
    def post(self):
        """
        restic 初始化仓库并将仓库信息写入数据库
        :return:
        """
        parser = reqparse.RequestParser()
        parser.add_argument('bucket', required=True, help='Bucket 必须填写！')
        parser.add_argument('region', help='region 为可选项')
        parser.add_argument('endpoint', required=True, help='Endpoint 必须填写！')
        parser.add_argument('access_id', required=True, help='Access id 必须填写！')
        parser.add_argument('access_key', required=True,
                            help='Access key 必须填写！')
        parser.add_argument('repo_passwd', required=True, help='仓库密码必须填写！')
        parser.parse_args()

        if Repo.query.filter_by(bucket=request.form['bucket'], endpoint=request.form['endpoint']).first():
            return {'error': '仓库已存在，请勿重复添加！'}
        else:
            repo = Repo(
                bucket=request.form['bucket'],
                region=request.form['region'],
                endpoint=request.form['endpoint'],
                access_id=request.form['access_id'],
                access_key=request.form['access_key'],
            )

            # 由于该仓库尚未存入数据库，因此不能使用 `repo_select()` 方法。
            repo_setenv(repo, request.form['repo_passwd'])

            init_repo_cmd = 'restic init'.split()
            init_repo = sp.run(init_repo_cmd, stdout=sp.PIPE,
                               stderr=sp.PIPE, universal_newlines=True)

            if init_repo.returncode:
                if 'already initialized' in init_repo.stderr:
                    db.session.add(repo)
                    db.session.commit()
                    return {"message": "成功添加了一个已被初始化的仓库"}
                return {"error": init_repo.stderr, "returncode": init_repo.returncode}
            else:
                db.session.add(repo)
                db.session.commit()
                return {"stdout": init_repo.stdout,
                        "warning": "存储仓库创建成功，请务必记住并妥善保管仓库密码，遗失仓库密码将无法访问仓库中所有的数据！",
                        "returncode": init_repo.returncode}

    def delete(self):
        """
        从数据库中删除指定的仓库
        """
        parser = reqparse.RequestParser()
        parser.add_argument('repo_id', required=True, help='请指定要删除的仓库ID')
        parser.parse_args()

        repo_id = request.form.get('repo_id')

        repo_exist(repo_id)

        db.session.delete(Repo.query.get(repo_id))
        db.session.commit()

        return {"stdout": "仓库删除成功"}


api.add_resource(RepoManage, '/repos/')


class BackupManage(Resource):
    """
    备份功能
    """

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('repo_id', required=True, help='请指定仓库ID')
        parser.add_argument('repo_passwd', required=True, help='请指定仓库密码')
        parser.add_argument('file_path', required=True, help='请指定备份路径')
        parser.parse_args()

        repo_id = request.form.get('repo_id')
        repo_passwd = request.form.get('repo_passwd')
        file_path = request.form.get('file_path')

        repo_exist(repo_id)
        repo_select(repo_id, repo_passwd)

        backup_cmd = 'restic backup {}'.format(file_path).split()
        backup = sp.run(backup_cmd, stderr=sp.PIPE, stdout=sp.PIPE, universal_newlines=True)

        if backup.returncode:
            return {"error": backup.stderr, "returncode": backup.returncode}
        else:
            return {"message": backup.stdout, "returncode": backup.returncode}


api.add_resource(BackupManage, '/backup/')


class SnapshotManage(Resource):
    """
    快照管理
    """
    def post(self):
        """
        获取指定仓库的快照列表
        """
        parser = reqparse.RequestParser()
        parser.add_argument('repo_id', required=True, help='请指定仓库ID')
        parser.add_argument('repo_passwd', required=True, help='请指定仓库密码')
        parser.parse_args()

        repo_id = request.form['repo_id']
        repo_select(repo_id, request.form['repo_passwd'])

        snapshots_cmd = 'restic snapshots --json'.split()
        snapshots = sp.run(snapshots_cmd, stdout=sp.PIPE, stderr=sp.PIPE, universal_newlines=True)

        if snapshots.returncode:
            if 'wrong password' in snapshots.stderr:
                return {"error": "仓库密码或密钥不正确"}
            else:
                return {"error": snapshots.stderr, "returncode": snapshots.returncode}
        else:
            return {"data": snapshots.stdout, "returncode": snapshots.returncode}


    def delete(self):
        """
        删除指定ID的快照
        :return:
        """
        parser = reqparse.RequestParser()
        parser.add_argument('repo_id', required=True, help='请指定仓库ID')
        parser.add_argument('repo_passwd', required=True, help='请指定仓库密码')
        parser.add_argument('snapshot_id', required=True, help='请指定要删除的快照ID')
        parser.parse_args()

        repo_id = request.form['repo_id']
        snapshot_id = request.form['snapshot_id']
        repo_select(repo_id, request.form['repo_passwd'])

        delete_cmd = 'restic forget --prune {}'.format(snapshot_id).split()
        delete = sp.run(delete_cmd, stdout=sp.PIPE, stderr=sp.PIPE, universal_newlines=True)

        if delete.returncode:
            return {"error": delete.stderr, "returncode": delete.returncode}
        else:
            return {"stdout": delete.stdout, "returncode": delete.returncode}

api.add_resource(SnapshotManage, '/snapshots/')