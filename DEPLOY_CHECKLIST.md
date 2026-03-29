# 🚀 部署检查清单

## 部署前检查

### 1. 代码文件完整性 ✅

- [x] `src/sse/services/auth.js` (367行) - 主要改进文件
- [x] `src/sse/handlers/chat.js` (226行) - 401/403处理
- [x] `src/sse/services/auth.js.backup` - 原始备份存在

### 2. 代码验证 ✅

- [x] 18/18 检查通过
  - [x] 基础功能 (10项)
  - [x] 401/403处理 (8项)

### 3. 文档完整性 ✅

- [x] 9个文档文件全部存在
- [x] 总计约2500行文档

---

## 部署步骤

### Step 1: 备份当前运行版本（可选）

```bash
cd /Users/irobotx/.openclaw/workspace/9router

# 如果需要额外备份
cp src/sse/services/auth.js src/sse/services/auth.js.pre-deploy
cp src/sse/handlers/chat.js src/sse/handlers/chat.js.pre-deploy
```

### Step 2: 停止服务

```bash
# 如果使用 npm
# Ctrl+C 停止当前进程

# 如果使用 pm2
pm2 stop 9router

# 如果使用其他进程管理器
# 根据实际情况停止
```

### Step 3: 启动服务

```bash
# 使用 npm
npm run dev

# 使用 pm2
pm2 start 9router
pm2 logs 9router --lines 50
```

### Step 4: 观察日志

```bash
# 实时监控日志
tail -f logs/9router.log | grep "AUTH"

# 或使用 pm2
pm2 logs 9router --lines 100
```

---

## 部署后验证

### 1. 基础功能验证 ✅

**检查项**:
- [ ] 服务正常启动，无报错
- [ ] 现有请求正常工作
- [ ] 账号选择逻辑正常

**验证方法**:
```bash
# 发送测试请求
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "antigravity/claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**预期**: 请求成功返回

### 2. 429错误 + 服务器时间验证 ✅

**检查项**:
- [ ] 日志显示 `Querying quota resetAt`
- [ ] 日志显示 `Got quota resetAt from server`
- [ ] 日志显示 `(server resetAt)`
- [ ] 锁定时间是真实的配额恢复时间

**触发方法**:
```bash
# 快速发送多个请求触发429
for i in {1..5}; do
  curl -X POST http://localhost:3000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "antigravity/claude-sonnet-4-6",
      "messages": [{"role": "user", "content": "Test '$i'"}]
    }' &
