# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, send_file, render_template_string, redirect, url_for, session
import json
import os
import uuid
import base64
import logging
import socket
import secrets
import threading
import sqlite3
from datetime import datetime, timedelta
from hashlib import pbkdf2_hmac
from threading import Lock
import time
import sys
import zipfile
import importlib.util
import io, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

log = logging.getLogger('werkzeug')
log.setLevel(logging.INFO)
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message="This is a development server")

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['SESSION_COOKIE_SECURE'] = False
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    PREFERRED_URL_SCHEME='http'
)

# ==================== 基础配置 ====================
SERVER_NAME = "〈星〉"
SKIN_FOLDER = "skins"
DB_FILE = "yggdrasil.db"
ADMIN_USER = "admin"
SESSION_TTL = 60 * 30  # 30分钟

# 获取本地 IP
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()
PORT = os.environ.get("PORT") or "5912"
SERVER_START_TIME = datetime.now()

# ==================== 密码哈希（纯标准库，无额外依赖） ====================
def hash_password(password: str) -> str:
    """返回 salt$hash_hex 格式的字符串"""
    salt = secrets.token_hex(16)
    dk = pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return salt + "$" + dk.hex()

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, dk_hex = hashed.split('$')
        dk = pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
        return dk.hex() == dk_hex
    except:
        return False

# ==================== SQLite 数据库初始化 ====================
db_lock = Lock()

def get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 更好的并发支持
    return conn

