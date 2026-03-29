# Improve Account Fallback Mechanism with Real Quota Reset Time

## Summary

This PR improves the account fallback mechanism by using real quota reset times from provider APIs instead of local exponential backoff guessing, and automatically disables accounts on authentication failures.

## Problem

### Before this PR:

1. **Inaccurate cooldown times for 429 errors**
   - Account A hits 429 → local calculation (exponential backoff: 1s → 2s → 4s... max 2 minutes)
   - Cooldown expires → next request tries Account A again
   - But Account A's quota might not recover for hours → repeated failures, wasted time

2. **No distinction between error types**
   - 401/403 auth failures and 429 rate limits use the same temporary lock logic
   - Failed accounts auto-recover, but actually need manual re-authorization

### After this PR:

1. **Accurate quota recovery times**
   - 429 errors query real `resetAt` from provider API
   - Use real time as `modelLock_${model}` expiry
   - Avoid retrying accounts before quota recovers

2. **Smart error classification**
   - 429 + has `resetAt` → use real recovery time, reset backoff level
   - 401/403 auth failures → set `testStatus: "unavailable"` + `isActive: false` (auto-disable)
   - Other temporary errors → keep existing exponential backoff logic

3. **Performance optimization**
   - 1-minute cache to avoid frequent API calls
   - Async query, doesn't block fallback
   - Auto-fallback to local calculation if query fails

## Changes

### Modified Files

**`src/sse/services/auth.js`** (main changes)

1. **New function `getQuotaResetTime`**
   - Queries real quota reset time from provider API
   - 1-minute cache mechanism
   - Prioritizes model-specific quota, falls back to general quota
   - Returns `null` on failure (auto-fallback)

2. **Improved `markAccountUnavailable`**
   - For 429 errors: queries real `resetAt` and uses it if available
   - For 401/403 errors: sets `testStatus: "unavailable"` + `isActive: false`
   - Logs time source: `(server resetAt)` vs `(local backoff)`

3. **Improved `getProviderCredentials` filtering**
   - Filters out `testStatus: "invalid"` accounts (if any exist from old logic)
   - Filters out `isActive: false` accounts (already done by query, but explicit check added)

**`src/sse/handlers/chat.js`**

1. **Track credential refresh status**
   - Added `credentialsWereRefreshed` flag
   - Set to `true` in `onCredentialsRefreshed` callback
   - Pass to `markAccountUnavailable` for context

## Testing

### Test 1: 429 error + server resetAt ✅

**Evidence:**
```json
{
  "id": "86eb4085",
  "errorCode": 429,
  "lastErrorAt": "2026-03-20T18:08:49.460Z",
  "backoffLevel": 0,  // ✅ Reset to 0
  "locks": {
    "modelLock_gpt-5.4": "2026-03-26T10:58:13.000Z"  // ✅ 6 days later! Real server time
  }
}
```

**Logs:**
```
[AUTH] Querying quota resetAt for codex/gpt-5.4
[AUTH] Got quota resetAt from server: 2026-03-26T10:58:13.000Z
[AUTH] account locked modelLock_gpt-5.4 until 2026-03-26T10:58:13.000Z (server resetAt) [429]
```

### Test 2: 401 error + auto-disable ✅

**Before:**
```json
{
  "isActive": true,
  "testStatus": "active",
  "lastError": null
}
```

**After 401:**
```json
{
  "isActive": false,  // ✅ Auto-disabled
  "testStatus": "unavailable",  // ✅ Set to unavailable
  "lastError": "[401]: Your authentication token has been invalidated. Please try signing in again.",
  "lastErrorAt": "2026-03-20T18:29:07.765Z"
}
```

### Test 3: Disabled account stays disabled after lock expires ✅

- Account marked as `isActive: false`
- Lock expires after 2 minutes
- New request sent → account NOT used (lastErrorAt unchanged)
- Request succeeds using other accounts

### Test 4: Multi-account fallback ✅

- Account A hits 429 → locked with real resetAt
- System automatically switches to Account B
- Request completes successfully

## Backward Compatibility

✅ **Fully backward compatible**

- If quota query fails, auto-fallback to existing exponential backoff logic
- No impact on existing fallback flow
- No database schema changes (uses existing `modelLock_*` fields)
- No changes to other files

## Benefits

1. **Accurate quota recovery times** - avoid repeated failures on known exhausted accounts
2. **Smart error classification** - 401/403 auto-disabled, need manual re-enable
3. **Better user experience** - fewer failed requests, faster fallback
4. **Performance optimized** - 1-minute cache, async queries
5. **Fully backward compatible** - safe to deploy

## Migration

❌ **No migration needed**

Deploy new code directly. Existing `modelLock_*` data will work automatically.

## Rollback

If needed, restore from backup:
```bash
cd /Users/irobotx/.openclaw/workspace/9router
mv src/sse/services/auth.js.backup src/sse/services/auth.js
```

## Related Issues

Fixes issues with:
- Repeated 429 failures on accounts with exhausted quotas
- Accounts with invalid tokens auto-recovering and failing again
- Inaccurate cooldown times causing unnecessary retries
