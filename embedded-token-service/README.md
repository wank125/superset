# Superset Embedded SDK 完整测试环境

这是一个完整的 Superset 嵌入式 Dashboard 测试环境，包含后端 Token 服务和前端测试页面。

## 📁 项目结构

```
embedded-token-service/
├── server.js              # Node.js 后端服务 (Guest Token 生成)
├── package.json           # Node.js 依赖配置
├── test_embedded.html     # 前端测试页面
└── README.md             # 本文档
```

## 🚀 快速开始

### 1. 安装依赖

```bash
cd embedded-token-service
npm install
```

### 2. 启动 Token 服务

```bash
# 使用 start.sh（推荐，自动设置 API key）
bash start.sh

# 或手动启动（需要设置 TOKEN_SERVICE_API_KEY）
TOKEN_SERVICE_API_KEY=your-secret-key npm start
```

服务将在 http://localhost:5000 启动

> **注意**: `TOKEN_SERVICE_API_KEY` 环境变量是必需的。如果未设置，`/api/guest-token` 端点将返回 503 错误。

### 3. 打开测试页面

在浏览器中打开: http://localhost:5000/test_embedded.html

## 📋 前置条件

### Superset 配置

确保 Superset 已正确配置嵌入式功能:

1. **编辑配置文件** (`docker/pythonpath_dev/superset_config.py`):
   ```python
   FEATURE_FLAGS = {
       "EMBEDDED_SUPERSET": True
   }
   
   GUEST_TOKEN_JWT_SECRET = "superset-embedded-secret-key-change-in-production"
   GUEST_TOKEN_JWT_AUDIENCE = "superset"
   
   ENABLE_CORS = True
   CORS_OPTIONS = {
       "supports_credentials": True,
       "allow_headers": ["*"],
       "resources": ["*"],
       "origins": ["http://localhost:5000"]
   }
   ```

2. **重启 Superset 服务**:
   ```bash
   docker restart superset_app superset_worker
   ```

3. **验证服务运行**:
   ```bash
   curl http://localhost:8088/health
   ```

### 获取 Dashboard UUID

1. 访问 http://localhost:8088
2. 登录 (默认: admin/admin)
3. 打开任意 Dashboard
4. 点击右上角 **Settings** → **Embed Dashboard**
5. 启用嵌入功能
6. 复制生成的 **UUID**

## 🎯 使用测试页面

### 功能说明

测试页面提供以下功能:

#### 1. 系统健康检查
- 检查 Superset 服务状态
- 检查 Token 服务状态
- 一键全部检查

#### 2. Guest Token 生成
- 输入 Dashboard UUID
- 生成 Guest Token
- 解码查看 Token 内容

#### 3. 嵌入式 Dashboard
- 加载 Dashboard
- 卸载 Dashboard
- 获取 Dashboard 尺寸
- 获取活动标签页

#### 4. 操作日志
- 实时显示所有操作
- 彩色分类 (成功/错误/信息)
- 可清空日志

### 测试步骤

1. **输入 Dashboard UUID**
   - 在配置部分输入您从 Superset 获取的 UUID

2. **健康检查**
   - 点击 "全部检查" 确保服务正常

3. **生成 Token**
   - 点击 "生成 Token" 测试 Token 生成
   - 可选: 点击 "解码 Token" 查看内容

4. **加载 Dashboard**
   - 点击 "🚀 加载 Dashboard"
   - 等待 Dashboard 在页面中显示

5. **交互测试**
   - 使用 Dashboard 的过滤器
   - 点击图表进行交互
   - 查看操作日志中的事件

## 🔧 后端服务 API

### POST /api/guest-token

生成 Guest Token

**请求:**
```json
{
  "dashboardId": "your-dashboard-uuid",
  "userId": "optional-user-id"
}
```

