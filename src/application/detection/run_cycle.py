from src.application.correlation.use_cases import CorrelateSignal, CorrelationResult
from src.application.detection.detectors import Detector
from src.domain.incident.entities import Signal


class RunDetectionCycle:
    """Run every detector once and feed each emitted signal through correlation.

    This is the read-only heartbeat of Phase 1: detectors observe, correlation turns
    observations into incidents. No cluster mutation happens anywhere in this path.
    """

    def __init__(
        self, detectors: list[Detector], correlate: CorrelateSignal
    ) -> None:
        self._detectors = detectors
        self._correlate = correlate

    async def execute(self) -> list[CorrelationResult]:
        results: list[CorrelationResult] = []
        for detector in self._detectors:
            for observed in await detector.detect():
                signal = Signal.observe(
                    fingerprint=observed.fingerprint,
                    source=observed.source,
                    severity=observed.severity,
                    summary=observed.summary,
                )
                results.append(await self._correlate.execute(signal))
        return results
