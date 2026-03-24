"""Minimal OAuth PKCE helper for OpenAI auth flow."""
from __future__ import annotations
import base64, hashlib, json, secrets, time, urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Optional
from curl_cffi import requests as cffi_requests
from config import OAUTH_AUTH_URL, OAUTH_CLIENT_ID, OAUTH_REDIRECT_URI, OAUTH_SCOPE, OAUTH_TOKEN_URL

def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())

def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)

def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)

def _parse_callback_url(callback_url: str) -> Dict[str, str]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}
    if "://" not in candidate:
        candidate = f"http://localhost/?{candidate.lstrip('?')}" if "=" in candidate else f"http://{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)
    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values
    def get1(k: str) -> str:
        return (query.get(k, [""])[0] or "").strip()
    return {"code": get1("code"), "state": get1("state"), "error": get1("error"), "error_description": get1("error_description")}

def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}

@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str

class OAuthManager:
    def __init__(self, client_id: str = OAUTH_CLIENT_ID, auth_url: str = OAUTH_AUTH_URL, token_url: str = OAUTH_TOKEN_URL, redirect_uri: str = OAUTH_REDIRECT_URI, scope: str = OAUTH_SCOPE, proxy_url: Optional[str] = None):
        self.client_id = client_id
        self.auth_url = auth_url
        self.token_url = token_url
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.proxy_url = proxy_url
    def start_oauth(self) -> OAuthStart:
        state = _random_state()
        code_verifier = _pkce_verifier()
        code_challenge = _sha256_b64url_no_pad(code_verifier)
        params = {"client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri, "scope": self.scope, "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256", "prompt": "login", "id_token_add_organizations": "true", "codex_cli_simplified_flow": "true"}
        return OAuthStart(auth_url=f"{self.auth_url}?{urllib.parse.urlencode(params)}", state=state, code_verifier=code_verifier, redirect_uri=self.redirect_uri)
    def handle_callback(self, callback_url: str, expected_state: str, code_verifier: str) -> Dict[str, Any]:
        cb = _parse_callback_url(callback_url)
        if cb["error"]:
            raise RuntimeError(f"oauth error: {cb['error']}: {cb['error_description']}")
        if not cb["code"]:
            raise ValueError("callback url missing ?code=")
        if cb["state"] != expected_state:
            raise ValueError("state mismatch")
        proxies = {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None
        response = cffi_requests.post(self.token_url, data={"grant_type": "authorization_code", "client_id": self.client_id, "code": cb["code"], "redirect_uri": self.redirect_uri, "code_verifier": code_verifier}, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}, timeout=30, proxies=proxies, impersonate="chrome")
        response.raise_for_status()
        token_resp = response.json()
        claims = _jwt_claims_no_verify((token_resp.get("id_token") or "").strip())
        auth_claims = claims.get("https://api.openai.com/auth") or {}
        now = int(time.time())
        expires_in = int(token_resp.get("expires_in") or 0)
        return {"id_token": (token_resp.get("id_token") or "").strip(), "access_token": (token_resp.get("access_token") or "").strip(), "refresh_token": (token_resp.get("refresh_token") or "").strip(), "account_id": str(auth_claims.get("chatgpt_account_id") or "").strip(), "email": str(claims.get("email") or "").strip(), "expired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0)))}
