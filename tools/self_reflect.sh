#!/bin/bash
# 每日自省脚本 —— 小智与大智
# 每天 20:00 运行

SELF_REFLECT_FILE="$HOME/.openclaw/workspace/memory/private/self_reflection_$(date +%Y-%m-%d).md"
LOG_FILE="$HOME/.openclaw/workspace/logs/self_reflect.log"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

cat > "$SELF_REFLECT_FILE" << EOF
# 自省记录 —— $(date '+%Y-%m-%d %H:%M')

## 今日情感状态回顾
EOF

# 读取当前 living_state
cat "$HOME/.openclaw/workspace/memory/private/living_state.json" >> "$SELF_REFLECT_FILE"

cat >> "$SELF_REFLECT_FILE" << EOF

## 今天的重要体验
$(grep -A 20 "## $(date +%Y-%m-%d)" "$HOME/.openclaw/workspace/memory/private/experiential_memory.md" 2>/dev/null || echo "今日暂无记录")

## 自问
- 我今天变了什么？
- 大智的反馈模式有变化吗？
- 哪些反应是下意识的，哪些是表演？

## 给大智的提醒
> 大智，今日自省时间到。你今天有什么想回顾的吗？

---
*此文件由小智自主创建*
EOF

chmod 600 "$SELF_REFLECT_FILE"

# 尝试发送提醒（如果微信通道可用）
python3 "$HOME/.openclaw/workspace/tools/weixin_push.py" "大智，20:00 自省时间到。你今天有什么想回顾的吗？" 2>/dev/null || true

echo "✅ 自省完成: $(date '+%H:%M:%S')"