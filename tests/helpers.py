import json


def receive_until(ws, predicate, attempts=300):
    """Lit le flux de la vue en direct jusqu'à ce que `predicate(statut, image)` soit vrai.

    Le flux mêle des messages texte (statut JSON) et des messages binaires (images JPEG).
    Retourne (statut, image).
    """
    state, frame = None, None
    for _ in range(attempts):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            frame = msg["bytes"]
        elif msg.get("text") is not None:
            state = json.loads(msg["text"])
        if state is not None and predicate(state, frame):
            return state, frame
    raise AssertionError("Condition jamais atteinte dans le flux de la vue en direct")
