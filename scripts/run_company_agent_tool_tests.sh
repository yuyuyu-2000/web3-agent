#!/usr/bin/env bash
# Run user-level tool tests against a running ChainCloud-AI backend.
#
# Usage:
#   bash scripts/run_company_agent_tool_tests.sh
#
# Optional environment variables:
#   BACKEND_URL=http://127.0.0.1:8001
#   CHAT_API_TOKEN=<token-if-CHAT_API_TOKEN-is-enabled>
#   TEST_OUT_DIR=.tmp/company_agent_tool_tests
#
# Notes:
# - This script does not contain or print database passwords.
# - It sends natural-language /chat requests and relies on debug traces to verify tool calls.
# - Optional tools such as ethereum_jsonrpc / tron_node_request / contract_decode_tx_input
#   only pass when the corresponding .env variables are configured.

set -u

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8001}"
TEST_OUT_DIR="${TEST_OUT_DIR:-.tmp/company_agent_tool_tests}"
mkdir -p "$TEST_OUT_DIR"

echo "Backend: $BACKEND_URL"
echo "Output:  $TEST_OUT_DIR"
if [ -n "${CHAT_API_TOKEN:-}" ]; then
  echo "Auth:    using CHAT_API_TOKEN"
else
  echo "Auth:    no CHAT_API_TOKEN"
fi
echo

run_get() {
  local name="$1"
  local path="$2"
  local outfile="${TEST_OUT_DIR}/${name}.json"

  echo "==> GET ${path}"
  curl -sS "${BACKEND_URL}${path}" > "$outfile"
  echo "Saved: $outfile"
  echo
}

run_chat() {
  local name="$1"
  local thread_id="$2"
  local message="$3"
  local outfile="${TEST_OUT_DIR}/${name}.json"
  local payload

  echo "==> CHAT ${name}"

  payload="$(python - <<PY
import json
print(json.dumps({
    "thread_id": "${thread_id}",
    "message": """${message}""",
    "debug": True,
    "trace_max_chars": 2000,
}, ensure_ascii=False))
PY
)"

  if [ -n "${CHAT_API_TOKEN:-}" ]; then
    curl -sS -X POST "${BACKEND_URL}/chat" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${CHAT_API_TOKEN}" \
      -d "$payload" > "$outfile"
  else
    curl -sS -X POST "${BACKEND_URL}/chat" \
      -H "Content-Type: application/json" \
      -d "$payload" > "$outfile"
  fi

  echo "Saved: $outfile"
  echo
}

echo "Step 1: inspect registered tools"
run_get "00_tools" "/tools"

echo "Step 2: ClickHouse user-level tests"
run_chat "01_clickhouse_show_tables" "company-clickhouse-show-tables" \
"请务必调用 clickhouse_select 工具执行 SQL：SHOW TABLES FROM trx。不要凭经验回答，必须基于工具返回结果列出表名，并说明哪些表可能和 TRON 链上交易分析有关。"

run_chat "02_clickhouse_dictionary_tokens" "company-clickhouse-dictionary-tokens" \
"请务必调用 clickhouse_select 工具，先执行 DESCRIBE TABLE trx.dictionary_tokens，再执行 SELECT Address, Symbol, Name, Decimals FROM trx.dictionary_tokens LIMIT 5，并基于工具返回结果解释字段含义。"

run_chat "03_clickhouse_distributed_histories_schema" "company-clickhouse-histories-schema" \
"请务必调用 clickhouse_select 工具执行 SQL：DESCRIBE TABLE trx.distributed_histories。请基于工具返回结果解释这张表可能记录了哪些 TRON 链上交易信息。"

run_chat "04_clickhouse_distributed_histories_sample" "company-clickhouse-histories-sample" \
"请务必调用 clickhouse_select 工具执行 SQL：SELECT Address, Counterpart, Value, TokenId, CreatedAt, BlockNumber, TxHash FROM trx.distributed_histories LIMIT 5。请基于工具返回结果解释这些字段在链上交易分析中的意义。"

run_chat "05_clickhouse_usdt_schema" "company-clickhouse-usdt-schema" \
"请务必调用 clickhouse_select 工具执行 SQL：DESCRIBE TABLE trx.distributed_histories_usdt。请根据字段结构判断这张表适合回答哪些 USDT 转账分析问题。"

