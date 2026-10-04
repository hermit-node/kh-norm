from __future__ import annotations

import asyncio
import ipaddress
import logging
import sys
import threading
from collections.abc import Callable

from aiohttp import web


def validate_private_bind_host(host: str) -> str:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("service bind host must be a literal loopback or Tailscale IP") from exc
    if not (ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("service bind host must remain loopback or Tailscale-only")
    return host


class AiohttpThreadServer:
    """Run one aiohttp application on a dedicated asyncio loop.

    The compatibility surface intentionally mirrors the old ThreadingHTTPServer
    lifecycle used by norm_main: shutdown(), server_close(), accepting_requests,
    and active_request_count.  This lets the coordinator retain its proven startup
    and cleanup ordering while the HTTP transport becomes asyncio-native.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        app_factory: Callable[["AiohttpThreadServer"], web.Application],
        name: str,
        shutdown_timeout: float = 5.0,
    ) -> None:
        self.host = validate_private_bind_host(host)
        self.port = int(port)
        self.name = name
        self.shutdown_timeout = float(shutdown_timeout)
        self.accepting_requests = True
        self._active_requests = 0
        self._active_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread = threading.Thread(target=self._thread_main, name=name, daemon=True)
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: BaseException | None = None
        self._app_factory = app_factory

    @property
    def active_request_count(self) -> int:
        with self._active_lock:
            return self._active_requests

    def begin_request(self) -> None:
        with self._active_lock:
            self._active_requests += 1

    def end_request(self) -> None:
        with self._active_lock:
            self._active_requests = max(0, self._active_requests - 1)

    def start(self, timeout: float = 10.0) -> tuple["AiohttpThreadServer", threading.Thread]:
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError(f"{self.name} did not become ready within {timeout:.1f}s")
        if self._startup_error is not None:
            raise RuntimeError(f"{self.name} failed to start") from self._startup_error
        return self, self._thread

    def shutdown(self) -> None:
        self.accepting_requests = False
        loop = self._loop
        if loop is None or loop.is_closed() or self._stopped.is_set():
            return
        stop_event = self._stop_event
        if stop_event is not None:
            loop.call_soon_threadsafe(stop_event.set)

    def server_close(self) -> None:
        self.shutdown()

    @staticmethod
    def _new_event_loop() -> asyncio.AbstractEventLoop:
        # On Windows, the default ProactorEventLoop can permanently lose a TCP
        # listening socket after a transient AcceptEx WinError 64/10054.  These
        # dedicated aiohttp threads only need socket I/O (no asyncio subprocesses),
        # so use the selector implementation there and leave Norm's process-wide
        # event-loop policy untouched.
        if sys.platform == "win32":
            return asyncio.SelectorEventLoop()
        return asyncio.new_event_loop()

    def _thread_main(self) -> None:
        loop = self._new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stop_event = asyncio.Event()
        try:
            app = self._app_factory(self)
            runner = web.AppRunner(
                app,
                access_log=None,
                handle_signals=False,
                shutdown_timeout=self.shutdown_timeout,
            )
            self._runner = runner
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(
                runner,
                self.host,
                self.port,
                shutdown_timeout=self.shutdown_timeout,
                reuse_address=True,
            )
            loop.run_until_complete(site.start())
            self._ready.set()
            loop.run_until_complete(self._stop_event.wait())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            logging.exception("%s failed", self.name)
        finally:
            self.accepting_requests = False
            runner = self._runner
            if runner is not None:
                try:
                    loop.run_until_complete(runner.cleanup())
                except Exception:
                    logging.exception("%s cleanup failed", self.name)
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            except Exception:
                logging.exception("%s event-loop cleanup failed", self.name)
            finally:
                loop.close()
                self._stopped.set()
