from datetime import timedelta

import httpx

from src.domain.detection.metrics_gateway import MetricsGateway


class PrometheusMetricsGateway(MetricsGateway):
    """Prometheus HTTP API adapter for the MetricsGateway port.

    The PromQL lives here and nowhere else. `error_ratio` assumes an
    `http_requests_total{service,status}` counter convention; adjust the query templates to
    match your instrumentation without touching any detector.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def error_ratio(self, service: str, window: timedelta) -> float:
        window_s = f"{int(window.total_seconds())}s"
        query = (
            f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[{window_s}]))'
            f' / sum(rate(http_requests_total{{service="{service}"}}[{window_s}]))'
        )
        value = await self._instant_query(query)
        return value if value is not None else 0.0

    async def gauge(self, service: str, metric: str) -> float | None:
        return await self._instant_query(f'{metric}{{service="{service}"}}')

    async def _instant_query(self, query: str) -> float | None:
        response = await self._client.get(
            f"{self._base_url}/api/v1/query", params={"query": query}
        )
        response.raise_for_status()
        results = response.json().get("data", {}).get("result", [])
        if not results:
            return None
        # instant vector sample: ["<unix_ts>", "<value>"]
        return float(results[0]["value"][1])
