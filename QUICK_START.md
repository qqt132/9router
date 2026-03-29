# 🚀 快速开始 - 账号Fallback改进

## 立即部署

### 1️⃣ 验证改进已就绪

```bash
cd /Users/irobotx/.openclaw/workspace/9router

# 确认所有文件存在
ls -lh src/sse/services/auth.js
ls -lh src/sse/services/auth.js.backup
ls -lh FALLBACK_IMPROVEMENT.md TEST_GUIDE.md IMPLEMENTATION_SUMMARY.md
```

### 2️⃣ 重启服务

```bash
# 如果使用 npm
npm run dev

# 如果使用 pm2
pm2 restart 9router

# 如果使用其他进程管理器
# 停止并重启你的服务
```

### 3️⃣ 观察日志

```bash
# 实时监控日志
tail -f logs/9router.log

# 或者使用 pm2
pm2 logs 9router --lines 50
```

### 4️⃣ 寻找成功标志

在日志中查找以下内容：

✅ **成功使用服务器时间：**
```
[AUTH] Querying quota resetAt for antigravity/claude-sonnet-4-6
[AUTH] Got quota resetAt from server: 2026-03-20T18:30:00.000Z
[AUTH] user@example.com locked modelLock_claude-sonnet-4-6 until 2026-03-20T18:30:00.000Z (server resetAt) [429]
```

✅ **缓存生效：**
```
[AUTH] Using cached quota resetAt for abc12345
```

✅ **认证失败正确标记：**
```
[AUTH] user@example.com locked modelLock___all until ... (local backoff) [401]
[AUTH]   → abc12345 | invalid
```

## 快速测试

### 触发429错误

```bash
# 快速发送多个请求
for i in {1..5}; do
  curl -X POST http://localhost:3000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "antigravity/claude-sonnet-4-6",
      "messages": [{"role": "user", "content": "Test"}]
    }' &
done
wait
```

**预期结果：**
- 第一个请求成功
- 后续请求触发429
- 日志显示 `(server resetAt)` 或 `(local backoff)`
- 自动切换到其他账号

## 如果遇到问题

### 回滚到原版本

```bash
cd /Users/irobotx/.openclaw/workspace/9router
mv src/sse/services/auth.js src/sse/services/auth.js.new
mv src/sse/services/auth.js.backup src/sse/services/auth.js
npm run dev  # 或 pm2 restart 9router
```

### 恢复新版本

```bash
mv src/sse/services/auth.js.new src/sse/services/auth.js
npm run dev  # 或 pm2 restart 9router
```

## 详细文档

- 📖 **完整技术文档**: `FALLBACK_IMPROVEMENT.md`
- 🧪 **测试指南**: `TEST_GUIDE.md`
- 📊 **实现总结**: `IMPLEMENTATION_SUMMARY.md`
- ✅ **完成清单**: `CHECKLIST.md`

## 核心改进

1. ✅ **真实配额恢复时间** - 不再猜测，使用服务器返回的真实时间
2. ✅ **智能缓存** - 1分钟内不重复查询配额API
3. ✅ **错误分类** - 401/403持久标记，429临时锁定
4. ✅ **自动回退** - 查询失败时自动使用本地计算
5. ✅ **向后兼容** - 不影响现有功能

## 预期效果

### 改进前
```
账号A触发429 → 等1秒 → 再试 → 失败 → 等2秒 → 再试 → 失败...
（浪费多次重试，累计几分钟）
```

### 改进后
```
账号A触发429 → 查询真实恢复时间(1小时后) → 立即切换账号B
（直接跳过无效重试，节省时间）
```

## 监控指标

部署后关注：

1. **日志中 `(server resetAt)` 的出现频率**
2. **配额查询成功率**（应该 >80%）
3. **缓存命中率**（1分钟内重复429应该使用缓存）
4. **401/403账号是否被正确标记为 `invalid`**

---

**状态**: ✅ 已完成并验证  
**部署时间**: 2026-03-20 17:44 UTC  
**准备就绪**: 可以立即部署
