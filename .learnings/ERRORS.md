# 错误记录

本文件用于记录命令失败、工具错误和异常。

格式基于 self-improving-agent 技能规范。

---

## [ERR-20260513-001] openrouter_chat_500

**Logged**: 2026-05-13T10:20:00+08:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
OpenRouter 列表API正常但 chat completions 全模型500

### Error
```
POST /chat/completions → 500 Internal Server Error
模型测试: gpt-4o-mini, claude-sonnet, 免费模型 全军覆没
```

### Context
- 向用户汇报"key有效"后立刻翻车
- status page 显示服务端问题，非key问题

### Suggested Fix
验证API必须做端到端调用测试，不只测列表端点

### Resolution
- **Resolved**: 2026-05-13T10:25:00+08:00
- **Notes**: 等待OpenRouter服务端恢复，key本身无问题

---

## [ERR-20260512-001] weixin_push_delivered_false

**Logged**: 2026-05-12T08:35:00+08:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
cron微信推送状态显示"delivered=false"，消息未实际送达

### Error
```
policy-morning-brief: status=ok 但 delivered=false
morning-task-brief: 状态error"Message failed" 但系统标记delivered=true
```

### Context
- 05/05早报正常，说明是偶发性故障而非配置问题
- 根源：iLink微信通道token/account不稳定

### Suggested Fix
- 新增送达确认机制（发送后检查result.ok）
- 失败自动重试一次（缩短至600字符内）
- 发送送达确认消息：✅已送达 / ⚠️发送异常
- 备用：多个微信账户轮询

### Resolution
- **Resolved**: 2026-05-12T12:18:00+08:00
- **Notes**: 补发7条简报全部成功；配置新账户 ad811510fd3a-im-bot

---