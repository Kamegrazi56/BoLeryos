"""Journal des actions vues sur le WebSocket de la page : jamais le jeton, lectures écartées par défaut, réponses rattachées."""

import json

from app.action_log import MAX_ENTRIES, ActionLog


def _send(log, action, request_id, payload=None):
    log.sent(json.dumps({"type": "ACTION", "action": action, "requestId": request_id, "payload": payload or {}}))


def test_actions_are_kept_with_their_values_and_the_reply():
    log = ActionLog()
    _send(log, "enqueue_factory_unit", "r1", {"planetId": "p1", "unitId": "pilote", "count": 10})
    log.received(json.dumps({"type": "ACTION_RESULT", "requestId": "r1", "success": True}))
    [entry] = log.entries()
    assert entry["action"] == "enqueue_factory_unit" and entry["payload"]["count"] == 10 and '"success": true' in entry["reply"]


def test_the_token_and_the_state_are_never_kept():
    log = ActionLog()
    log.sent(json.dumps({"type": "AUTH_INIT", "token": "secret-token", "clientContractVersion": 7}))
    log.received(json.dumps({"type": "STATE_UPDATE", "state": {"x": 1}}))
    assert log.entries(only_writes=False) == []
    assert "secret-token" not in json.dumps(log._entries.__repr__())


def test_reads_are_hidden_unless_asked_and_newest_come_first():
    log = ActionLog()
    _send(log, "get_unread_count", "r0")
    _send(log, "enqueue_building", "r1")
    _send(log, "enqueue_factory_unit", "r2")
    assert [e["action"] for e in log.entries()] == ["enqueue_factory_unit", "enqueue_building"]
    assert len(log.entries(only_writes=False)) == 3


def test_the_log_is_bounded_and_clearable():
    log = ActionLog()
    for i in range(MAX_ENTRIES + 10):
        _send(log, "enqueue_building", f"r{i}")
    assert len(log.entries()) == MAX_ENTRIES
    log.clear()
    assert log.entries() == []


def test_garbage_frames_are_ignored():
    log = ActionLog()
    log.sent("pas du json")
    log.received(b"\x00\x01")
    log.sent(json.dumps([1, 2]))
    assert log.entries(only_writes=False) == []
