import httpx

from src.domain.analysis.models import IncidentAnalysis
from src.domain.incident.value_objects import IncidentId
from src.domain.notification.notifier import IncidentNotifier

_MAX_PROPOSALS = 5


class SlackIncidentNotifier(IncidentNotifier):
    """Slack Incoming Webhook adapter for the notifier port.

    Posts the analysis as a Block Kit message (a header, the root cause, and the ranked
    runbook proposals) to a webhook URL. The message is informational only — no buttons, no
    actions — matching the read-only Phase 2 boundary. Provider-neutral over httpx; swap this
    adapter for Teams/PagerDuty/etc. without touching the application layer.
    """

    def __init__(self, webhook_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._webhook_url = webhook_url
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def notify_analysis(
        self, incident_id: IncidentId, analysis: IncidentAnalysis
    ) -> None:
        response = await self._client.post(
            self._webhook_url, json=_message(incident_id, analysis)
        )
        response.raise_for_status()


def _message(incident_id: IncidentId, analysis: IncidentAnalysis) -> dict:
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔎 {analysis.summary}"[:150]},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root cause:* {analysis.root_cause or '_unknown_'}",
            },
        },
    ]

    if analysis.proposals:
        ranked = "\n".join(
            f"{i}. `{p.runbook_id}` — {p.reasoning} "
            f"_(confidence {p.confidence:.0%})_"
            for i, p in enumerate(analysis.proposals[:_MAX_PROPOSALS], start=1)
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Proposed runbooks:*\n{ranked}"},
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Incident `{incident_id}` · advisory only — "
                        "nothing has been executed (Phase 2)."
                    ),
                }
            ],
        }
    )

    # `text` is the notification/fallback shown in previews and by clients that ignore blocks.
    return {"text": f"Incident analysis: {analysis.summary}", "blocks": blocks}
