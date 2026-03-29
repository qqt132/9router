# 401/403 认证错误处理逻辑

## 问题背景

项目已有自动token刷新机制（`handleChatCore` 遇到401/403会调用 `refreshCredentials` 并重试）。

但401/403有两种情况：
1. **Token真的被撤销了**（用户手动撤销授权）→ 应该持久标记为 `invalid`
2. **刷新API临时失败**（网络问题、服务器临时故障）→ 应该短暂冷却后重试

## 解决方案

### 核心逻辑

通过跟踪 `onCredentialsRefreshed` 回调是否被调用，判断token刷新是否成功：

```
401/403错误
    ↓
handleChatCore尝试刷新token
    ↓
    ├─ 刷新成功 → onCredentialsRefreshed被调用 → credentialsWereRefreshed = true
    │       ↓
    │   重试请求
    │       ↓
    │       ├─ 成功 → 返回结果
    │       └─ 还是401/403 → token真的失效了 → 标记为 invalid
    │
    └─ 刷新失败 → onCredentialsRefreshed未被调用 → credentialsWereRefreshed = false
            ↓
        返回401/403 → 可能是临时网络问题 → 短暂冷却
```

### 实现细节

#### 1. chat.js - 跟踪刷新状态

```javascript
// Track if credentials were refreshed during this request
let credentialsWereRefreshed = false;

const result = await handleChatCore({
  // ... 其他参数
  onCredentialsRefreshed: async (newCreds) => {
    credentialsWereRefreshed = true; // 标记刷新成功
    await updateProviderCredentials(credentials.connectionId, {
      accessToken: newCreds.accessToken,
      refreshToken: newCreds.refreshToken,
      providerSpecificData: newCreds.providerSpecificData,
      testStatus: "active"
    });
  },
  // ...
});

// 传递刷新状态给 markAccountUnavailable
const { shouldFallback } = await markAccountUnavailable(
  credentials.connectionId, 
  result.status, 
  result.error, 
  provider, 
  model,
  { credentialsRefreshed: credentialsWereRefreshed }
);
```

#### 2. auth.js - 根据刷新状态决定标记

```javascript
export async function markAccountUnavailable(
  connectionId, 
  status, 
  errorText, 
  provider = null, 
  model = null,
  options = {}
) {
  const { credentialsRefreshed = false } = options;
  
  // ... 现有逻辑 ...
  
  let testStatus = "unavailable";
  const isAuthError = (status === 401 || status === 403);
  
  if (isAuthError && credentialsRefreshed) {
    // 401/403 after successful token refresh → token is truly invalid
    testStatus = "invalid";
    log.warn("AUTH", `Auth error after successful refresh - marking as invalid`);
  } else if (isAuthError && !credentialsRefreshed) {
    // 401/403 without refresh attempt or refresh failed
    // → short cooldown, might be temporary network issue
    testStatus = "unavailable";
    log.warn("AUTH", `Auth error without successful refresh - temporary cooldown`);
  }
  
  // ...
}
```

## 错误分类表

| 场景 | credentialsRefreshed | testStatus | 行为 |
|-----|---------------------|-----------|------|
| 429配额限制 | N/A | `unavailable` | 使用服务器resetAt或本地backoff |
| 401/403 + 刷新成功 + 重试失败 | `true` | `invalid` | 持久标记，需手动重新授权 |
| 401/403 + 刷新失败 | `false` | `unavailable` | 短暂冷却，自动恢复 |
| 5xx服务器错误 | N/A | `unavailable` | 短暂冷却 |

## 日志示例

### 场景1: Token真的失效（刷新成功但重试还是401）

```
[AUTH] Using antigravity account: user@example.com
[TOKEN] ANTIGRAVITY | refreshed
[AUTH] Auth error after successful refresh - marking as invalid
[AUTH] user@example.com locked modelLock___all until 2026-03-20T18:00:00.000Z (local backoff) [401] [INVALID]
[AUTH] antigravity | available: 1/2
[AUTH]   → abc12345 | invalid
```

**结果**: 账号被标记为 `invalid`，后续请求会跳过该账号

### 场景2: 刷新API临时失败

```
[AUTH] Using antigravity account: user@example.com
[TOKEN] ANTIGRAVITY | refresh failed
[AUTH] Auth error without successful refresh - temporary cooldown
[AUTH] user@example.com locked modelLock___all until 2026-03-20T17:52:00.000Z (local backoff) [401]
```

**结果**: 账号短暂冷却（例如2分钟），之后自动恢复

### 场景3: 刷新成功，重试也成功

```
[AUTH] Using antigravity account: user@example.com
[TOKEN] ANTIGRAVITY | refreshed
[AUTH] Account user@example.com cleared lock for model=claude-sonnet-4-6
```

**结果**: 请求成功，账号状态恢复为 `active`

## 测试验证

### 测试1: 模拟token被撤销

**步骤**:
1. 在provider控制台撤销token授权
2. 发送请求触发401错误
3. 观察日志

**预期**:
```
[TOKEN] ANTIGRAVITY | refreshed
[AUTH] Auth error after successful refresh - marking as invalid
[AUTH] ... [401] [INVALID]
```

**验证点**:
- ✅ 日志显示 `after successful refresh`
- ✅ 日志显示 `[INVALID]`
- ✅ 数据库中 `testStatus = "invalid"`
- ✅ 后续请求跳过该账号

### 测试2: 模拟刷新API临时故障

**步骤**:
1. 临时断网或阻止刷新API访问
2. 发送请求触发401错误
3. 观察日志

**预期**:
```
[TOKEN] ANTIGRAVITY | refresh failed
[AUTH] Auth error without successful refresh - temporary cooldown
[AUTH] ... [401]
```

**验证点**:
- ✅ 日志显示 `without successful refresh`
- ✅ 日志**不显示** `[INVALID]`
- ✅ 数据库中 `testStatus = "unavailable"`
- ✅ 冷却时间过后账号自动恢复

### 测试3: 刷新成功，重试也成功

**步骤**:
1. 使用即将过期的token
2. 发送请求触发401
3. 刷新成功，重试成功

**预期**:
```
[TOKEN] ANTIGRAVITY | refreshed
[AUTH] Account ... cleared lock for model=...
```

**验证点**:
- ✅ 请求成功返回
- ✅ 账号状态恢复为 `active`
- ✅ 新token被保存到数据库

## 优势

1. **精确区分**: 真正的认证失败 vs 临时网络问题
2. **减少误判**: 不会因为临时网络问题就永久标记账号
3. **自动恢复**: 临时问题会自动恢复，无需手动干预
4. **清晰日志**: 日志明确标注原因和处理方式

## 向后兼容

✅ **完全向后兼容**

- 如果 `options.credentialsRefreshed` 未传递，默认为 `false`
- 行为回退到保守策略（短暂冷却）
- 不影响其他错误类型的处理

## 相关文件

- `src/sse/handlers/chat.js` - 跟踪刷新状态
- `src/sse/services/auth.js` - 根据刷新状态决定标记
- `open-sse/handlers/chatCore.js` - 执行token刷新（无需修改）

---

**更新时间**: 2026-03-20 17:50 UTC  
**状态**: ✅ 已实现并验证