**响应:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expiresIn": 86400,
  "message": "Token generated successfully"
}
```

### GET /health

检查服务健康状态

**响应:**
```json
{
  "status": "ok",
  "service": "Superset Guest Token Service",
  "timestamp": "2026-02-07T08:00:00.000Z"
}
```

### GET /api/superset-health

检查 Superset 连接状态

## 🛠️ 配置说明

### 修改 Superset 域名

编辑 `server.js`:
```javascript
const SUPERSET_DOMAIN = 'http://your-superset-domain:8088';
```

编辑 `test_embedded.html`:
```javascript
const SUPERSET_DOMAIN = 'http://your-superset-domain:8088';
```

### 修改 JWT Secret

**重要:** 确保与 Superset 配置中的 `GUEST_TOKEN_JWT_SECRET` 一致

编辑 `server.js`:
```javascript
const JWT_SECRET = 'your-secret-key';
```

### 修改服务端口

编辑 `server.js`:
```javascript
const PORT = 5000; // 改为您想要的端口
```

## 🐛 常见问题

### 1. Token 服务无法启动

**错误:** `Error: Cannot find module 'express'`

**解决:** 
```bash
npm install
```

### 2. CORS 错误

**错误:** `Access to fetch at 'http://localhost:8088/...' has been blocked by CORS policy`

**解决:** 
1. 检查 Superset 配置中的 `CORS_OPTIONS`
2. 确保包含 `http://localhost:5000`
3. 重启 Superset 服务

### 3. Dashboard 无法加载

**错误:** `Failed to load dashboard`

**可能原因:**
- Dashboard UUID 不正确
- Dashboard 未启用嵌入功能
- Superset 服务未运行
- Token 生成失败

**解决:**
1. 检查 UUID 是否正确
2. 在 Superset UI 中启用 Dashboard 嵌入
3. 检查 Superset 服务状态
4. 查看浏览器控制台和操作日志

### 4. Token 验证失败

**错误:** `Invalid token signature`

**解决:**
确保 `server.js` 中的 `JWT_SECRET` 与 Superset 配置中的 `GUEST_TOKEN_JWT_SECRET` 完全一致

## 📚 相关文档

- [Superset Embedded SDK 官方文档](https://superset.apache.org/docs/configuration/embedded/)
- [NPM Package](https://www.npmjs.com/package/@superset-ui/embedded-sdk)
- [JWT.io](https://jwt.io/) - JWT Token 调试工具

## 🔐 安全注意事项

1. **生产环境必须修改 JWT Secret**
2. **使用 HTTPS** 在生产环境
3. **限制 CORS 允许的域名**
4. **实现用户认证** 在 Token 生成前
5. **添加速率限制** 防止滥用
6. **使用环境变量** 存储敏感配置

## 📝 开发建议

### 添加用户认证

在 `server.js` 中添加认证中间件:

```javascript
function authenticate(req, res, next) {
    // 验证用户身份
    const userId = req.headers['x-user-id'];
    if (!userId) {
        return res.status(401).json({ error: 'Unauthorized' });
    }
    req.userId = userId;
    next();
}

app.post('/api/guest-token', authenticate, (req, res) => {
    // 使用 req.userId 生成 token
});
```

### 添加行级安全 (RLS)

```javascript
function generateGuestToken(dashboardId, userId, userRole) {
    let rls_rules = [];
    
    if (userRole === 'regional_manager') {
        rls_rules = [{ clause: `region = '${userRegion}'` }];
    }
    
    const payload = {
        // ...
        rls_rules: rls_rules
    };
    
    return jwt.sign(payload, JWT_SECRET);
}
```

## 🎉 下一步

1. 尝试不同的 Dashboard
2. 测试过滤器交互
3. 自定义 UI 配置
4. 实现用户权限控制
5. 部署到生产环境

## 💡 提示

- 使用浏览器开发者工具查看网络请求
- 查看操作日志了解详细信息
- Token 默认有效期 24 小时
- SDK 会自动刷新 Token

祝您使用愉快! 🚀