def init_db():
    os.makedirs(SKIN_FOLDER, exist_ok=True)
    with db_lock:
        conn = get_db_conn()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                uuid TEXT UNIQUE NOT NULL,
                reg_time TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                time TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                server_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                access_token TEXT NOT NULL,
                time REAL NOT NULL
            );
        ''')
        conn.commit()
        conn.close()
    # 如果还没有管理员账号，自动创建（密码从环境变量获取或启动时询问）
    admin_info = get_user(ADMIN_USER)
    if not admin_info:
        admin_pwd = os.environ.get("ADMIN_PASSWORD")
        if not admin_pwd:
            admin_pwd = input("请输入管理员密码（新创建）：").strip()
        user_uuid = str(uuid.uuid4())
        db_exec('INSERT INTO users(username,password,uuid,reg_time) VALUES(?,?,?,?)',
                (ADMIN_USER, hash_password(admin_pwd), user_uuid, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def db_exec(query, params=()):
    with db_lock:
        conn = get_db_conn()
        try:
            cur = conn.execute(query, params)
            conn.commit()
            return cur
        finally:
            conn.close()

def db_fetchall(query, params=()):
    with db_lock:
        conn = get_db_conn()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

def db_fetchone(query, params=()):
    with db_lock:
        conn = get_db_conn()
        try:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

# ==================== 用户操作 ====================
def get_user(username):
    return db_fetchone('SELECT * FROM users WHERE username=?', (username,))

def add_user(username, password):
    user_uuid = str(uuid.uuid4())
    reg_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_exec('INSERT INTO users(username,password,uuid,reg_time) VALUES(?,?,?,?)',
            (username, hash_password(password), user_uuid, reg_time))
    return user_uuid

def get_user_by_uuid(uuid_str):
    return db_fetchone('SELECT * FROM users WHERE uuid=?', (uuid_str,))

# ==================== 公告操作 ====================
def get_notices():
    return db_fetchall('SELECT * FROM notices ORDER BY id DESC')

def add_notice(title, content):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_exec('INSERT INTO notices(title,content,time) VALUES(?,?,?)', (title, content, now))

def del_notice(idx):
    # idx 是前台序号（从1开始），转换为数据库 id
    notices = get_notices()
    if 0 < idx <= len(notices):
        db_exec('DELETE FROM notices WHERE id=?', (notices[idx-1]['id'],))

# ==================== 会话管理（持久化 + 过期清理） ====================
def clean_expired_sessions():
    now = datetime.now().timestamp()
    db_exec('DELETE FROM sessions WHERE time + ? < ?', (SESSION_TTL, now))

def save_session(server_id, profile_id, access_token):
    clean_expired_sessions()
    now = datetime.now().timestamp()
    db_exec('INSERT OR REPLACE INTO sessions(server_id,profile_id,access_token,time) VALUES(?,?,?,?)',
            (server_id, profile_id, access_token, now))

def get_session(server_id):
    clean_expired_sessions()
    row = db_fetchone('SELECT * FROM sessions WHERE server_id=?', (server_id,))
    return row

# 定时清理过期会话的线程
def periodic_session_cleaner():
    while True:
        time.sleep(60)
        try:
            clean_expired_sessions()
        except:
            pass

# ==================== 皮肤安全文件名 ====================
def safe_skin_filename(filename):
    # 只允许 UUID.png 或 default_steve.png 等安全文件名
    if re.match(r'^[a-zA-Z0-9_\-\.]+$', filename) and '..' not in filename and '/' not in filename and '\\' not in filename:
        return os.path.join(SKIN_FOLDER, filename)
    return None

# ==================== Yggdrasil 路由 ====================
@app.route('/')
def metadata():
    return jsonify({
        "meta": {"serverName": SERVER_NAME},
        "skinDomains": [LOCAL_IP],
        "signaturePublicKeys": {}
    })

@app.route('/authserver/authenticate', methods=['POST'])
def authenticate():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    user = get_user(username)
    if not user:
        # 自动注册（保持原逻辑）
        add_user(username, password)
        user = get_user(username)
    if not verify_password(password, user['password']):
        return jsonify({"error": "Invalid credentials"}), 403

    access_token = str(uuid.uuid4())
    client_token = data.get('clientToken', str(uuid.uuid4()))
    user_uuid = user['uuid'].replace('-', '')

    return jsonify({
        "accessToken": access_token,
        "clientToken": client_token,
        "availableProfiles": [{"name": username, "id": user_uuid}],
        "selectedProfile": {"name": username, "id": user_uuid}
    })

@app.route('/sessionserver/session/minecraft/join', methods=['POST'])
def join():
    data = request.get_json()
    access_token = data.get("accessToken")
    profile_id = data.get("selectedProfile")
    server_id = data.get("serverId")
    if not access_token or not profile_id or not server_id:
        return "", 400
    save_session(server_id, profile_id, access_token)
    return "", 204

@app.route('/sessionserver/session/minecraft/hasJoined', methods=['GET'])
def has_joined():
    server_id = request.args.get("serverId")
    username = request.args.get("username")
    if not server_id or not username:
        return "", 400
    session_info = get_session(server_id)
    if not session_info:
        return "", 404
    user = get_user(username)
    if not user:
        return "", 404
    return jsonify({
        "id": user['uuid'].replace('-', ''),
        "name": username,
        "properties": []
    })

@app.route('/sessionserver/session/minecraft/profile/<uuid>.json')
@app.route('/sessionserver/session/minecraft/profile/<uuid>')
def get_profile(uuid):
    user = get_user_by_uuid(uuid.replace("-", ""))
    if not user:
        # 尝试用 username 当作 uuid 的情况（兼容旧请求）
        user = get_user(uuid)
        if not user:
            return "", 404

    username = user['username']
    uid = user['uuid'].replace('-', '')
    skin_path = os.path.join(SKIN_FOLDER, f"{uid}.png")
    default_path = os.path.join(SKIN_FOLDER, "default.png")
    if os.path.exists(skin_path):
        skin_url = f"http://{LOCAL_IP}:{PORT}/skin/{uid}.png"
    elif os.path.exists(default_path):
        skin_url = f"http://{LOCAL_IP}:{PORT}/skin/default.png"
    else:
        skin_url = f"http://{LOCAL_IP}:{PORT}/skin/default"

    textures = json.dumps({
        "profileId": uid,
        "profileName": username,
        "textures": {
            "SKIN": {"url": skin_url}
        }
    })
    textures_b64 = base64.b64encode(textures.encode('utf-8')).decode('utf-8')
    return jsonify({
        "id": uid,
        "name": username,
        "properties": [{
            "name": "textures",
            "value": textures_b64,
        }]
    })

@app.route('/skin/<filename>')
def get_skin(filename):
    safe_path = safe_skin_filename(filename)
    if safe_path and os.path.exists(safe_path):
        return send_file(safe_path, mimetype='image/png')
    else:
        return "", 404

@app.route('/skin/default')
def serve_default_skin():
    path = os.path.join(SKIN_FOLDER, "default_steve.png")
    if os.path.exists(path):
        return send_file(path, mimetype='image/png')
    return "", 404

# 兼容旧接口
@app.route('/skin/uuid/<uuid>')
def serve_skin_by_uuid(uuid):
    return serve_default_skin()

@app.route('/skin/<username>')
def serve_skin_by_user(username):
    return serve_default_skin()

@app.route('/authserver/refresh', methods=['POST'])
def refresh():
    return jsonify({"accessToken": str(uuid.uuid4()), "clientToken": str(uuid.uuid4())})

@app.route('/authserver/validate', methods=['POST'])
def validate():
    return "", 204

@app.route('/authserver/invalidate', methods=['POST'])
def invalidate():
    return "", 204

# ==================== 注册页面 ====================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        confirm_pwd = request.form.get('confirm_pwd', '').strip()
        if not username or len(username) < 3 or len(username) > 16:
            return f'''{CSS_STYLE}<div class=container><h3>用户名长度必须在3-16位</h3><br><a href=/register>返回</a></div>'''
        if not password or len(password) < 6:
            return f'''{CSS_STYLE}<div class=container><h3>密码长度至少6位</h3><br><a href=/register>返回</a></div>'''
        if password != confirm_pwd:
            return f'''{CSS_STYLE}<div class=container><h3>两次输入的密码不一致</h3><br><a href=/register>返回</a></div>'''
        if username == ADMIN_USER:
            return f'''{CSS_STYLE}<div class=container><h3>该用户名不可注册</h3><br><a href=/register>返回</a></div>'''
        if get_user(username):
            return f'''{CSS_STYLE}<div class=container><h3>用户名已被注册</h3><br><a href=/register>返回</a></div>'''
        add_user(username, password)
        return f'''{CSS_STYLE}<div class=container><h3>注册成功！</h3><br>
        <a href=/register>返回注册页</a> | <a href=/admin/login>去登录后台</a></div>'''
    return render_template_string(CSS_STYLE + '''
    <div class="container login-box">
        <h2>星闪外置登录 · 用户注册</h2>
        <form method=post>
            <input name=username placeholder="用户名(3-16位)" required minlength=3 maxlength=16>
            <input name=password type=password placeholder="密码(至少6位)" required minlength=6>
            <input name=confirm_pwd type=password placeholder="确认密码" required>
            <button>注册</button>
        </form>
        <br>
        <a href="/admin/login">已有账号？去登录</a>
    </div>
    ''')

@app.route('/api/register', methods=['GET', 'POST'])
def api_register():
    try:
        if request.method == 'GET':
            username = request.args.get('username', '').strip()
            password = request.args.get('password', '').strip()
        else:
            data = request.get_json() or {}
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
        if not username or len(username) < 3 or len(username) > 16:
            return jsonify({"code": 400, "msg": "用户名长度必须在3-16位"}), 400
        if not password or len(password) < 6:
            return jsonify({"code": 400, "msg": "密码长度至少6位"}), 400
        if username == ADMIN_USER:
            return jsonify({"code": 403, "msg": "该用户名不可注册"}), 403
        if get_user(username):
            return jsonify({"code": 409, "msg": "用户名已被注册"}), 409
        add_user(username, password)
        return jsonify({"code": 200, "msg": "注册成功", "username": username}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": f"服务器错误：{str(e)}"}), 500

@app.route('/api/notice', methods=['GET'])
def api_notice():
    notices = get_notices()
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": notices
    })

@app.route('/api/refresh_skin_cache', methods=['POST'])
def refresh_skin_cache():
    # 清理缓存，这里简单返回成功
    return jsonify({"code":200,"msg":"没做皮肤缓存刷新"})

# ==================== 管理后台 ====================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USER:
            user = get_user(ADMIN_USER)
            if user and verify_password(password, user['password']):
                session['admin'] = True
                session.permanent = True
                return redirect(url_for('admin_index'))
        return f'''{CSS_STYLE}<div class=container login-box><h3>账号或密码错误</h3><br><a href=/admin/login>返回</a></div>'''
    return render_template_string(CSS_STYLE + '''
    <div class="container login-box">
        <h2>🌟 星闪控制台 · 管理员登录</h2>
        <form method=post>
            <input name=username placeholder="管理员账号">
            <input name=password type=password placeholder="管理员密码">
            <button>登录控制台</button>
        </form>
        <br>
        <a href="/register">没有账号？去注册</a>
    </div>
    ''')

@app.route('/admin')
def admin_index():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    users = db_fetchall('SELECT * FROM users')
    total_users = len(users)
    notices = get_notices()
    total_notices = len(notices)
    clean_expired_sessions()
    total_sessions = db_fetchone('SELECT COUNT(*) as cnt FROM sessions')['cnt']

    now = datetime.now()
    uptime = now - SERVER_START_TIME
    uptime_str = f"{uptime.days}天 {uptime.seconds//3600}小时 {(uptime.seconds%3600)//60}分钟"

    # 最近注册用户（前10）
    recent_users = sorted(users, key=lambda x: x['reg_time'] or '', reverse=True)[:10]

    return render_template_string(CSS_STYLE + '''
    <div class="container">
        <h2>🌟 星闪控制台 · 数据仪表盘</h2>
        <div class="nav">
            <a href="/admin" class="active">数据总览</a>
            <a href="/admin/notice">公告管理</a>
            <a href="/admin/logout">退出登录</a>
        </div>
        <div class="card-group">
            <div class="card"><div class="num">{{ total_users }}</div><div class="label">👥 注册用户</div></div>
            <div class="card"><div class="num">{{ total_notices }}</div><div class="label">📢 公告总数</div></div>
            <div class="card"><div class="num">{{ total_sessions }}</div><div class="label">🟢 在线会话</div></div>
            <div class="card"><div class="num" style="font-size:16px;">{{ uptime_str }}</div><div class="label">⏱️ 运行时长</div></div>
        </div>
        <div class="btn-group">
            <a href="/admin/notice" class="btn btn-primary">📝 发布公告</a>
            <a href="#" id="refreshSkinBtn" class="btn btn-info">🔄 刷新皮肤缓存</a>
        </div>
        <h3>📋 最近注册用户 (前10)</h3>
        <table>
            <tr><th>玩家名</th><th>UUID</th><th>注册时间</th><th>操作</th></tr>
            {% for u in recent_users %}
            <tr>
                <td>{{ u.username }}</td>
                <td>{{ u.uuid }}</td>
                <td>{{ u.reg_time }}</td>
                <td><a href="/admin/delete?name={{ u.username }}" onclick="return confirm('确定删除？')" style="color:#ff8a7a;">删除</a></td>
            </tr>
            {% endfor %}
            {% if recent_users|length == 0 %}
            <tr><td colspan="4">暂无用户</td></tr>
            {% endif %}
        </table>
    </div>
    <script>
        document.getElementById('refreshSkinBtn')?.addEventListener('click', function(e){
            e.preventDefault();
            fetch('/api/refresh_skin_cache', {method: 'POST'})
                .then(res => res.json())
                .then(data => alert(data.msg))
                .catch(err => alert('刷新失败：' + err));
        });
    </script>
    ''', total_users=total_users, total_notices=total_notices, total_sessions=total_sessions,
        uptime_str=uptime_str, recent_users=recent_users)

@app.route('/admin/notice')
def admin_notice():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    notices = get_notices()
    return render_template_string(CSS_STYLE + '''
    <div class="container">
        <h2>🌟 星闪控制台 · 公告管理</h2>
        <div class="nav">
            <a href="/admin">数据总览</a>
            <a href="/admin/notice" class="active">公告管理</a>
            <a href="/admin/logout">退出登录</a>
        </div>
        <h3>📝 发布新公告</h3>
        <form method="post" action="/admin/notice/add">
            <input name="title" placeholder="公告标题" required>
            <textarea name="content" rows="5" placeholder="公告内容" required></textarea>
            <button>发布公告</button>
        </form>
        <h3>📋 公告列表</h3>
        <table>
            <tr><th>序号</th><th>标题</th><th>内容</th><th>发布时间</th><th>操作</th></tr>
            {% for n in notices %}
            <tr>
                <td>{{ loop.index }}</td>
                <td>{{ n.title }}</td>
                <td>{{ n.content }}</td>
                <td>{{ n.time }}</td>
                <td><a href="/admin/notice/del?id={{ loop.index }}" onclick="return confirm('确定删除？')" style="color:#ff8a7a;">删除</a></td>
            </tr>
            {% endfor %}
            {% if notices|length == 0 %}
            <tr><td colspan="5">暂无公告</td></tr>
            {% endif %}
        </table>
    </div>
    ''', notices=notices)

@app.route('/admin/notice/add', methods=['POST'])
def add_notice_route():
    if not session.get('admin'):
        return "no permission"
    title = request.form.get('title')
    content = request.form.get('content')
    add_notice(title, content)
    return redirect(url_for('admin_notice'))

@app.route('/admin/notice/del')
def del_notice_route():
    if not session.get('admin'):
        return "no permission"
    idx = int(request.args.get('id', 0))
    del_notice(idx)
    return redirect(url_for('admin_notice'))

@app.route('/admin/delete')
def admin_delete():
    if not session.get('admin'):
        return "no permission"
    username = request.args.get('name')
    if username and username != ADMIN_USER:
        db_exec('DELETE FROM users WHERE username=?', (username,))
    return redirect(url_for('admin_index'))

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

# ==================== 控制台指令系统 ====================
SERVER_COMMANDS = {}
def register_command(name, desc, func):
    SERVER_COMMANDS[name] = {"func": func, "desc": desc}

def cmd_help(args):
    print("\n[控制台] 可用指令:")
    for cmd, info in SERVER_COMMANDS.items():
        print(f"  {cmd:10} | {info['desc']}")
    print()

def cmd_status(args):
    total_users = db_fetchone('SELECT COUNT(*) as cnt FROM users')['cnt']
    total_sessions = db_fetchone('SELECT COUNT(*) as cnt FROM sessions')['cnt']
    total_plugins = len(plugins)
    print(f"\n[状态] 用户数: {total_users} | 会话数: {total_sessions} | 插件数: {total_plugins}")

def cmd_plugins(args):
    print(f"\n[插件] 已加载: {list(plugins.keys())}")

def cmd_reload(args):
    load_all_plugins(app)
    print("\n✅ 插件重载完成！")

def cmd_stop(args):
    print("\n🛑 正在关闭服务器...")
    os._exit(0)

register_command("help", "查看指令列表", cmd_help)
register_command("status", "查看服务器状态", cmd_status)
register_command("plugins", "查看已加载插件", cmd_plugins)
register_command("reload", "热重载所有插件", cmd_reload)
register_command("stop", "安全关闭服务器", cmd_stop)

def console_listener():
    print("\n✅ 控制台已启动 | 输入 help 查看指令")
    while True:
        try:
            line = input("> ").strip()
            if not line: continue
            parts = line.split()
            cmd = parts[0]
            args = parts[1:]
            if cmd in SERVER_COMMANDS:
                SERVER_COMMANDS[cmd]["func"](args)
            else:
                print(f"未知指令: {cmd}")
        except (KeyboardInterrupt, EOFError):
            print("\n👋 控制台退出")
            break
        except Exception as e:
            print(f"错误: {e}")

# ==================== 插件系统（简化版，移除无效沙箱） ====================
PLUGIN_DIR = "plugins"
plugins = {}
plugin_total = 0
plugin_success = 0
route_queue = []
plugin_lock = Lock()

if not os.path.exists(PLUGIN_DIR):
    os.makedirs(PLUGIN_DIR)

def load_all_plugins(app):
    global plugin_total, plugin_success
    print("\n[\033[1;32m✨星闪✨\033[0m] 开始加载插件...")
    sys.path.append(PLUGIN_DIR)
    plugin_files = [f[:-3] for f in os.listdir(PLUGIN_DIR) if f.endswith(".py") and not f.startswith("_")]
    plugin_total = len(plugin_files)
    plugin_success = 0
    route_queue.clear()
    if not plugin_files:
        print("[\033[1;32m✨星闪✨\033[0m] \033[1;31m无插件可加载\033[0m")
        return
    for fname in plugin_files:
        load_plugin(fname, app)
    print(f"\n\033[0m[\033[1;32m✨星闪✨\033[0m] 加载完成：{plugin_success}/{plugin_total} 个插件")

def load_plugin(fname, app):
    global plugin_success
    try:
        path = os.path.join(PLUGIN_DIR, f"{fname}.py")
        spec = importlib.util.spec_from_file_location(fname, path)
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        if hasattr(plugin, "on_load"):
            plugin.on_load(app, route_queue)
        if hasattr(plugin, "register"):
            plugin.register(register_command)
        with plugin_lock:
            plugins[fname] = plugin
            plugin_success += 1
        print(f"  \033[1;32m✅ {fname} —— 加载成功\033[0m")
    except Exception as e:
        print(f"  ❌ {fname} —— 加载失败：{e}")

def install_plugin(zip_path):
    try:
        if not os.path.exists(zip_path):
            print("❌ 插件包不存在！")
            return
        if not zipfile.is_zipfile(zip_path):
            print("❌ 必须是 zip 插件压缩包！")
            return
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(PLUGIN_DIR)
        print(f"✅ 插件安装成功！已解压到 {PLUGIN_DIR} 文件夹")
        print("💡 重启服务或输入 reload 即可加载插件")
    except Exception as e:
        print(f"❌ 安装失败：{e}")

# ==================== 全局样式 ====================
CSS_STYLE = '''
<style>
* { margin: 0; padding: 0; box-sizing: border-box; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
body { background-image: url('https://picsum.photos/id/1002/1920/1080'); background-size: cover; background-position: center; background-attachment: fixed; position: relative; color: #e0e6f0; min-height: 100vh; padding: 20px; }
body::before { content: ""; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); backdrop-filter: blur(10px); z-index: -1; }
.container { background: rgba(15,22,36,0.85); border: 1px solid rgba(100,130,190,0.3); border-radius: 14px; box-shadow: 0 0 40px rgba(0,140,255,0.15), 0 0 80px rgba(100,120,200,0.1); backdrop-filter: blur(10px); padding: 30px 36px; width: 100%; max-width: 1000px; margin: 0 auto; }
.login-box { max-width: 450px; margin: 100px auto; text-align: center; }
h2 { color: #cfe0ff; margin-bottom: 24px; font-weight: 600; font-size: 24px; }
h3 { color: #cfe0ff; margin: 20px 0; font-weight: 500; font-size: 18px; }
.nav { display: flex; gap: 12px; margin-bottom: 30px; padding-bottom: 16px; border-bottom: 1px solid rgba(100,130,190,0.2); }
.nav a { padding: 8px 16px; border-radius: 8px; text-decoration: none; color: #cfe0ff; background: rgba(20,32,60,0.6); transition: all 0.3s; }
.nav a.active { background: #3b82f6; color: #fff; }
.nav a:hover { background: #2563eb; color: #fff; }
a { color: #7ebcff; text-decoration: none; }
a:hover { color: #b3d7ff; }
input, textarea, button { width: 100%; padding: 12px 14px; border-radius: 8px; border: 1px solid rgba(100,130,190,0.4); background: rgba(10,16,30,0.7); color: #eaf2ff; font-size: 15px; outline: none; margin-bottom: 16px; }
input:focus, textarea:focus { border-color: #6ba6ff; box-shadow: 0 0 0 2px rgba(107,166,255,0.3); }
button { background: linear-gradient(135deg, #3b82f6, #2563eb); border: none; color: white; font-weight: 600; cursor: pointer; margin-top: 8px; transition: all 0.3s; }
button:hover { background: linear-gradient(135deg, #2563eb, #1d4ed8); }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
th, td { padding: 14px; border: 1px solid rgba(100,130,190,0.3); text-align: left; }
th { background: rgba(20,32,60,0.8); color: #cfe0ff; font-weight: 600; }
td { background: rgba(10,18,36,0.6); }
.card-group { display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 30px; }
.card { flex: 1; min-width: 200px; background: rgba(10,20,40,0.6); border-radius: 12px; padding: 20px; text-align: center; border: 1px solid rgba(100,150,255,0.3); }
.card .num { font-size: 32px; font-weight: bold; margin-bottom: 8px; color: #6ba6ff; }
.card .label { font-size: 14px; color: #a0b8d8; }
.btn-group { display: flex; gap: 12px; margin-bottom: 20px; }
.btn { padding: 10px 20px; border-radius: 8px; color: white; text-decoration: none; transition: all 0.3s; }
.btn-primary { background: #3b82f6; }
.btn-dark { background: #2d3748; }
.btn-danger { background: #dc2626; }
.btn-info { background: #4f46e5; }
.btn:hover { opacity: 0.9; }
.text-center { text-align: center; }
</style>
'''

# ==================== 主程序入口 ====================
if __name__ == '__main__':
    # 插件安装模式
    if len(sys.argv) == 3 and sys.argv[1] == "--install":
        install_plugin(sys.argv[2])
        sys.exit(0)

    # 正常启动
    init_db()           # 创建表、管理员账号
    load_all_plugins(app)

    print("\033[1;36m" + "="*50 + "\033[0m")
    print("\033[1;32m✨ 星闪外置登录服务已启动！\033[0m")
    print("\033[1;36m" + "="*50 + "\033[0m")
    print(f"登录地址：\033[1;34mhttp://{LOCAL_IP}:{PORT}\033[0m")
    print(f"用户注册：\033[1;34mhttp://{LOCAL_IP}:{PORT}/register\033[0m")
    print(f"公告API：\033[1;34mhttp://{LOCAL_IP}:{PORT}/api/notice\033[0m")
    print(f"管理后台：\033[1;34mhttp://{LOCAL_IP}:{PORT}/admin\033[0m")
    print(f"后台账号：\033[1;31m{ADMIN_USER}\033[0m （密码已通过环境变量或启动时设置）")
    print(f"插件数量：{plugin_total} 个 | 已加载：{plugin_success} 个")
    print("\033[1;36m" + "="*50 + "\033[0m")

    # 启动控制台和会话清理线程
    threading.Thread(target=console_listener, daemon=True).start()
    threading.Thread(target=periodic_session_cleaner, daemon=True).start()

    app.run(host='0.0.0.0', port=int(PORT), debug=False)