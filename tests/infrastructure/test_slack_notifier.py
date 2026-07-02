import json
from uuid import uuid4

import httpx
import pytest

from src.domain.analysis.models import IncidentAnalysis, RemediationProposal
from src.domain.incident.value_objects import IncidentId
from src.domain.runbook.contract import RunbookId
from src.infrastructure.notification.slack_notifier import SlackIncidentNotifier


def _analysis() -> IncidentAnalysis:
    return IncidentAnalysis(
        summary="checkout OOMing",
        root_cause="memory leak in cart serializer",
        proposals=(
            RemediationProposal(
                RunbookId("increase-memory-limit"), "ns=prod", "reclaim headroom", 0.9
            ),
        ),
    )


@pytest.mark.asyncio
async def test_notify_posts_block_kit_message_to_webhook():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = SlackIncidentNotifier("https://hooks.slack.test/T/B/xyz", client)

    incident_id = IncidentId(uuid4())
    await notifier.notify_analysis(incident_id, _analysis())

    assert captured["url"] == "https://hooks.slack.test/T/B/xyz"
    body = captured["body"]
    # Fallback text + blocks are both present; the runbook + root cause are surfaced.
    assert "checkout OOMing" in body["text"]
    rendered = json.dumps(body["blocks"])
    assert "memory leak in cart serializer" in rendered
    assert "increase-memory-limit" in rendered
    # The advisory footer references the incident and never offers an execute affordance.
    assert str(incident_id) in rendered
    assert "action" not in rendered.lower()


@pytest.mark.asyncio
async def test_notify_raises_on_webhook_error():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="nope"))
    )
    notifier = SlackIncidentNotifier("https://hooks.slack.test/T/B/xyz", client)

    with pytest.raises(httpx.HTTPStatusError):
        await notifier.notify_analysis(IncidentId(uuid4()), _analysis())
