#!/usr/bin/env python3
"""Standalone OpenAI register + fallback-login script."""
from __future__ import annotations
import argparse, base64, json, secrets, sys, time, urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from curl_cffi import requests as cffi_requests
from config import DEFAULT_PASSWORD_LENGTH, OPENAI_API_ENDPOINTS, OPENAI_PAGE_TYPES, OTP_CODE_PATTERN, PASSWORD_CHARSET, generate_random_user_info
from email_service import TempmailService
from http_client import OpenAIHTTPClient
from oauth import OAuthManager, OAuthStart

@dataclass
class RegistrationResult:
    success: bool
    email: str = ""
    password: str = ""
    account_id: str = ""
    workspace_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    session_token: str = ""
    source: str = "register"
    error_message: str = ""
    logs: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SignupFormResult:
    success: bool
    page_type: str = ""
    is_existing_account: bool = False
    response_data: Dict[str, Any] = field(default_factory=dict)
    error_message: str = ""

class RegistrationEngine:
    def __init__(self, email_service: TempmailService, proxy_url: Optional[str] = None, callback_logger: Optional[Callable[[str], None]] = None):
        self.email_service = email_service
        self.proxy_url = proxy_url
        self.callback_logger = callback_logger or (lambda msg: None)
        self.http_client = OpenAIHTTPClient(proxy_url=proxy_url)
        self.oauth_manager = OAuthManager(proxy_url=proxy_url)
        self.email: Optional[str] = None
        self.password: Optional[str] = None
        self.email_info: Optional[Dict[str, Any]] = None
        self.oauth_start: Optional[OAuthStart] = None
        self.session: Optional[cffi_requests.Session] = None
        self.logs: List[str] = []
        self._otp_sent_at: Optional[float] = None
        self._is_existing_account: bool = False
    def _log(self, message: str):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self.logs.append(line)
        self.callback_logger(line)
    def _generate_password(self, length: int = DEFAULT_PASSWORD_LENGTH) -> str:
        return "".join(secrets.choice(PASSWORD_CHARSET) for _ in range(length))
    def _create_email(self) -> bool:
        self.email_info = self.email_service.create_email()
        self.email = self.email_info["email"]
        self._log(f"created tempmail inbox: {self.email}")
        return True
    def _start_oauth(self) -> bool:
        self.oauth_start = self.oauth_manager.start_oauth()
        self._log(f"generated oauth url: {self.oauth_start.auth_url[:100]}...")
        return True
    def _init_session(self) -> bool:
        self.session = self.http_client.session
        return True
    def _get_device_id(self) -> Optional[str]:
        if not self.oauth_start:
            return None
        for attempt in range(1, 4):
            try:
                response = self.session.get(self.oauth_start.auth_url, timeout=20)
                did = self.session.cookies.get("oai-did")
                if did:
                    self._log(f"device id: {did}")
                    return did
                self._log(f"missing oai-did cookie (http {response.status_code}, attempt {attempt}/3)")
            except Exception as exc:
                self._log(f"device id fetch failed ({attempt}/3): {exc}")
            if attempt < 3:
                time.sleep(attempt)
                self.http_client.close()
                self.session = self.http_client.session
        return None
    def _check_sentinel(self, did: str) -> Optional[str]:
        try:
            token = self.http_client.check_sentinel(did)
            self._log("sentinel token acquired" if token else "sentinel token not returned")
            return token
        except Exception as exc:
            self._log(f"sentinel check failed: {exc}")
            return None
    def _signup_headers(self, did: str, sen_token: Optional[str], referer: str) -> Dict[str, str]:
        headers = {"referer": referer, "accept": "application/json", "content-type": "application/json"}
        if sen_token:
            headers["openai-sentinel-token"] = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'
        return headers
    def _submit_signup_form(self, did: str, sen_token: Optional[str]) -> SignupFormResult:
        body = json.dumps({"username": {"value": self.email, "kind": "email"}, "screen_hint": "signup"})
        response = self.session.post(OPENAI_API_ENDPOINTS["signup"], headers=self._signup_headers(did, sen_token, "https://auth.openai.com/create-account"), data=body)
        self._log(f"signup form status: {response.status_code}")
        if response.status_code != 200:
            return SignupFormResult(False, error_message=f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            data = response.json()
        except Exception:
            return SignupFormResult(True)
        page_type = data.get("page", {}).get("type", "")
        is_existing = page_type == OPENAI_PAGE_TYPES["EMAIL_OTP_VERIFICATION"]
        self._is_existing_account = is_existing
        self._log(f"signup page type: {page_type or '<unknown>'}")
        return SignupFormResult(True, page_type=page_type, is_existing_account=is_existing, response_data=data)
    def _register_password(self) -> Tuple[bool, Optional[str]]:
        self.password = self._generate_password()
        response = self.session.post(OPENAI_API_ENDPOINTS["register"], headers={"referer": "https://auth.openai.com/create-account/password", "accept": "application/json", "content-type": "application/json"}, data=json.dumps({"password": self.password, "username": self.email}))
        self._log(f"password register status: {response.status_code}")
        if response.status_code != 200:
            self._log(f"password register failed: {response.text[:200]}")
            return False, None
        return True, self.password
    def _send_verification_code(self) -> bool:
        self._otp_sent_at = time.time()
        response = self.session.get(OPENAI_API_ENDPOINTS["send_otp"], headers={"referer": "https://auth.openai.com/create-account/password", "accept": "application/json"})
        self._log(f"send otp status: {response.status_code}")
        return response.status_code == 200
    def _get_verification_code(self) -> Optional[str]:
        email_id = self.email_info.get("service_id") if self.email_info else None
        code = self.email_service.get_verification_code(email=self.email, email_id=email_id, timeout=120, pattern=OTP_CODE_PATTERN, otp_sent_at=self._otp_sent_at)
        if code:
            self._log(f"received otp code: {code}")
        return code
    def _validate_verification_code(self, code: str) -> bool:
        response = self.session.post(OPENAI_API_ENDPOINTS["validate_otp"], headers={"referer": "https://auth.openai.com/email-verification", "accept": "application/json", "content-type": "application/json"}, data=json.dumps({"code": code}))
        self._log(f"validate otp status: {response.status_code}")
        return response.status_code == 200
    def _create_user_account(self) -> bool:
        response = self.session.post(OPENAI_API_ENDPOINTS["create_account"], headers={"referer": "https://auth.openai.com/about-you", "accept": "application/json", "content-type": "application/json"}, data=json.dumps(generate_random_user_info()))
        self._log(f"create account status: {response.status_code}")
        return response.status_code == 200
    def _login_with_oauth(self) -> bool:
        if not self.oauth_start:
            return False
        response = self.session.get(self.oauth_start.auth_url, timeout=20, allow_redirects=True)
        self._log(f"oauth login final url: {response.url[:120]}...")
        return "code=" in response.url and "state=" in response.url
    def _fallback_login_for_workspace(self) -> bool:
        old_oauth_start = self.oauth_start
        self._log("workspace missing; starting fallback passwordless login flow")
        try:
            self.oauth_start = self.oauth_manager.start_oauth()
            self.http_client.close()
            self.session = self.http_client.session
            did = self._get_device_id()
            if not did:
                self.oauth_start = old_oauth_start
                return False
            sen_token = self._check_sentinel(did)
            login_body = json.dumps({"username": {"value": self.email, "kind": "email"}, "screen_hint": "login"})
            login_response = self.session.post(OPENAI_API_ENDPOINTS["signup"], headers=self._signup_headers(did, sen_token, "https://auth.openai.com/"), data=login_body)
            self._log(f"fallback login submit status: {login_response.status_code}")
            if login_response.status_code != 200:
                self.oauth_start = old_oauth_start
                return False
            self._otp_sent_at = time.time()
            otp_response = self.session.post(OPENAI_API_ENDPOINTS["passwordless_send_otp"], headers={"referer": "https://auth.openai.com/", "accept": "application/json", "content-type": "application/json"}, data="{}")
            self._log(f"fallback passwordless otp status: {otp_response.status_code}")
            if otp_response.status_code != 200:
                self.oauth_start = old_oauth_start
                return False
            code = self._get_verification_code()
            if not code:
                self.oauth_start = old_oauth_start
                return False
            if not self._validate_verification_code(code):
                self.oauth_start = old_oauth_start
                return False
            self._log("fallback login completed; workspace cookie should now be available")
            return True
        except Exception as exc:
            self._log(f"fallback login failed: {exc}")
            self.oauth_start = old_oauth_start
            return False
    def _get_workspace_id(self) -> Optional[str]:
        auth_cookie = self.session.cookies.get("oai-client-auth-session")
        if not auth_cookie:
            self._log("missing oai-client-auth-session cookie")
            return None
        try:
            payload = auth_cookie.split(".")[0]
            pad = "=" * ((4 - (len(payload) % 4)) % 4)
            auth_json = json.loads(base64.urlsafe_b64decode((payload + pad).encode("ascii")).decode("utf-8"))
            workspaces = auth_json.get("workspaces") or []
            workspace_id = str((workspaces[0] or {}).get("id") or "").strip() if workspaces else ""
            if workspace_id:
                self._log(f"workspace id: {workspace_id}")
                return workspace_id
            self._log("workspace missing in auth cookie")
            return None
        except Exception as exc:
            self._log(f"failed to decode workspace cookie: {exc}")
            return None
    def _select_workspace(self, workspace_id: str) -> Optional[str]:
        response = self.session.post(OPENAI_API_ENDPOINTS["select_workspace"], headers={"referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent", "content-type": "application/json"}, data=json.dumps({"workspace_id": workspace_id}))
        self._log(f"select workspace status: {response.status_code}")
        if response.status_code != 200:
            return None
        return str((response.json() or {}).get("continue_url") or "").strip() or None
    def _follow_redirects(self, start_url: str) -> Optional[str]:
        current_url = start_url
        for _ in range(6):
            response = self.session.get(current_url, allow_redirects=False, timeout=15)
            location = response.headers.get("Location") or ""
            if response.status_code not in [301,302,303,307,308] or not location:
                break
            next_url = urllib.parse.urljoin(current_url, location)
            if "code=" in next_url and "state=" in next_url:
                return next_url
            current_url = next_url
        return None
    def _handle_oauth_callback(self, callback_url: str) -> Optional[Dict[str, Any]]:
        return self.oauth_manager.handle_callback(callback_url=callback_url, expected_state=self.oauth_start.state, code_verifier=self.oauth_start.code_verifier)
    def run(self) -> RegistrationResult:
        result = RegistrationResult(success=False)
        result.logs = self.logs
        try:
            self._log("starting registration flow")
            ip_ok, location = self.http_client.check_ip_location()
            if not ip_ok:
                result.error_message = f"unsupported ip location: {location}"
                return result
            self._log(f"ip location: {location}")
            self._create_email(); result.email = self.email
            self._init_session(); self._start_oauth()
            did = self._get_device_id()
            if not did:
                result.error_message = "failed to get device id"; return result
            sen_token = self._check_sentinel(did)
            signup_result = self._submit_signup_form(did, sen_token)
            if not signup_result.success:
                result.error_message = signup_result.error_message; return result
            if self._is_existing_account:
                self._otp_sent_at = time.time(); self._log("existing account detected; using login path")
            else:
                ok, _ = self._register_password()
                if not ok:
                    result.error_message = "password registration failed"; return result
                if not self._send_verification_code():
                    result.error_message = "failed to send signup otp"; return result
            code = self._get_verification_code()
            if not code:
                result.error_message = "failed to fetch otp"; return result
            if not self._validate_verification_code(code):
                result.error_message = "otp validation failed"; return result
            if not self._is_existing_account and not self._create_user_account():
                result.error_message = "create account failed"; return result
            self._login_with_oauth()
            workspace_id = self._get_workspace_id()
            if not workspace_id:
                if not self._fallback_login_for_workspace():
                    result.error_message = "workspace missing and fallback login failed"; return result
                workspace_id = self._get_workspace_id()
                if not workspace_id:
                    result.error_message = "workspace still missing after fallback login"; return result
            continue_url = self._select_workspace(workspace_id)
            if not continue_url:
                result.error_message = "select workspace failed"; return result
            callback_url = self._follow_redirects(continue_url)
            if not callback_url:
                result.error_message = "failed to obtain oauth callback url"; return result
            token_info = self._handle_oauth_callback(callback_url)
            result.success = True
            result.workspace_id = workspace_id
            result.account_id = token_info.get("account_id", "")
            result.access_token = token_info.get("access_token", "")
            result.refresh_token = token_info.get("refresh_token", "")
            result.id_token = token_info.get("id_token", "")
            result.password = self.password or ""
            result.source = "login" if self._is_existing_account else "register"
            session_cookie = self.session.cookies.get("__Secure-next-auth.session-token")
            if session_cookie:
                result.session_token = session_cookie
            result.metadata = {"email_service": self.email_service.service_type, "proxy_used": self.proxy_url, "registered_at": datetime.now().isoformat(), "is_existing_account": self._is_existing_account}
            return result
        except Exception as exc:
            result.error_message = str(exc)
            self._log(f"unhandled error: {exc}")
            return result
        finally:
            result.logs = self.logs

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone OpenAI register + fallback-login tool")
    parser.add_argument("--email-service", default="tempmail", choices=["tempmail"])
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--output", default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    results: List[Dict[str, Any]] = []
    def printer(msg: str):
        if not args.quiet:
            print(msg, file=sys.stderr)
    for index in range(args.count):
        printer(f"=== account {index + 1}/{args.count} ===")
        engine = RegistrationEngine(TempmailService(proxy_url=args.proxy), proxy_url=args.proxy, callback_logger=printer)
        results.append(asdict(engine.run()))
    payload: Any = results[0] if args.count == 1 else results
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if all(item.get("success") for item in results) else 1

if __name__ == "__main__":
    raise SystemExit(main())
