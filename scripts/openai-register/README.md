# openai-register

OpenAI 免费账号自动注册脚本，支持临时邮箱接码、OAuth PKCE 流程、Sentinel 检查和 Workspace 降级登录修复。

## 目录结构

```text
openai-register/
├── README.md
├── requirements.txt
├── register.py          # 主注册脚本
├── config.py            # 配置和常量
├── oauth.py             # OAuth PKCE 实现
├── http_client.py       # HTTP 客户端和 IP 检查
└── email_service.py     # 临时邮箱服务
```

## 安装依赖

```bash
pip install -r requirements.txt
```

依赖：
- `curl-cffi` - 模拟浏览器指纹的 HTTP 客户端

## 使用方式

### 1. 命令行直接使用

#### 单个注册

```bash
python3 register.py --email-service tempmail
```

#### 批量注册

```bash
python3 register.py --count 5 --output accounts.json
```

#### 使用代理

```bash
python3 register.py --proxy http://127.0.0.1:7890
```

#### 静默模式（仅输出 JSON）

```bash
python3 register.py --quiet
```

### 2. 9router 集成使用

脚本已集成到 9router 项目中，通过 Web UI 一键注册：

1. 访问 `http://localhost:20128/dashboard/providers/codex`
2. 点击"添加免费账号"按钮
3. 实时查看注册进度（中文日志）
4. 注册成功后自动添加到账号列表

**API 端点：** `POST /api/providers/codex/register`

**特性：**
- SSE 流式传输实时日志
- 中文进度提示
- 自动保存到数据库
- 多语言 UI 支持（32 种语言）

## 输出格式

成功时返回 JSON：

```json
{
  "success": true,
  "email": "example@tempmail.com",
  "password": "generated_password",
  "account_id": "user-xxx",
  "workspace_id": "workspace-xxx",
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "id_token": "eyJ...",
  "session_token": "xxx",
  "expires_in": 3600,
  "source": "register",
  "logs": ["...", "..."],
  "metadata": {
    "email_service": "tempmail",
    "proxy_used": null,
    "registered_at": "2026-03-24T10:00:00",
    "is_existing_account": false
  }
}
```

失败时：

```json
{
  "success": false,
  "error_message": "获取验证码失败",
  "logs": ["...", "..."]
}
```

## 注册流程

1. **IP 检查** - 验证出口 IP 是否支持（需要非中国大陆 IP）
2. **创建临时邮箱** - 通过 tempmail 服务获取临时邮箱地址
3. **OAuth 授权** - 生成 PKCE 参数和授权链接
4. **获取设备 ID** - 访问授权页面获取 `oai-did` cookie
5. **Sentinel 检查** - 获取安全令牌
6. **提交注册表单** - 提交邮箱地址
7. **密码注册** - 生成随机密码并注册
8. **发送验证码** - 请求 OTP 验证码
9. **接收验证码** - 从临时邮箱获取验证码
10. **验证 OTP** - 提交验证码
11. **创建账号** - 完成账号创建
12. **OAuth 登录** - 通过 OAuth 流程登录
13. **获取 Workspace** - 从 auth cookie 中提取 workspace ID
14. **选择 Workspace** - 确认 workspace 并获取回调链接
15. **Token 交换** - 用授权码交换 access token

## 降级登录流程

当注册完成后 `oai-client-auth-session` Cookie 缺少 `workspaces` 时，自动触发降级登录：

1. 检测 Cookie 缺少 workspace
2. 生成新的 OAuth URL 和 session
3. 重新获取 device ID 和 Sentinel token
4. 以 `screen_hint="login"` 提交邮箱
5. 调用 `/api/accounts/passwordless/send-otp` 发送验证码
6. 从临时邮箱接收 OTP
7. 验证 OTP 后重新读取 workspace
8. 继续完成 OAuth 流程

## 日志说明

脚本输出中文日志到 stderr，便于实时监控：

- `开始注册流程`
- `IP 地区: SG`
- `临时邮箱已创建: xxx@xxx.com`
- `OAuth 授权链接已生成`
- `设备 ID 已获取: xxx`
- `安全令牌已获取`
- `注册表单状态: 200`
- `密码注册状态: 200`
- `验证码发送状态: 200`
- `验证码已收到: xxxxxx`
- `验证码校验状态: 200`
- `账号创建状态: 200`
- `OAuth 登录跳转完成`
- `Workspace ID 已获取: xxx`
- `Workspace 选择状态: 200`

## 故障排查

### 不支持的 IP 地区

OpenAI 限制部分地区注册。解决方案：
- 使用美国、新加坡等支持地区的代理
- 通过 `--proxy` 参数指定代理

### 获取验证码失败

可能原因：
- 临时邮箱服务不可用
- OpenAI 邮件投递延迟
- 代理网络问题

解决方案：
- 重试注册
- 更换代理
- 检查网络连接

### Workspace 获取失败

常见原因：
- 首次注册后 Cookie 无 workspace
- 降级登录失败
- Sentinel / Device ID 状态失效
- 新 session 被风控

解决方案：
- 脚本会自动尝试降级登录
- 如果仍失败，更换代理重试

### OAuth 回调失败

可能原因：
- State 参数不匹配
- Session 污染
- 并发流程冲突

解决方案：
- 避免并发注册
- 重新运行脚本

## OAuth PKCE 实现

脚本实现完整的 OAuth PKCE 流程：

- 生成 `code_verifier`（随机 43 字符）
- 计算 `code_challenge`（SHA256 + Base64URL）
- 生成 `state`（随机 32 字符）
- 授权码交换时验证 state
- 使用 code_verifier 交换 token

## Sentinel 检查

脚本会自动：

1. 访问 OAuth 授权页获取 `oai-did` cookie
2. 调用 Sentinel 接口获取安全令牌
3. 将令牌放入 `openai-sentinel-token` 请求头
4. 在所有后续请求中携带该令牌

## 技术细节

### HTTP 客户端

使用 `curl-cffi` 模拟真实浏览器指纹，避免被检测为机器人。

### 临时邮箱

使用 tempmail.lol 服务：
- 自动创建临时邮箱
- 轮询接收验证码
- 支持正则匹配提取 OTP

### 密码生成

随机生成 16 位密码，包含：
- 大写字母
- 小写字母
- 数字
- 特殊字符

## 设计原则

- **最小依赖** - 仅依赖 curl-cffi
- **独立运行** - 无需数据库或 Web UI
- **易于集成** - 可作为独立脚本或 API 调用
- **详细日志** - 中文日志便于调试
- **错误处理** - 完整的异常捕获和错误信息

## 许可证

MIT
