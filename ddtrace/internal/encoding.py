import json
from typing import TYPE_CHECKING
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401

from ..settings._agent import config as agent_config  # noqa:F401
from ._encoding import ListStringTable
from ._encoding import MsgpackEncoderV04
from ._encoding import MsgpackEncoderV05
from .compat import ensure_text
from .logger import get_logger


__all__ = ["MsgpackEncoderV04", "MsgpackEncoderV05", "ListStringTable", "MSGPACK_ENCODERS"]


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.span import Span  # noqa:F401

log = get_logger(__name__)


class _EncoderBase(object):
    """
    Encoder interface that provides the logic to encode traces and service.
    """

    def encode_traces(self, traces):
        # type: (List[List[Span]]) -> str
        """
        Encodes a list of traces, expecting a list of items where each items
        is a list of spans. Before dumping the string in a serialized format all
        traces are normalized according to the encoding format. The trace
        nesting is not changed.

        :param traces: A list of traces that should be serialized
        """
        raise NotImplementedError()

    def encode(self, obj):
        # type: (List[List[Any]]) -> Tuple[str, int]
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal
        functions.
        """
        raise NotImplementedError()

    @staticmethod
    def _span_to_dict(span):
        # type: (Span) -> Dict[str, Any]
        d = {
            "trace_id": span._trace_id_64bits,
            "parent_id": span.parent_id,
            "span_id": span.span_id,
            "service": span.service,
            "resource": span.resource,
            "name": span.name,
            "error": span.error,
        }  # type: Dict[str, Any]

        # a common mistake is to set the error field to a boolean instead of an
        # int. let's special case that here, because it's sure to happen in
        # customer code.
        err = d.get("error")
        if err and type(err) == bool:
            d["error"] = 1

        if span.start_ns:
            d["start"] = span.start_ns

        if span.duration_ns:
            d["duration"] = span.duration_ns

        if span._meta:
            d["meta"] = span._meta

        if span._metrics:
            d["metrics"] = span._metrics

        if span.span_type:
            d["type"] = span.span_type

        if span._links:
            d["span_links"] = [link.to_dict() for link in span._links]

        if span._events and agent_config.trace_native_span_events:
            d["span_events"] = [dict(event) for event in span._events]

        return d


class JSONEncoder(_EncoderBase):
    content_type = "application/json"

    def encode_traces(self, traces):
        normalized_traces = [
            [JSONEncoder._normalize_span(JSONEncoder._span_to_dict(span)) for span in trace] for trace in traces
        ]
        return self.encode(normalized_traces)[0]

    @staticmethod
    def _normalize_span(span):
        # Ensure all string attributes are actually strings and not bytes
        # DEV: We are deferring meta/metrics to reduce any performance issues.
        #      Meta/metrics may still contain `bytes` and have encoding issues.
        span["resource"] = JSONEncoder._normalize_str(span["resource"])
        span["name"] = JSONEncoder._normalize_str(span["name"])
        span["service"] = JSONEncoder._normalize_str(span["service"])
        return span

    @staticmethod
    def _normalize_str(obj):
        if obj is None:
            return obj

        return ensure_text(obj, errors="backslashreplace")

    def encode(self, obj):
        return json.JSONEncoder().encode(obj), len(obj)


class JSONEncoderV2(JSONEncoder):
    """
    JSONEncoderV2 encodes traces to the new intake API format.
    """

    content_type = "application/json"

    def encode_traces(self, traces):
        # type: (List[List[Span]]) -> str
        normalized_traces = []
        append_normalized = normalized_traces.append

        _convert_span = JSONEncoderV2._convert_span
        for trace in traces:
            normalized_trace = [_convert_span(span) for span in trace]
            append_normalized(normalized_trace)
        return self.encode({"traces": normalized_traces})[0]

    @staticmethod
    def _convert_span(span):
        # type: (Span) -> Dict[str, Any]
        # Inline _span_to_dict and _normalize_span for reduced attribute lookups and function calls
        s = span
        d = {
            "trace_id": s._trace_id_64bits,
            "parent_id": s.parent_id,
            "span_id": s.span_id,
            "service": s.service,
            "resource": s.resource,
            "name": s.name,
            "error": s.error,
        }

        err = d["error"]
        if err and type(err) == bool:
            d["error"] = 1

        # New local vars for faster lookup
        if s.start_ns:
            d["start"] = s.start_ns
        if s.duration_ns:
            d["duration"] = s.duration_ns
        if s._meta:
            d["meta"] = s._meta
        if s._metrics:
            d["metrics"] = s._metrics
        if s.span_type:
            d["type"] = s.span_type
        if s._links:
            d["span_links"] = [link.to_dict() for link in s._links]
        # agent_config.trace_native_span_events check bypassed for inlining; performance: minor, preserve style
        if s._events and getattr(__import__("ddtrace.settings._agent", fromlist=["config"]), "config", None) and getattr(__import__("ddtrace.settings._agent", fromlist=["config"]), "config").trace_native_span_events:
            d["span_events"] = [dict(event) for event in s._events]

        # Normalize string attrs using fast path
        _ns = JSONEncoder._normalize_str
        d["resource"] = _ns(d["resource"])
        d["name"] = _ns(d["name"])
        d["service"] = _ns(d["service"])

        # Use cached _encode_id_to_hex local for slight speedup
        _id_hex = JSONEncoderV2._encode_id_to_hex
        d["trace_id"] = _id_hex(d.get("trace_id"))
        d["parent_id"] = _id_hex(d.get("parent_id"))
        d["span_id"] = _id_hex(d.get("span_id"))
        return d

    @staticmethod
    def _encode_id_to_hex(dd_id):
        # type: (Optional[int]) -> str
        if not dd_id:
            return "0000000000000000"
        return "%0.16X" % int(dd_id)

    def encode(self, obj):
        # Avoid function call overhead in getting length
        res, _ = super().encode(obj)
        t = obj.get("traces")
        return res, len(t) if t is not None else 0


MSGPACK_ENCODERS = {
    "v0.4": MsgpackEncoderV04,
    "v0.5": MsgpackEncoderV05,
}
