"""
OpenTelemetry bridge for DevOS TraceContext.

DevOS durable tracing remains product authority for Nuha/evidence.
OTel is optional interoperability/export — never execution authority.
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from typing import Any, Generator, Optional

logger = logging.getLogger("devos.otel")

_SECRET_KEY = re.compile(r"(?i)(token|secret|password|authorization|api[_-]?key|jwt|cookie)")
_SECRET_VAL = re.compile(r"(?i)(api[_-]?key|token|password|secret|bearer)\s*[:=]\s*\S+")

_sdk_available = False
_tracer = None
_provider = None
_exporter_configured = False
_exporter_healthy = True
_initialized = False

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import Status, StatusCode, SpanKind
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    _sdk_available = True
except ImportError:
    otel_trace = None  # type: ignore
    TracerProvider = None  # type: ignore
    SpanKind = None  # type: ignore
    Status = StatusCode = None  # type: ignore
    TraceContextTextMapPropagator = None  # type: ignore


def sanitize_attributes(attrs: Optional[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (attrs or {}).items():
        ks = str(k).lower()
        if _SECRET_KEY.search(ks):
            continue
        if isinstance(v, (dict, list)):
            continue
        s = str(v)
        if _SECRET_VAL.search(s):
            s = "[REDACTED]"
        # prefix non-devos keys
        key = k if str(k).startswith("devos.") else f"devos.{k}" if not str(k).startswith("http.") else str(k)
        out[key] = s[:500]
    return out


class _InMemoryExporter:
    """Test/local exporter — does not require external collector."""

    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(list(spans))
        return getattr(SpanExportResult, "SUCCESS", 0) if _sdk_available else 0

    def shutdown(self):
        pass


_memory_exporter: Optional[_InMemoryExporter] = None


def init_otel(*, force_memory: bool = False) -> dict:
    """Initialize OTel SDK if available. Safe no-op if packages missing."""
    global _initialized, _tracer, _provider, _exporter_configured, _exporter_healthy, _memory_exporter
    if _initialized and not force_memory:
        return otel_health()
    if not _sdk_available:
        _initialized = True
        return otel_health()

    enabled = os.environ.get("OTEL_ENABLED", "false").lower() in ("1", "true", "yes")
    service = os.environ.get("OTEL_SERVICE_NAME", "devos")
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()

    resource = Resource.create({"service.name": service})
    provider = TracerProvider(resource=resource)
    _memory_exporter = _InMemoryExporter()
    provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))

    if enabled and endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            headers = {}
            raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
            # parse key=value,key2=value2 without logging values
            for part in raw.split(","):
                if "=" in part:
                    k, _v = part.split("=", 1)
                    headers[k.strip()] = _v.strip()
            exp = OTLPSpanExporter(endpoint=endpoint, headers=headers or None)
            provider.add_span_processor(BatchSpanProcessor(exp))
            _exporter_configured = True
            _exporter_healthy = True
        except Exception as e:
            logger.warning("OTLP exporter setup failed (non-fatal): %s", type(e).__name__)
            _exporter_configured = True
            _exporter_healthy = False
    else:
        _exporter_configured = False

    otel_trace.set_tracer_provider(provider)
    _provider = provider
    _tracer = otel_trace.get_tracer("devos")
    _initialized = True
    return otel_health()


def otel_health() -> dict:
    enabled = os.environ.get("OTEL_ENABLED", "false").lower() in ("1", "true", "yes")
    endpoint = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())
    return {
        "otel_enabled": enabled,
        "otel_sdk_available": _sdk_available,
        "otel_exporter_configured": _exporter_configured or (enabled and endpoint),
        "otel_exporter_healthy": _exporter_healthy if (_exporter_configured or not enabled) else True,
        "service_name": os.environ.get("OTEL_SERVICE_NAME", "devos"),
    }


def _kind(kind: str):
    if not _sdk_available or SpanKind is None:
        return None
    m = {
        "server": SpanKind.SERVER,
        "client": SpanKind.CLIENT,
        "producer": SpanKind.PRODUCER,
        "consumer": SpanKind.CONSUMER,
        "internal": SpanKind.INTERNAL,
        "mission": SpanKind.INTERNAL,
        "dag.node": SpanKind.INTERNAL,
        "deployment": SpanKind.CLIENT,
        "compensation": SpanKind.INTERNAL,
    }
    return m.get(kind, SpanKind.INTERNAL)


def parse_traceparent(header: Optional[str]) -> Optional[tuple[str, str, bool]]:
    """Parse W3C traceparent → (trace_id, parent_span_id, sampled) or None if malformed."""
    if not header or not isinstance(header, str):
        return None
    parts = header.strip().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, span_id, flags = parts
    if version != "00":
        return None
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    if not re.fullmatch(r"[0-9a-f]+", trace_id) or not re.fullmatch(r"[0-9a-f]+", span_id):
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    sampled = (int(flags, 16) & 1) == 1
    return trace_id, span_id, sampled


def format_traceparent(trace_id: str, span_id: str, sampled: bool = True) -> str:
    tid = trace_id.replace("-", "")[:32].ljust(32, "0")
    sid = span_id.replace("-", "")[:16].ljust(16, "0")
    return f"00-{tid}-{sid}-{'01' if sampled else '00'}"


@contextmanager
def start_otel_span(
    name: str,
    *,
    kind: str = "internal",
    attributes: Optional[dict] = None,
    devos_trace_id: Optional[str] = None,
    devos_span_id: Optional[str] = None,
    devos_parent_span_id: Optional[str] = None,
) -> Generator[Any, None, None]:
    """Start an OTel span correlated with DevOS IDs. No-op if SDK unavailable."""
    if not _initialized:
        try:
            init_otel()
        except Exception:
            pass
    if not _sdk_available or _tracer is None:
        yield None
        return
    attrs = sanitize_attributes(attributes)
    if devos_trace_id:
        attrs["devos.trace_id"] = devos_trace_id
    if devos_span_id:
        attrs["devos.span_id"] = devos_span_id
    if devos_parent_span_id:
        attrs["devos.parent_span_id"] = devos_parent_span_id
    try:
        with _tracer.start_as_current_span(name, kind=_kind(kind), attributes=attrs) as span:
            yield span
    except Exception as e:
        logger.debug("otel span failed (non-fatal): %s", type(e).__name__)
        yield None


def record_exception(span, exc: BaseException) -> None:
    if span is None or not _sdk_available:
        return
    try:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
    except Exception:
        pass


def get_memory_spans() -> list:
    if _memory_exporter is None:
        return []
    return list(_memory_exporter.spans)
