# 测试指南 - 账号Fallback机制改进

## 快速测试步骤

### 1. 启动9router服务

```bash
cd /Users/irobotx/.openclaw/workspace/9router
npm run dev
```

### 2. 触发429错误测试

**方法A：快速连续请求**

```bash
# 使用同一个账号快速发送10个请求
for i in {1..10}; do
  curl -X POST http://localhost:3000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "antigravity/claude-sonnet-4-6",
      "messages": [{"role": "user", "content": "Hello"}]
    }' &
done
wait
```

**预期日志：**
```
[AUTH] Querying quota resetAt for antigravity/claude-sonnet-4-6
[AUTH] Got quota resetAt from server: 2026-03-20T18:30:00.000Z
[AUTH] user@example.com locked modelLock_claude-sonnet-4-6 until 2026-03-20T18:30:00.000Z (server resetAt) [429]
```

**验证点：**
- ✅ 看到 `(server resetAt)` 标记
- ✅ 锁定时间是未来的真实时间（不是几秒后）
- ✅ 后续请求自动切换到其他账号

### 3. 测试缓存机制

**在1分钟内再次触发429：**

```bash
# 等待10秒后再次请求
sleep 10
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "antigravity/claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Test cache"}]
  }'
```

**预期日志：**
```
[AUTH] Using cached quota resetAt for account123
[AUTH] user@example.com locked modelLock_claude-sonnet-4-6 until 2026-03-20T18:30:00.000Z (server resetAt) [429]
```

**验证点：**
- ✅ 看到 `Using cached quota resetAt`
- ✅ 没有重复查询配额API

### 4. 测试401/403认证失败

**方法：使用无效的token**

修改数据库中某个账号的 `accessToken` 为无效值，然后发送请求。

**预期日志：**
```
[AUTH] user@example.com locked modelLock___all until 2026-03-20T17:52:00.000Z (local backoff) [401]
[AUTH] antigravity | available: 1/2
[AUTH]   → user@example.com | invalid
```

**验证点：**
- ✅ `testStatus` 被标记为 `"invalid"`
- ✅ 该账号在后续请求中被跳过
- ✅ 日志显示 `invalid` 标记

### 5. 测试回退逻辑（配额查询失败）

**方法：模拟网络错误**

临时修改 `getQuotaResetTime` 函数，让它抛出错误：

```javascript
async function getQuotaResetTime(connection, model) {
  throw new Error("Network error"); // 临时测试
  // ... 原有代码
}
```

**预期日志：**
```
[AUTH] Failed to query quota resetAt: Network error
[AUTH] user@example.com locked modelLock_claude-sonnet-4-6 until 2026-03-20T17:44:00.000Z (local backoff) [429]
```

**验证点：**
- ✅ 看到 `(local backoff)` 标记
- ✅ 使用指数退避计算的时间（几秒到几分钟）
- ✅ Fallback流程正常工作

## 日志关键字

### 成功场景
- `Querying quota resetAt` - 开始查询配额
- `Got quota resetAt from server` - 成功获取服务器时间
- `(server resetAt)` - 使用服务器时间
- `Using cached quota resetAt` - 使用缓存

### 回退场景
- `Failed to query quota resetAt` - 查询失败
- `No quota data available` - 无配额数据
- `(local backoff)` - 使用本地计算

### 错误分类
- `[429]` + `(server resetAt)` - 配额限制，使用真实时间
- `[401]` 或 `[403]` + `invalid` - 认证失败，持久标记
- `[5xx]` + `(local backoff)` - 临时错误，指数退避

## 数据库验证

### 查看账号状态

```javascript
// 在9router控制台或数据库工具中
const { getProviderConnections } = require('@/lib/localDb');

// 查看所有antigravity账号
const conns = await getProviderConnections({ provider: 'antigravity' });
conns.forEach(c => {
  console.log({
    id: c.id.slice(0, 8),
    testStatus: c.testStatus,
    backoffLevel: c.backoffLevel,
    modelLocks: Object.keys(c).filter(k => k.startsWith('modelLock_')).map(k => ({
      key: k,
      until: c[k]
    }))
  });
});
```

### 预期输出

```javascript
{
  id: 'abc12345',
  testStatus: 'unavailable',
  backoffLevel: 0,  // ← 使用服务器时间时重置为0
  modelLocks: [
    {
      key: 'modelLock_claude-sonnet-4-6',
      until: '2026-03-20T18:30:00.000Z'  // ← 真实的配额恢复时间
    }
  ]
}
```

## 性能监控

### 监控配额查询耗时

在 `getQuotaResetTime` 函数中添加计时：

