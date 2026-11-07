from contextlib import closing
import json
from decimal import Decimal

from backend.checklist import get_checklist
from backend.dataset import import_dataset
from backend.db import connect_db, init_db
from backend.stats import get_cost_stats


def test_cost_includes_retries_appeals_and_unknown_usage(tmp_path):
    path = tmp_path / "stats.sqlite3"
    init_db(path)
    with closing(connect_db(path)) as conn:
        import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 1, "records": [
            {"conversation_id": "c", "language": "RU", "channel": "agent chat", "transcript": [{"speaker": "operator", "timestamp": "00:00", "text": "Добрый день"}]}]}})
        vid = get_checklist(conn)["checklist_version_id"]
        conn.execute("INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,conversation_snapshot_json,status,created_at,updated_at) VALUES ('e','c',?,1,'{}','completed','now','now')", (vid,))
        for i, purpose in enumerate(["evaluation", "retry", "appeal"]):
            conn.execute("INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,http_status,duration_ms,prompt_tokens,completion_tokens,total_tokens,price_input_per_1m,price_output_per_1m,cost,created_at) VALUES (?,'e',?,'test',?,200,1,10,20,30,'1','2','0.00005','now')", (str(i), purpose, i + 1))
        conn.commit()
        stats = get_cost_stats(conn)
        assert stats["total"]["cost"] == "0.00015"
        assert stats["total"]["total_tokens"] == 90
        assert stats["total"]["retry_calls"] == 1
        assert stats["evaluations"][0]["usage"]["calls"] == 3
        assert stats["average_per_evaluation"]["cost"] == "0.00015"
        conn.execute("INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,error_code,duration_ms,created_at) VALUES ('bad','e','retry','test',4,'MODEL_TIMEOUT',1,'now')")
        conn.commit()
        stats = get_cost_stats(conn)
        assert stats["total"]["cost"] is None
        assert stats["total"]["known_cost"] == "0.00015"
        assert not stats["total"]["usage_complete"]
        assert stats["average_per_evaluation"]["total_tokens"] == "90.00"
        assert stats["total"]["price_message"] == "цена не задана"


def test_empty_stats_do_not_invent_price(tmp_path):
    path = tmp_path / "empty.sqlite3"
    init_db(path)
    with closing(connect_db(path)) as conn:
        result = get_cost_stats(conn)
    assert result["total"]["total_tokens"] == 0
    assert result["total"]["cost"] is None
    assert result["average_per_evaluation"]["cost"] is None


def _add_evaluation(conn, eid, *, result=None, status="completed"):
    import_dataset(conn, {"error": "", "isSuccess": True, "data": {"dataset_group_id": "DEV", "total_records": 1, "records": [
        {"conversation_id": "c", "language": "RU", "channel": "agent chat", "transcript": [
            {"speaker": "operator", "timestamp": "00:00", "text": "Добрый день"}]}]}})
    vid = get_checklist(conn)["checklist_version_id"]
    encoded = json.dumps(result) if result is not None else None
    conn.execute("INSERT INTO evaluations(evaluation_id,conversation_id,checklist_version_id,conversation_revision,"
                 "conversation_snapshot_json,status,created_at,updated_at,original_result_json,final_result_json) "
                 "VALUES (?,'c',?,1,'{}',?,'now','now',?,?)", (eid, vid, status, encoded, encoded))
    conn.commit()


def test_average_cost_preserves_small_amounts_across_evaluations(tmp_path):
    path = tmp_path / "mean.sqlite3"
    init_db(path)
    with closing(connect_db(path)) as conn:
        _add_evaluation(conn, "first")
        _add_evaluation(conn, "second")
        for i, purpose in enumerate(["evaluation", "retry", "appeal"]):
            conn.execute("INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,http_status,duration_ms,"
                         "prompt_tokens,completion_tokens,total_tokens,price_input_per_1m,price_output_per_1m,cost,created_at) "
                         "VALUES (?,'first',?,'test',?,200,1,10,20,30,'1','2','0.00005','now')", (str(i), purpose, i + 1))
        conn.execute("INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,http_status,duration_ms,"
                     "prompt_tokens,completion_tokens,total_tokens,price_input_per_1m,price_output_per_1m,cost,created_at) "
                     "VALUES ('last','second','evaluation','test',1,200,1,20,40,60,'1','2','0.0001','now')")
        conn.commit()
        result = get_cost_stats(conn)
    assert result["evaluation_count"] == 2
    assert result["average_per_evaluation"]["total_tokens"] == "75.00"
    assert result["average_per_evaluation"]["cost"] == "0.000125"
    assert result["average_per_evaluation"]["cost_complete"] is True
    assert Decimal(result["total"]["cost"]) == Decimal("0.00025")
    assert {item["evaluation_id"]: item["usage"]["calls"] for item in result["evaluations"]} == {"first": 3, "second": 1}


def test_weight_recalculation_without_model_has_known_zero_usage(tmp_path):
    path = tmp_path / "recalculation.sqlite3"
    init_db(path)
    with closing(connect_db(path)) as conn:
        _add_evaluation(conn, "recalculation", result={"reevaluation": {"mode": "recalculation"}})
        result = get_cost_stats(conn)
        usage = result["evaluations"][0]["usage"]
        assert usage["calls"] == 0
        assert usage["prompt_tokens"] == usage["completion_tokens"] == usage["total_tokens"] == 0
        assert usage["cost"] == usage["known_cost"] == "0"
        assert usage["usage_complete"] is usage["cost_complete"] is True
        assert usage["price_status"] == "price_not_set"
        assert result["average_per_evaluation"]["cost"] == "0"
        assert result["average_per_evaluation"]["total_tokens"] == "0.00"
        assert result["total"]["cost"] == "0"
        assert result["total"]["price_message"] == "цена не задана"
        # A failed/incomplete record without audit calls cannot claim known cost.
        _add_evaluation(conn, "failed", status="failed")
        result = get_cost_stats(conn)
        assert result["average_per_evaluation"]["cost"] is None
        assert result["average_per_evaluation"]["cost_complete"] is False
        assert result["average_per_evaluation"]["usage_complete"] is False


def test_zero_call_recalculation_counts_in_mean_denominator(tmp_path):
    path = tmp_path / "mixed.sqlite3"
    init_db(path)
    with closing(connect_db(path)) as conn:
        _add_evaluation(conn, "recalculation", result={"reevaluation": {"mode": "recalculation"}})
        _add_evaluation(conn, "full")
        conn.execute("INSERT INTO llm_calls(call_id,evaluation_id,purpose,model,attempt,http_status,duration_ms,"
                     "prompt_tokens,completion_tokens,total_tokens,price_input_per_1m,price_output_per_1m,cost,created_at) "
                     "VALUES ('a','full','evaluation','test',1,200,1,1,1,2,'0.00001','0.00001','0.00000000002','now')")
        conn.commit()
        result = get_cost_stats(conn)
    assert result["average_per_evaluation"]["cost"] == "0.00000000001"
    assert result["average_per_evaluation"]["total_tokens"] == "1.00"
    assert result["average_per_evaluation"]["usage_complete"] is True
