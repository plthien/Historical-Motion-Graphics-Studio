from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


class BoundedTransportError(Exception):
    pass


class BoundedTransportDeadline(BoundedTransportError):
    pass


class BoundedTransportUnavailable(BoundedTransportError):
    pass


class BoundedTransportBodyTooLarge(BoundedTransportError):
    pass


class BoundedTransportRequestTooLarge(BoundedTransportError):
    pass


class BoundedTransportMalformedResponse(BoundedTransportError):
    pass


class BoundedTransportHttpError(BoundedTransportError):
    def __init__(self, status_code: int, body: bytes) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True, slots=True)
class DownloadTransportMetadata:
    content_type: str | None
    size_bytes: int
    sha256: str


class BoundedHttpTransport:
    _WATCHDOG_PREFIX = "media-http-watchdog-"
    _CHUNK_SIZE = 64 * 1024

    @classmethod
    def active_watchdog_names(cls) -> tuple[str, ...]:
        return tuple(
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith(cls._WATCHDOG_PREFIX) and thread.is_alive()
        )

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        total_deadline_seconds: float,
        io_timeout_seconds: float,
        max_request_bytes: int,
        max_response_bytes: int,
        expected_status: int,
    ) -> dict[str, object]:
        absolute_deadline = time.monotonic() + float(total_deadline_seconds)
        status, _, raw = self._request_bytes(
            method=method,
            url=url,
            headers=headers,
            body=body,
            total_deadline_seconds=total_deadline_seconds,
            io_timeout_seconds=io_timeout_seconds,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            absolute_deadline=absolute_deadline,
        )
        self._raise_if_deadline_passed(absolute_deadline)
        if status != expected_status:
            raise BoundedTransportHttpError(status, raw)
        if not raw:
            raise BoundedTransportMalformedResponse("empty response body")
        try:
            text = raw.decode("utf-8")
            self._raise_if_deadline_passed(absolute_deadline)
        except UnicodeDecodeError as exc:
            raise BoundedTransportMalformedResponse("invalid UTF-8") from exc
        try:
            payload = json.loads(text)
            self._raise_if_deadline_passed(absolute_deadline)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise BoundedTransportMalformedResponse("malformed JSON") from exc
        if not isinstance(payload, dict):
            raise BoundedTransportMalformedResponse("JSON root must be an object")
        return payload

    def download_to_file(
        self,
        *,
        url: str,
        destination: Path,
        total_deadline_seconds: float,
        io_timeout_seconds: float,
        max_bytes: int,
    ) -> DownloadTransportMetadata:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        absolute_deadline = time.monotonic() + float(total_deadline_seconds)
        expired = threading.Event()
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        watchdog: threading.Timer | None = None
        size_bytes = 0
        digest = hashlib.sha256()
        try:
            # Filesystem preparation occurs before any network ownership or
            # watchdog lifecycle begins.
            destination.parent.mkdir(parents=True, exist_ok=True)
            parsed = self._parse_local_url(url)
            connection = self._new_connection(parsed, io_timeout_seconds)
            watchdog = self._start_watchdog(
                connection, expired, max(absolute_deadline - time.monotonic(), 0.000001)
            )
            deadline = absolute_deadline
            self._set_operation_timeout(connection, deadline, io_timeout_seconds)
            connection.request("GET", self._request_target(parsed), headers={})
            self._set_operation_timeout(connection, deadline, io_timeout_seconds)
            response = connection.getresponse()
            if response.status != 200:
                error_body = self._read_response_body(
                    response,
                    connection,
                    deadline=deadline,
                    io_timeout_seconds=io_timeout_seconds,
                    max_bytes=min(max_bytes, 4096),
                )
                raise BoundedTransportHttpError(int(response.status), error_body)
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as exc:
                    raise BoundedTransportMalformedResponse("invalid Content-Length") from exc
                if declared > max_bytes:
                    raise BoundedTransportBodyTooLarge("declared body exceeds limit")
            content_type = response.getheader("Content-Type")
            normalized_content_type = content_type.split(";", 1)[0].strip().lower() if content_type else None
            with destination.open("xb") as handle:
                while True:
                    self._set_operation_timeout(connection, deadline, io_timeout_seconds)
                    reader = getattr(response, "read1", response.read)
                    chunk = reader(self._CHUNK_SIZE)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise BoundedTransportBodyTooLarge("body exceeds limit")
                    handle.write(chunk)
                    digest.update(chunk)
            return DownloadTransportMetadata(
                content_type=normalized_content_type,
                size_bytes=size_bytes,
                sha256=digest.hexdigest(),
            )
        except Exception as exc:
            if expired.is_set():
                raise BoundedTransportDeadline("absolute request deadline exceeded") from exc
            if isinstance(exc, BoundedTransportError):
                raise
            if isinstance(exc, (socket.timeout, TimeoutError)):
                raise BoundedTransportDeadline("I/O timeout") from exc
            if isinstance(exc, (OSError, http.client.HTTPException, ssl.SSLError, ValueError, TypeError)):
                raise BoundedTransportUnavailable("transport failure") from exc
            raise BoundedTransportUnavailable("transport failure") from exc
        finally:
            self._finish_request(connection, response, watchdog)

    def _request_bytes(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        total_deadline_seconds: float,
        io_timeout_seconds: float,
        max_request_bytes: int,
        max_response_bytes: int,
        absolute_deadline: float,
    ) -> tuple[int, Mapping[str, str], bytes]:
        if type(max_request_bytes) is not int or max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be a positive integer")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        if body is not None:
            if type(body) is not bytes:
                raise ValueError("request body must be bytes or null")
            if len(body) > max_request_bytes:
                raise BoundedTransportRequestTooLarge("request body exceeds control-plane limit")
        expired = threading.Event()
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        watchdog: threading.Timer | None = None
        try:
            parsed = self._parse_local_url(url)
            connection = self._new_connection(parsed, io_timeout_seconds)
            watchdog = self._start_watchdog(
                connection, expired, max(absolute_deadline - time.monotonic(), 0.000001)
            )
            deadline = absolute_deadline
            self._set_operation_timeout(connection, deadline, io_timeout_seconds)
            connection.request(method, self._request_target(parsed), body=body, headers=dict(headers))
            self._set_operation_timeout(connection, deadline, io_timeout_seconds)
            response = connection.getresponse()
            raw = self._read_response_body(
                response,
                connection,
                deadline=deadline,
                io_timeout_seconds=io_timeout_seconds,
                max_bytes=max_response_bytes,
            )
            return int(response.status), {key: value for key, value in response.getheaders()}, raw
        except Exception as exc:
            if expired.is_set():
                raise BoundedTransportDeadline("absolute request deadline exceeded") from exc
            if isinstance(exc, BoundedTransportError):
                raise
            if isinstance(exc, (socket.timeout, TimeoutError)):
                raise BoundedTransportDeadline("I/O timeout") from exc
            if isinstance(exc, (OSError, http.client.HTTPException, ssl.SSLError, ValueError, TypeError)):
                raise BoundedTransportUnavailable("transport failure") from exc
            raise BoundedTransportUnavailable("transport failure") from exc
        finally:
            self._finish_request(connection, response, watchdog)

    def _read_response_body(
        self,
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
        *,
        deadline: float,
        io_timeout_seconds: float,
        max_bytes: int,
    ) -> bytes:
        content_length = response.getheader("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise BoundedTransportMalformedResponse("invalid Content-Length") from exc
            if declared > max_bytes:
                raise BoundedTransportBodyTooLarge("declared body exceeds limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            self._set_operation_timeout(connection, deadline, io_timeout_seconds)
            reader = getattr(response, "read1", response.read)
            chunk = reader(min(self._CHUNK_SIZE, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise BoundedTransportBodyTooLarge("body exceeds limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _raise_if_deadline_passed(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise BoundedTransportDeadline("absolute request deadline exceeded")

    @staticmethod
    def _parse_local_url(url: str):
        if not isinstance(url, str) or not url:
            raise ValueError("URL must be a non-empty string")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only HTTP(S) is supported")
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("URL must target localhost")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL userinfo is forbidden")
        return parsed

    @staticmethod
    def _request_target(parsed) -> str:
        path = parsed.path or "/"
        return f"{path}?{parsed.query}" if parsed.query else path

    @staticmethod
    def _new_connection(parsed, io_timeout_seconds: float) -> http.client.HTTPConnection:
        port = parsed.port
        if parsed.scheme == "https":
            return http.client.HTTPSConnection(parsed.hostname, port=port, timeout=io_timeout_seconds)
        return http.client.HTTPConnection(parsed.hostname, port=port, timeout=io_timeout_seconds)

    def _start_watchdog(
        self,
        connection: http.client.HTTPConnection,
        expired: threading.Event,
        total_deadline_seconds: float,
    ) -> threading.Timer:
        if type(total_deadline_seconds) not in {int, float} or isinstance(total_deadline_seconds, bool) or total_deadline_seconds <= 0:
            raise ValueError("total_deadline_seconds must be positive")

        def expire() -> None:
            expired.set()
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                connection.close()
            except OSError:
                pass

        watchdog = threading.Timer(total_deadline_seconds, expire)
        watchdog.name = f"{self._WATCHDOG_PREFIX}{id(watchdog)}"
        watchdog.daemon = True
        try:
            watchdog.start()
        except Exception:
            watchdog.cancel()
            if watchdog.is_alive() and watchdog is not threading.current_thread():
                watchdog.join(timeout=1.0)
            raise
        return watchdog

    @staticmethod
    def _set_operation_timeout(
        connection: http.client.HTTPConnection,
        deadline: float,
        io_timeout_seconds: float,
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedTransportDeadline("absolute request deadline exceeded")
        timeout = min(float(io_timeout_seconds), remaining)
        connection.timeout = timeout
        if connection.sock is not None:
            connection.sock.settimeout(timeout)

    @staticmethod
    def _finish_request(
        connection: http.client.HTTPConnection | None,
        response: http.client.HTTPResponse | None,
        watchdog: threading.Timer | None,
    ) -> None:
        if watchdog is not None:
            watchdog.cancel()
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=1.0)
