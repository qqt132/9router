# openai-register-standalone

独立版 OpenAI 注册脚本，提取自 `codex-manager`，仅保留核心能力：注册、OAuth PKCE、Sentinel 检查、Cookie 缺少 workspace 时的降级登录修复，以及 tempmail 接码。

## 目录结构

```text
openai-register-standalone/
├── README.md
├── requirements.txt
├── register.py
├── config.py
├── oauth.py
├── http_client.py
└── email_service.py
```

## 安装

```bash
cd ~/.openclaw/workspace/scripts/openai-register-standalone
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使用示例

### 单个注册

```bash
python3 register.py --email-service tempmail --proxy http://127.0.0.1:7890
```

### 批量注册

```bash
python3 register.py --count 5 --output accounts.json
```

## 输出格式

JSON 输出包含：

- `email`
- `password`
- `account_id`
- `workspace_id`
- `access_token`
- `refresh_token`

同时保留 `success`、`id_token`、`session_token`、`source`、`error_message`、`logs`、`metadata` 便于排查。

## 降级登录流程说明

当注册完成后，`oai-client-auth-session` Cookie 有时不再带 `workspaces`。本脚本保留了修复后的降级逻辑：

1. 检测 Cookie 缺少 workspace
2. 生成新的 OAuth URL
3. 创建新的 HTTP session
4. 重新获取 `oai-did`
5. 调用 Sentinel 获取 token
6. 以 `screen_hint="login"` 提交邮箱
7. 调用 `/api/accounts/passwordless/send-otp`
8. 从 tempmail 收 OTP
9. 验证 OTP
10. 重新读取 workspace
11. 继续 `workspace/select -> redirect -> callback -> token exchange`

## OAuth PKCE

脚本保留：

- `code_verifier`
- `code_challenge`
- `state` 生成与校验
- 回调后 `authorization_code` 交换 token

## Sentinel 检查

脚本会：

1. 访问 OAuth 页获取 `oai-did`
2. 调用 Sentinel 接口
3. 将返回 token 放入 `openai-sentinel-token` 请求头

## 故障排查

### unsupported ip location

OpenAI 会限制部分地区出口。请更换代理。

### 收不到验证码

检查代理、tempmail 可用性，以及 OpenAI 邮件投递延迟。

### workspace missing and fallback login failed

说明首次注册后无 workspace，且降级登录失败。常见原因：

- OTP 未送达
- passwordless 接口行为变化
- Sentinel / Device ID 状态失效
- 新 session 被风控

### state mismatch

说明 OAuth 回调与当前流程不匹配，通常是并发流程或 session 污染导致。

## 设计取舍

已移除：

- 数据库
- Web UI
- 任务管理
- 多邮箱服务
- codex-manager 配置系统

只保留最小可运行链路，方便独立维护与验证。
