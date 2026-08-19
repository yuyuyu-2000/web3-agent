from __future__ import annotations

import json
from unittest.mock import patch

from chaincloud_agent_service.tools.tron_rpc import make_tron_transaction_lookup_tool


TXID = "a" * 64


def test_tron_transaction_lookup_combines_both_read_only_responses() -> None:
    responses = [
        json.dumps({"txID": TXID, "raw_data": {"contract": []}}),
        json.dumps({"id": TXID, "fee": 1000, "receipt": {"result": "SUCCESS"}}),
    ]

    with patch(
        "chaincloud_agent_service.tools.tron_rpc._post", side_effect=responses
    ) as post:
        result = json.loads(make_tron_transaction_lookup_tool().invoke({"txid": TXID}))

    assert result["txid"] == TXID
    assert result["transaction"]["txID"] == TXID
    assert result["transaction_info"]["receipt"]["result"] == "SUCCESS"
    assert [call.args[1] for call in post.call_args_list] == [
        "/wallet/gettransactionbyid",
        "/wallet/gettransactioninfobyid",
    ]
    assert all(call.args[2] == {"value": TXID} for call in post.call_args_list)


def test_tron_transaction_lookup_rejects_invalid_txid_without_network_call() -> None:
    with patch("chaincloud_agent_service.tools.tron_rpc._post") as post:
        result = json.loads(
            make_tron_transaction_lookup_tool().invoke({"txid": "not-a-txid"})
        )

    assert "error" in result
    post.assert_not_called()


def test_tron_transaction_lookup_keeps_partial_result() -> None:
    with patch(
        "chaincloud_agent_service.tools.tron_rpc._post",
        side_effect=[json.dumps({"txID": TXID}), TimeoutError()],
    ):
        result = json.loads(make_tron_transaction_lookup_tool().invoke({"txid": TXID}))

    assert result["transaction"]["txID"] == TXID
    assert result["transaction_info"] is None
    assert result["errors"]["transaction_info"] == "请求超时: 60s"
