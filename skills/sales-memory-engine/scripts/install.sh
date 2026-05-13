#!/bin/bash
# 销售记忆引擎 - 安装脚本
set -e

echo "=== 销售记忆引擎 (Sales Memory Engine) 安装 ==="

# 1. 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 需要 Python 3，请先安装"
    exit 1
fi

PYVER=$(python3 --version 2>&1 | awk '{print $2}')
echo "✅ Python 版本: $PYVER"

# 2. 创建虚拟环境
VENV_PATH="/tmp/sales-memory-venv"
if [ ! -d "$VENV_PATH" ]; then
    echo "🔄 创建虚拟环境..."
    python3 -m venv "$VENV_PATH"
fi

source "$VENV_PATH/bin/activate"

# 3. 安装依赖
echo "🔄 安装依赖..."
pip install -q sentence-transformers numpy 2>&1 | tail -3

echo "✅ 依赖安装完成"

# 4. 初始化数据库
echo "🔄 初始化数据库..."
cd ~/.openclaw/skills/sales-memory-engine/src
python3 database.py

# 5. 运行测试
echo "🔄 运行基础测试..."
python3 extractor.py > /tmp/extractor_test.log 2>&1
if [ $? -eq 0 ]; then
    echo "✅ 实体提取模块测试通过"
else
    echo "⚠️ 实体提取测试有输出，检查 /tmp/extractor_test.log"
fi

echo ""
echo "=== 安装完成 ==="
echo "数据库路径: ~/.openclaw/workspace/memory_engine/db/sales_memory.db"
echo ""
echo "使用方法:"
echo "  1. 导入模块: from memory_engine import save_observation, search, get_customer_profile"
echo "  2. 保存记录: save_observation('盱眙张主任说...', session_id='xxx')"
echo "  3. 检索记忆: search('盱眙')"
echo "  4. 客户画像: get_customer_profile('xuyi_tcm')"