echo "Step 3: chart and dashboard tool tests"
run_chat "06_chart_time_series" "company-chart-time-series" \
"请务必调用 generate_time_series 工具生成一个简单的 TRON 链上交易量时间序列图。数据使用样例：2026-05-01 为 120，2026-05-02 为 180，2026-05-03 为 150，2026-05-04 为 230。请返回图表生成结果。"

run_chat "07_chart_bar" "company-chart-bar" \
"请务必调用 generate_bar_chart 工具生成一个柱状图，展示三类交易数量：TRX 转账 320，USDT 转账 580，TRC20 其他转账 210。请返回图表生成结果。"

run_chat "08_chart_pie" "company-chart-pie" \
"请务必调用 generate_pie_chart 工具生成一个饼图，展示三类交易占比：TRX 转账 320，USDT 转账 580，TRC20 其他转账 210。请返回图表生成结果。"

run_chat "09_chart_multi_line" "company-chart-multi-line" \
"请务必调用 generate_multi_line_chart 工具生成一个多线折线图。日期为 2026-05-01 到 2026-05-04，USDT 转账数分别为 80、120、100、150，TRX 转账数分别为 120、180、150、230。请返回图表生成结果。"

run_chat "10_chart_dual_axis" "company-chart-dual-axis" \
"请务必调用 generate_dual_axis_chart 工具生成一个双轴图。日期为 2026-05-01 到 2026-05-04，交易数量分别为 1200、1500、1300、1700，活跃地址数分别为 260、310、280、360。请返回图表生成结果。"

run_chat "11_chart_price_distribution" "company-chart-price-distribution" \
"请务必调用 generate_price_distribution_chart 工具生成一个数值区间分布图。区间为 0-10、10-100、100-1000、1000+，交易价值分别为 5000、18000、42000、90000，交易数量分别为 300、180、60、12。请返回图表生成结果。"

run_chat "12_dashboard" "company-dashboard" \
"请务必调用 create_dashboard 工具生成一个 TRON 链上数据测试 dashboard，标题为 ChainCloud 工具测试 Dashboard，包含三个指标：今日交易数 1200，USDT 转账数 530，活跃地址数 260，并给出简短 Markdown 说明。"

echo "Step 4: scheduler tool test"
run_chat "13_scheduler" "company-scheduler" \
"请务必调用 add_scheduled_task 工具创建一个一次性的测试任务，任务内容是：生成一条 ChainCloud Agent 工具测试提醒，时间设置为 1 分钟后。如果工具需要 ISO 时间格式，请自行给出合理时间。"

echo "Step 5: optional RPC / contract tools. These pass only when corresponding .env is configured."
run_chat "14_ethereum_jsonrpc" "company-eth-rpc" \
"如果 ethereum_jsonrpc 工具已注册，请调用它执行 eth_blockNumber 查询当前以太坊最新区块号，并解释返回结果。如果该工具未注册或未配置，请明确说明未启用。"

run_chat "15_tron_node_request" "company-tron-rpc" \
"请依次调用 tron_node_request 工具完成两个只读测试：第一，target 使用 full，path 使用 /wallet/getnowblock，body_json 使用空对象 {}，查询 TRON full 节点最新区块；第二，target 使用 solidity，path 使用 /walletsolidity/getnowblock，body_json 使用空对象 {}，查询 TRON solidity 节点最新固化区块。请基于工具返回结果解释 blockID、block_header、txID、raw_data、contractRet 等字段。"

run_chat "16_contract_decode" "company-contract-decode-real" \
"请务必调用 contract_decode_tx_input 工具解析交易。tx 为 0x7d22665a0fdd2d0f3e586b65aab9c5117bcf65719a1f70749e173e3f006faa59，address 为 0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45。请基于工具返回结果解释方法名和参数含义。"

run_chat "17_postgres_memory_public" "company-pg-memory-public" \
"请务必调用 postgres_select 工具执行 SQL：SELECT memory_key, summary, source_thread_id, created_at, updated_at FROM agent_memory_public ORDER BY updated_at DESC LIMIT 5。请基于返回结果说明当前最近的长期记忆概要。"

echo "Done. Review JSON files in: $TEST_OUT_DIR"
echo "Tip: search for tool_call_request / tool_result / status in each JSON output."
