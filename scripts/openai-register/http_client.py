"""Small curl_cffi-based HTTP client with OpenAI helpers."""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from curl_cffi.requests import Session
from config import OPENAI_API_ENDPOINTS

@dataclass
class RequestConfig:
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
    impersonate: str = "chrome"
    verify_ssl: bool = True
    follow_redirects: bool = True

class HTTPClientError(Exception):
    pass

class OpenAIHTTPClient:
    def __init__(self, proxy_url: Optional[str] = None, config: Optional[RequestConfig] = None):
        self.proxy_url = proxy_url
        self.config = config or RequestConfig()
        self._session: Optional[Session] = None
    @property
    def proxies(self) -> Optional[Dict[str, str]]:
        return {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None
    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = Session(proxies=self.proxies, impersonate=self.config.impersonate, verify=self.config.verify_ssl, timeout=self.config.timeout)
        return self._session
    def request(self, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", self.config.timeout)
        kwargs.setdefault("allow_redirects", self.config.follow_redirects)
        if self.proxies and "proxies" not in kwargs:
            kwargs["proxies"] = self.proxies
        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                return self.session.request(method, url, **kwargs)
            except Exception as exc:
                last_error = exc
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
        raise HTTPClientError(f"request failed: {method} {url}: {last_error}")
    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)
    def post(self, url: str, data: Any = None, json: Any = None, **kwargs):
        return self.request("POST", url, data=data, json=json, **kwargs)
    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None
    def check_ip_location(self) -> Tuple[bool, Optional[str]]:
        try:
            response = self.get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
            loc = None
            for line in response.text.splitlines():
                if line.startswith("loc="):
                    loc = line.split("=", 1)[1].strip()
                    break
            if loc in {"CN", "HK", "MO", "TW"}:
                return False, loc
            return True, loc
        except Exception:
            return False, None
    def check_sentinel(self, did: str) -> Optional[str]:
        body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'
        response = self.post(OPENAI_API_ENDPOINTS["sentinel"], headers={"origin": "https://sentinel.openai.com", "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6", "content-type": "text/plain;charset=UTF-8"}, data=body)
        if response.status_code == 200:
            try:
                return response.json().get("token")
            except Exception:
                return None
        return None
