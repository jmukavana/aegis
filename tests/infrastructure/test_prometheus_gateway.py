from datetime import timedelta

import httpx
import pytest

from src.infrastructure.metrics.prometheus_gateway import PrometheusMetricsGateway


def _gateway(handler) -> PrometheusMetricsGateway:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PrometheusMetricsGateway("http://prometheus:9090", client=client)


@pytest.mark.asyncio
async def test_error_ratio_parses_instant_vector():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": [1719830400, "0.05"]}],
                },
            },
        )

    ratio = await _gateway(handler).error_ratio("checkout", timedelta(minutes=5))

    assert ratio == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_gauge_returns_none_for_empty_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "success", "data": {"result": []}}
        )

    value = await _gateway(handler).gauge("checkout", "container_memory_rss_ratio")

    assert value is None
