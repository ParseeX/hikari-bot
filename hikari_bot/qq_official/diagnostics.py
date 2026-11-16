"""Allowlisted exception metadata. Never format exceptions or log records."""
import json


SAFE_TYPES = frozenset({
    "RuntimeError", "ValueError", "TypeError", "ImportError", "ModuleNotFoundError",
    "TimeoutError", "ConnectionError", "PermissionError", "FileNotFoundError", "OSError",
    "ValidationError", "ClientConnectorError", "ClientConnectorDNSError",
    "ClientConnectorCertificateError", "ClientConnectorSSLError", "ClientResponseError",
    "ServerDisconnectedError", "ClientConnectionError", "NetworkError", "ActionFailed",
    "UnauthorizedException", "RateLimitException", "WebSocketClosed", "CancelledError",
})


def exception_metadata(exc):
    # ExceptionGroup messages and tracebacks can contain secrets. Only traverse leaves.
    from exceptiongroup import BaseExceptionGroup
    if isinstance(exc, BaseExceptionGroup):
        result = []
        for child in exc.exceptions[:4]:
            result.extend(exception_metadata(child))
        return result[:4]
    name = type(exc).__name__ if exc is not None else "None"
    result = {"type": name if name in SAFE_TYPES or name == "None" else "OtherException"}
    if isinstance(exc, OSError):
        for key in ("errno", "winerror"):
            value = getattr(exc, key, None)
            if type(value) is int and 0 <= value <= 65535:
                result[key] = value
    # These fields come from known SDK/HTTP exception classes, not arbitrary objects.
    if type(exc).__module__ == "nonebot.adapters.qq.exception" and name in {
            "ActionFailed", "UnauthorizedException", "RateLimitException"}:
        status, code = exc.status_code, exc.code
        if type(status) is int and 100 <= status <= 599:
            result["http_status"] = status
        if type(code) is int and 0 <= code <= 99999999:
            result["sdk_code"] = code
    elif type(exc).__module__.startswith("aiohttp.") and name == "ClientResponseError":
        if type(exc.status) is int and 100 <= exc.status <= 599:
            result["http_status"] = exc.status
    return [result]


class Diagnostics:
    def __init__(self, output, session):
        self.output = output
        self.session = session
        self.phase = "setup"
        self.failed = False
        self.shutdown_failed = False
        self._seen = set()

    def report(self, exc, *, code="exception"):
        self.failed = True
        if self.session.matched:
            self.shutdown_failed = True
        payload = {"phase": "shutdown" if self.session.matched else self.phase,
                   "matched": self.session.matched, "code": code,
                   "errors": exception_metadata(exc)}
        line = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        # Limit repeated errors; do not retain any original message or exception.
        if line not in self._seen and len(self._seen) < 6:
            self._seen.add(line)
            self.output("PAIR_DIAGNOSTIC " + line)

    def sink(self, message):
        record = message.record
        exception = record.get("exception")
        self.report(exception.value if exception else None,
                    code="sdk_error" if exception is None else "exception")
