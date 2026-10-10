import json
import os
from datetime import datetime, timezone

TOP_K = 6
NOTE_CHARS = 400

_client = None


def enabled():
    return bool(os.environ.get("AGENTCORE_MEMORY_ID"))


def _agentcore():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "bedrock-agentcore",
            config=Config(connect_timeout=3, read_timeout=6, retries={"max_attempts": 1}),
        )
    return _client


def recall(actor_id, question):
    """What the garage remembers about this signed-in person, as a prompt block. Never raises."""
    if not enabled():
        return ""
    try:
        found = _agentcore().retrieve_memory_records(
            memoryId=os.environ["AGENTCORE_MEMORY_ID"],
            namespacePath=f"/actor/{actor_id}/",
            searchCriteria={"searchQuery": question[:400], "topK": TOP_K},
        )
        notes = []
        for record in found.get("memoryRecordSummaries") or []:
            text = " ".join(str((record.get("content") or {}).get("text") or "").split())
            if text and text not in notes:
                notes.append(text[:NOTE_CHARS])
    except Exception as exc:
        print(json.dumps({"memory_recall_error": type(exc).__name__}))
        return ""
    if not notes:
        return ""
    return "Memory (what you remember about this person from earlier chats; the Season JSON wins on facts):\n" + "\n".join("- " + note for note in notes) + "\n\n"


def record(actor_id, session_id, question, answer):
    """Store the exchange so AgentCore can extract facts, preferences and a session summary. Never raises."""
    if not enabled():
        return
    try:
        _agentcore().create_event(
            memoryId=os.environ["AGENTCORE_MEMORY_ID"],
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[
                {"conversational": {"role": "USER", "content": {"text": question[:1000]}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": answer[:2000]}}},
            ],
        )
    except Exception as exc:
        print(json.dumps({"memory_record_error": type(exc).__name__}))