done
wait
```

**预期日志**:
```
[AUTH] Querying quota resetAt for antigravity/claude-sonnet-4-6
[AUTH] Got quota resetAt from server: 2026-03-20T18:30:00.000Z
[AUTH] user@example.com locked modelLock_claude-sonnet-4-6 until 2026-03-20T18:30:00.000Z (server resetAt) [429]
```

### 3. 缓存机制验证 ✅

**检查项**:
- [ ] 1分钟内重复429使用缓存
- [ ] 日志显示 `Using cached quota resetAt`

**触发方法**:
等待10秒后再次触发429

**预期日志**:
```
[AUTH] Using cached quota resetAt for abc12345
```

### 4. 401/403处理验证 ✅

**场景A: Token被撤销**

**检查项**:
- [ ] 日志显示 `after successful refresh`
- [ ] 日志显示 `[INVALID]`
- [ ] 账号被标记为 `invalid`
- [ ] 后续请求跳过该账号

**预期日志**:
```
[TOKEN] ANTIGRAVITY | refreshed
[AUTH] Auth error after successful refresh - marking as invalid
[AUTH] user@example.com locked modelLock___all until ... [401] [INVALID]
[AUTH]   → abc12345 | invalid
```

**场景B: 刷新API临时失败**

**检查项**:
- [ ] 日志显示 `without successful refresh`
- [ ] 日志不显示 `[INVALID]`
- [ ] 账号短暂冷却后自动恢复

**预期日志**:
```
[TOKEN] ANTIGRAVITY | refresh failed
[AUTH] Auth error without successful refresh - temporary cooldown
[AUTH] user@example.com locked modelLock___all until ... [401]
```

### 5. 多账号Fallback验证 ✅

**检查项**:
- [ ] 账号A失败后自动切换到账号B
- [ ] 所有账号锁定时返回正确的 `retryAfter`
- [ ] 日志显示账号切换过程

**预期日志**:
```
[AUTH] Using antigravity account: accountA
[AUTH] accountA locked ... [429]
[AUTH] Account accountA unavailable (429), trying fallback
[AUTH] Using antigravity account: accountB
```

---

## 成功标志

### 必须看到的日志

1. ✅ `(server resetAt)` - 使用服务器时间
2. ✅ `(local backoff)` - 回退到本地计算
3. ✅ `[INVALID]` - 持久失效标记
4. ✅ `Using cached quota resetAt` - 缓存生效
5. ✅ `after successful refresh` - 智能401/403处理

### 不应该看到的错误

- ❌ `TypeError` 或 `ReferenceError`
- ❌ `getQuotaResetTime is not defined`
- ❌ `credentialsRefreshed is not defined`
- ❌ 服务启动失败
- ❌ 现有功能异常

---

## 性能监控

### 监控指标

1. **配额查询成功率**
   - 目标: >80%
   - 监控: 日志中 `Got quota resetAt from server` 的频率

2. **缓存命中率**
   - 目标: 1分钟内重复429应该使用缓存
   - 监控: 日志中 `Using cached quota resetAt` 的频率

3. **401/403分类准确性**
   - 目标: 100%
   - 监控: `[INVALID]` 标记是否准确

4. **响应时间**
   - 目标: 配额查询不影响响应速度
   - 监控: 请求总耗时

---

## 回滚方案

### 如果发现问题

```bash
cd /Users/irobotx/.openclaw/workspace/9router

# 停止服务
pm2 stop 9router  # 或 Ctrl+C

# 回滚代码
mv src/sse/services/auth.js src/sse/services/auth.js.new
mv src/sse/handlers/chat.js src/sse/handlers/chat.js.new
mv src/sse/services/auth.js.backup src/sse/services/auth.js
git checkout src/sse/handlers/chat.js

# 重启服务
pm2 start 9router  # 或 npm run dev
```

### 恢复新版本

```bash
mv src/sse/services/auth.js.new src/sse/services/auth.js
mv src/sse/handlers/chat.js.new src/sse/handlers/chat.js
pm2 restart 9router
```

---

## 问题排查

### 问题1: 日志一直显示 `(local backoff)`

**可能原因**:
- Provider不支持配额查询
- 配额API返回数据中没有 `resetAt`
- 网络问题导致查询失败

**排查步骤**:
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

### 问题2: 401/403没有被标记为 `invalid`

**检查**:
```bash
# 确认代码中有这段逻辑
grep -A 5 "after successful refresh" src/sse/services/auth.js
```

### 问题3: 缓存没有生效

**检查缓存TTL**:
```bash
grep "QUOTA_CACHE_TTL" src/sse/services/auth.js
```

**预期**: `const QUOTA_CACHE_TTL = 60 * 1000;`

---

## 部署完成确认

### 最终检查清单

- [ ] 服务正常运行
- [ ] 基础功能正常
- [ ] 日志显示 `(server resetAt)`
- [ ] 日志显示 `[INVALID]`（如果有401/403）
- [ ] 缓存机制生效
- [ ] 多账号fallback正常
- [ ] 无异常错误日志
- [ ] 性能指标正常

### 签署确认

- **部署时间**: _______________
- **部署人**: _______________
- **验证人**: _______________
- **状态**: [ ] 成功 [ ] 失败 [ ] 需回滚

---

## 联系支持

如有问题，请查阅：

1. **FINAL_SUMMARY.md** - 最终总结
2. **TEST_GUIDE.md** - 测试指南
3. **AUTH_ERROR_HANDLING.md** - 401/403处理详解
4. **FALLBACK_IMPROVEMENT.md** - 完整技术文档

---

**创建时间**: 2026-03-20 17:52 UTC  
**版本**: v1.0  
**状态**: ✅ 准备就绪
