"""Shared MCP session for synchronous FastAPI/LangGraph workers.

The official SDK owns protocol negotiation, HTTP/SSE framing and request IDs.
One portal thread owns the session's entire async context-manager lifetime.
"""
import json
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import timedelta
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import anyio
import httpx
from anyio.from_thread import start_blocking_portal
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .execution import remaining


class AmapMCPError(RuntimeError):
    """Public, credential-free MCP failure."""


def decode_tool_result(result) -> dict:
    if result.isError:
        raise AmapMCPError("高德 MCP 工具执行失败")
    data = result.structuredContent
    if data is None:
        texts = [part.text for part in result.content if part.type == "text"]
        if len(texts) != 1 or len(texts[0]) > 2_000_000:
            raise AmapMCPError("高德 MCP 响应不符合 JSON 数据契约")
        try:
            data = json.loads(texts[0])
        except (TypeError, ValueError):
            raise AmapMCPError("高德 MCP 响应不是有效 JSON") from None
    if not isinstance(data, dict) or data.get("error") or data.get("status") in (0, "0"):
        raise AmapMCPError("高德 MCP 返回了无效数据或上游错误")
    return data


class AmapMCPClient:
    def __init__(self, url: str, api_key: str, timeout: float = 20):
        parts = urlsplit(url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("AMAP_MCP_URL 必须使用 HTTPS，本机测试可使用 HTTP")
        if not parts.hostname or parts.username or parts.password or parts.fragment:
            raise ValueError("AMAP_MCP_URL 格式无效")
        query = [(key, value) for key, value in parse_qsl(parts.query) if key.lower() != "key"]
        self._url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*query, ("key", api_key)]), ""))
        self._timeout = timeout
        self._lock = Lock()
        self._portal_cm = self._portal = self._task = self._session = None
        self._tools: dict = {}
        self._stop = None
        self._last_calls: dict[str, float] = {}

    async def _serve(self, startup_timeout, *, task_status=anyio.TASK_STATUS_IGNORED):
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as http_client, streamable_http_client(
            self._url, http_client=http_client
        ) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self._timeout)) as session:
                with anyio.fail_after(startup_timeout):
                    await session.initialize()
                    tools = {}
                    cursor = None
                    for _ in range(20):
                        page = await session.list_tools(cursor=cursor)
                        tools.update({tool.name: tool for tool in page.tools})
                        cursor = page.nextCursor
                        if not cursor:
                            break
                    else:
                        raise AmapMCPError("高德 MCP 工具列表分页异常")
                self._stop = anyio.Event()
                self._call_lock = anyio.Lock()
                task_status.started((session, tools))
                await self._stop.wait()

    def _connect(self, startup_timeout=None):
        if self._task is not None and not self._task.done():
            return
        self._close_locked()
        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        try:
            self._task, (self._session, self._tools) = self._portal.start_task(self._serve, startup_timeout or self._timeout)
        except Exception:
            self._close_locked()
            raise AmapMCPError("高德 MCP 连接或工具发现失败，请检查配置与服务状态") from None

    async def _invoke(self, name, arguments, timeout):
        # Per-tool throttling does not serialize unrelated weather/POI calls.
        with anyio.fail_after(timeout):
            async with self._call_lock:
                now = anyio.current_time()
                slot = max(now, self._last_calls.get(name, 0) + 0.4)
                self._last_calls[name] = slot
            await anyio.sleep(max(0, slot - anyio.current_time()))
            return await self._session.call_tool(name, arguments, read_timeout_seconds=timedelta(seconds=timeout))

    def call(self, name: str, arguments: dict) -> dict:
        if not self._lock.acquire(timeout=remaining(self._timeout)):
            raise TimeoutError("高德 MCP 等待连接超时")
        try:
            self._connect(remaining(self._timeout))
            tool = self._tools.get(name)
            if tool is None:
                raise AmapMCPError(f"高德 MCP 缺少工具：{name}")
            try:
                Draft202012Validator(tool.inputSchema).validate(arguments)
            except Exception:
                raise AmapMCPError(f"高德 MCP 工具参数不符合契约：{name}") from None
            timeout = remaining(self._timeout)
            future = self._portal.start_task_soon(self._invoke, name, arguments, timeout)
        finally:
            self._lock.release()
        try:
            return decode_tool_result(future.result(timeout=timeout))
        except (FutureTimeout, TimeoutError):
            future.cancel()
            raise TimeoutError("高德 MCP 工具调用超时") from None
        except AmapMCPError:
            raise
        except Exception:
            raise AmapMCPError(f"高德 MCP 工具调用失败：{name}") from None

    def tools(self) -> list[dict]:
        with self._lock:
            self._connect()
            return [tool.model_dump() for tool in self._tools.values()]

    def _close_locked(self):
        try:
            if self._task and not self._task.done():
                self._portal.call(self._stop.set)
                try:
                    self._task.result(timeout=self._timeout + 2)
                except Exception:
                    self._task.cancel()
        finally:
            if self._portal_cm:
                self._portal_cm.__exit__(None, None, None)
            self._portal_cm = self._portal = self._task = self._session = None
            self._tools = {}

    def close(self):
        with self._lock:
            self._close_locked()
