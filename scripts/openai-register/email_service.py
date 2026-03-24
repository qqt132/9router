"""Simplified tempmail-only email service."""
from __future__ import annotations
import re, time
from typing import Any, Dict, Optional
import requests
from config import OTP_CODE_PATTERN, TEMPMAIL_BASE_URL

class EmailServiceError(Exception):
    pass

class TempmailService:
    service_type = "tempmail"
    def __init__(self, base_url: str = TEMPMAIL_BASE_URL, proxy_url: Optional[str] = None, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        self._cache: Dict[str, Dict[str, Any]] = {}
    def create_email(self) -> Dict[str, Any]:
        response = self.session.post(f"{self.base_url}/inbox/create", headers={"Accept": "application/json", "Content-Type": "application/json"}, json={}, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        email = str(data.get("address") or "").strip()
        token = str(data.get("token") or "").strip()
        if not email or not token:
            raise EmailServiceError(f"invalid tempmail response: {data}")
        info = {"email": email, "service_id": token, "token": token, "created_at": time.time()}
        self._cache[email] = info
        return info
    def get_verification_code(self, email: str, email_id: Optional[str] = None, timeout: int = 120, pattern: str = OTP_CODE_PATTERN, otp_sent_at: Optional[float] = None) -> Optional[str]:
        token = email_id or self._cache.get(email, {}).get("token")
        if not token:
            raise EmailServiceError(f"missing tempmail token for {email}")
        start = time.time()
        seen = set()
        while time.time() - start < timeout:
            try:
                response = self.session.get(f"{self.base_url}/inbox", params={"token": token}, headers={"Accept": "application/json"}, timeout=self.timeout)
                if response.status_code == 200:
                    data = response.json() or {}
                    for msg in data.get("emails", []) or []:
                        uid = msg.get("date") or msg.get("id")
                        if uid in seen:
                            continue
                        seen.add(uid)
                        content = "\n".join([str(msg.get("from") or ""), str(msg.get("subject") or ""), str(msg.get("body") or ""), str(msg.get("html") or "")])
                        if "openai" not in content.lower():
                            continue
                        match = re.search(pattern, content)
                        if match:
                            return match.group(1)
            except Exception:
                pass
            time.sleep(3)
        return None