```javascript
async function getQuotaResetTime(connection, model) {
  const startTime = Date.now();
  try {
    // ... 原有代码
    const usage = await getUsageForProvider(connection);
    const elapsed = Date.now() - startTime;
    log.debug("AUTH", `Quota query took ${elapsed}ms`);
    // ...
  } catch (error) {
    const elapsed = Date.now() - startTime;
    log.warn("AUTH", `Quota query failed after ${elapsed}ms: ${error.message}`);
    return null;
  }
}
```

**预期耗时：**
- 首次查询：100-500ms（取决于provider API响应速度）
- 缓存命中：<1ms

## 常见问题排查

### Q1: 日志一直显示 `(local backoff)`，没有 `(server resetAt)`

**可能原因：**
1. Provider不支持配额查询（例如某些自定义provider）
2. 配额API返回的数据中没有 `resetAt` 字段
3. 网络问题导致查询失败

**排查步骤：**
```bash
# 检查usage.js是否支持该provider
grep -A 20 "case '${provider}'" open-sse/services/usage.js

# 手动测试配额查询
node -e "
const { getUsageForProvider } = require('./open-sse/services/usage.js');
const conn = { provider: 'antigravity', accessToken: 'your_token' };
getUsageForProvider(conn).then(console.log);
"
```

### Q2: 401/403错误后账号没有被标记为 `invalid`

**检查：**
```bash
# 确认代码中有这段逻辑
grep -A 5 "testStatus = \"invalid\"" src/sse/services/auth.js
```

**预期输出：**
```javascript
if (status === 401 || status === 403) {
  testStatus = "invalid";
}
```

### Q3: 缓存没有生效，每次都查询API

**检查缓存TTL：**
```bash
grep "QUOTA_CACHE_TTL" src/sse/services/auth.js
```

**预期：**
```javascript
const QUOTA_CACHE_TTL = 60 * 1000; // 1分钟
```

如果需要调整缓存时间，修改这个值（单位：毫秒）。

## 回滚方案

如果发现问题需要回滚：

```bash
cd /Users/irobotx/.openclaw/workspace/9router
mv src/sse/services/auth.js src/sse/services/auth.js.new
mv src/sse/services/auth.js.backup src/sse/services/auth.js
npm run dev  # 重启服务
```

恢复新版本：

```bash
mv src/sse/services/auth.js.new src/sse/services/auth.js
npm run dev
```

## 成功标志

✅ **改进生效的标志：**

1. 日志中出现 `(server resetAt)` 标记
2. 锁定时间是真实的配额恢复时间（例如1小时后）
3. 401/403错误的账号被标记为 `invalid`
4. 缓存生效，1分钟内不重复查询
5. 配额查询失败时自动回退到本地计算

如果以上5点都满足，说明改进已成功部署！

---

## 补充测试：401/403智能处理

### 测试场景4: Token被撤销（刷新成功但重试失败）

**触发方式**:
1. 在provider控制台撤销token授权
2. 发送请求

**预期日志**:
```
[TOKEN] ANTIGRAVITY | refreshed
[AUTH] Auth error after successful refresh - marking as invalid
[AUTH] user@example.com locked modelLock___all until 2026-03-20T18:00:00.000Z (local backoff) [401] [INVALID]
[AUTH] antigravity | available: 1/2
[AUTH]   → abc12345 | invalid
```

**验证点**:
- ✅ 日志显示 `after successful refresh`
- ✅ 日志显示 `[INVALID]`
- ✅ 数据库中 `testStatus = "invalid"`
- ✅ 后续请求跳过该账号

### 测试场景5: 刷新API临时故障

**触发方式**:
1. 临时断网或阻止刷新API访问
2. 发送请求触发401

**预期日志**:
```
[TOKEN] ANTIGRAVITY | refresh failed
[AUTH] Auth error without successful refresh - temporary cooldown
[AUTH] user@example.com locked modelLock___all until 2026-03-20T17:52:00.000Z (local backoff) [401]
```

**验证点**:
- ✅ 日志显示 `without successful refresh`
- ✅ 日志**不显示** `[INVALID]`
- ✅ 数据库中 `testStatus = "unavailable"`
- ✅ 冷却时间过后账号自动恢复

### 测试场景6: Token即将过期（刷新成功，重试成功）

**触发方式**:
1. 使用即将过期的token
2. 发送请求

**预期日志**:
```
[TOKEN] ANTIGRAVITY | refreshed
[AUTH] Account user@example.com cleared lock for model=claude-sonnet-4-6
```

**验证点**:
- ✅ 请求成功返回
- ✅ 账号状态恢复为 `active`
- ✅ 新token被保存到数据库

---

**更新时间**: 2026-03-20 17:50 UTC
