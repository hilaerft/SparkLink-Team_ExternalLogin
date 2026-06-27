# 星闪外置登录服务

> 一个轻量、开源的 Minecraft Yggdrasil 认证服务器实现，支持外置登录、皮肤托管、公告管理、插件扩展及可视化后台。

---

## ✨ 特性

- ✅ 完整实现 Yggdrasil 认证流程（`authenticate`、`join`、`hasJoined`、`profile`）
- ✅ 自动用户注册（首次登录自动创建账号）
- ✅ 支持皮肤托管（PNG 格式，按 UUID 命名）
- ✅ 内置公告系统（管理后台可发布/删除）
- ✅ 可视化管理仪表盘（用户管理、公告管理、会话统计）
- ✅ 插件系统（支持热加载，可扩展自定义路由和命令）
- ✅ 会话持久化（SQLite 存储，自动清理过期会话）
- ✅ 无额外依赖（仅需 Python 标准库 + Flask）
- ✅ MIT 开源许可

---

## 📦 依赖

- Python 3.6+
- Flask (自动安装以下依赖)
  - `flask`
  - `werkzeug`
  - `sqlite3`（内置）
  - `hashlib`、`secrets`、`uuid`、`threading` 等（内置）

---

## 🚀 快速开始

### 1. 克隆或下载项目

```bash
git clone https://github.com/your-repo/yggdrasil2.git
cd yggdrasil2
```

### 2. 安装 Python 依赖

推荐使用虚拟环境：

```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
# 或 venv\Scripts\activate # Windows
pip install flask
```

### 3. 设置管理员密码

首次启动时，如果未设置环境变量 `ADMIN_PASSWORD`，程序会**交互式询问**管理员密码。  
也可直接设置环境变量（推荐）：

```bash
export ADMIN_PASSWORD="你的强密码"   # Linux/Mac
# 或 set ADMIN_PASSWORD=你的强密码  # Windows (cmd)
```

### 4. 运行服务

```bash
python Yggdrasil2.0.py
```

默认监听 `0.0.0.0:5912`，可通过环境变量 `PORT` 修改端口。

---

## ⚙️ 配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `PORT` | 服务端口 | `5912` |
| `ADMIN_PASSWORD` | 管理员初始密码（首次运行必设） | 无（启动时交互输入） |
| `SECRET_KEY` | Flask 会话加密密钥 | 自动生成随机值 |

---

## 📁 文件结构

```
.
├── Yggdrasil2.0.py        # 主程序
├── skins/                 # 皮肤文件夹（自动创建）
│   ├── default.png        # 全局默认皮肤（可选）
│   └── <uuid>.png         # 按玩家 UUID 命名的皮肤
├── plugins/               # 插件文件夹（自动创建）
│   └── *.py               # 插件文件
├── yggdrasil.db           # SQLite 数据库（自动创建）
└── README.md
```

---

## 🖥️ 使用说明

### 客户端配置

在 Minecraft 启动器中，将认证服务器地址设置为：

```
http://<服务器IP>:<端口>/
```

例如：`http://192.168.1.100:5912/`

### 用户注册

访问 `http://<IP>:<PORT>/register` 进行注册，或通过 API 注册（见下文）。

### 皮肤设置

- 将皮肤图片（PNG）放入 `skins/` 文件夹，命名为 `玩家UUID.png`（无连字符）。
- 也可放置 `default.png` 作为所有玩家的默认皮肤。
- 客户端通过 `http://<IP>:<PORT>/skin/<filename>` 获取皮肤。

---

## 🔌 API 接口

### 认证相关（Yggdrasil 标准）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 服务元信息 |
| `/authserver/authenticate` | POST | 用户认证（自动注册） |
| `/authserver/refresh` | POST | 刷新令牌（简单实现） |
| `/authserver/validate` | POST | 验证令牌（始终返回 204） |
| `/authserver/invalidate` | POST | 注销令牌（返回 204） |
| `/sessionserver/session/minecraft/join` | POST | 服务器加入会话 |
| `/sessionserver/session/minecraft/hasJoined` | GET | 查询玩家会话 |
| `/sessionserver/session/minecraft/profile/<uuid>` | GET | 获取玩家资料（含皮肤 URL） |

### 皮肤服务

| 端点 | 说明 |
|------|------|
| `/skin/<filename>` | 获取皮肤文件（如 `uuid.png`） |
| `/skin/default` | 默认皮肤（若存在 `default_steve.png`） |

### 公开 API（非标准）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/register` | POST/GET | 注册用户（JSON 或表单） |
| `/api/notice` | GET | 获取公告列表 |
| `/api/refresh_skin_cache` | POST | 刷新皮肤缓存（占位） |

---

## 🛠️ 管理后台

访问 `http://<IP>:<PORT>/admin` 进入后台登录。

- **默认管理员账号**：`admin`
- **密码**：启动时设置的环境变量或交互输入。

### 后台功能

- **数据总览**：用户数、公告数、在线会话数、运行时长
- **公告管理**：发布新公告、删除公告
- **用户管理**：查看最近注册用户、删除用户（不可删除管理员）
- **插件管理**（通过控制台，详见下文）

---

## 🧩 插件系统

### 插件放置

将 `.py` 插件文件放入 `plugins/` 目录，服务启动时会自动加载。  
热重载支持：在控制台输入 `reload` 即可重新加载所有插件。

### 插件编写规范

每个插件需实现以下可选函数：

```python
def on_load(app, route_queue):
    """
    插件加载时调用
    :param app: Flask 应用实例
    :param route_queue: 路由列表，可动态添加路由（格式为 (rule, view_func, methods)）
    """
    @app.route('/my-plugin')
    def my_plugin():
        return "Hello from plugin!"

def register(cmd_register):
    """
    注册控制台命令
    :param cmd_register: 注册函数，调用 cmd_register(name, description, func)
    """
    def my_cmd(args):
        print("插件命令执行")
    cmd_register("mycmd", "我的插件命令", my_cmd)
```

### 命令行插件安装

```bash
python Yggdrasil2.0.py --install plugin.zip
```

会将 `plugin.zip` 解压到 `plugins/` 目录，随后 `reload` 即可加载。

---

## ⌨️ 控制台命令

服务启动后，在终端中可输入以下命令：

| 命令 | 说明 |
|------|------|
| `help` | 显示所有可用命令 |
| `status` | 显示用户数、会话数、插件数 |
| `plugins` | 列出已加载的插件 |
| `reload` | 热重载所有插件 |
| `stop` | 安全关闭服务器 |

---

## 📄 许可证

本项目采用 **MIT 许可证**。详见项目根目录下的 `LICENSE` 文件。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。  
若需自定义功能，推荐通过插件系统扩展，避免修改核心代码。

---

## 📧 联系

如有问题，可提交 Issue 或联系作者。

---

**Enjoy your Yggdrasil server!** 🌟
