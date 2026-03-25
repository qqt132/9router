#!/usr/bin/env python3
"""Standalone OpenAI register + fallback-login script."""
from __future__ import annotations
import argparse, base64, json, re, secrets, sys, time, urllib.parse
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
        self._log(f"临时邮箱已创建: {self.email}")
        return True
    def _start_oauth(self) -> bool:
        self.oauth_start = self.oauth_manager.start_oauth()
        self._log(f"OAuth 授权链接已生成")
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
                    self._log(f"设备 ID 已获取: {did}")
                    return did
                self._log(f"设备 ID 获取失败 (HTTP {response.status_code}, 第 {attempt}/3 次)")
            except Exception as exc:
                self._log(f"设备 ID 请求异常 (第 {attempt}/3 次): {exc}")
            if attempt < 3:
                time.sleep(attempt)
                self.http_client.close()
                self.session = self.http_client.session
        return None
    def _check_sentinel(self, did: str) -> Optional[str]:
        try:
            token = self.http_client.check_sentinel(did)
            self._log("安全令牌已获取" if token else "安全令牌未返回")
            return token
        except Exception as exc:
            self._log(f"安全令牌检查失败: {exc}")
            return None
    def _signup_headers(self, did: str, sen_token: Optional[str], referer: str) -> Dict[str, str]:
        headers = {"referer": referer, "accept": "application/json", "content-type": "application/json"}
        if sen_token:
            headers["openai-sentinel-token"] = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'
        return headers
    def _submit_signup_form(self, did: str, sen_token: Optional[str]) -> SignupFormResult:
        body = json.dumps({"username": {"value": self.email, "kind": "email"}, "screen_hint": "signup"})
        response = self.session.post(OPENAI_API_ENDPOINTS["signup"], headers=self._signup_headers(did, sen_token, "https://auth.openai.com/create-account"), data=body)
        self._log(f"注册表单状态: {response.status_code}")
        if response.status_code != 200:
            return SignupFormResult(False, error_message=f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            data = response.json()
        except Exception:
            return SignupFormResult(True)
        page_type = data.get("page", {}).get("type", "")
        is_existing = page_type == OPENAI_PAGE_TYPES["EMAIL_OTP_VERIFICATION"]
        self._is_existing_account = is_existing
        self._log(f"注册页面类型: {page_type or '<未知>'}")
        return SignupFormResult(True, page_type=page_type, is_existing_account=is_existing, response_data=data)
    def _register_password(self) -> Tuple[bool, Optional[str]]:
        self.password = self._generate_password()
        response = self.session.post(OPENAI_API_ENDPOINTS["register"], headers={"referer": "https://auth.openai.com/create-account/password", "accept": "application/json", "content-type": "application/json"}, data=json.dumps({"password": self.password, "username": self.email}))
        self._log(f"密码注册状态: {response.status_code}")
        if response.status_code != 200:
            self._log(f"密码注册失败: {response.text[:200]}")
            return False, None
        return True, self.password
    def _send_verification_code(self) -> bool:
        self._otp_sent_at = time.time()
        response = self.session.get(OPENAI_API_ENDPOINTS["send_otp"], headers={"referer": "https://auth.openai.com/create-account/password", "accept": "application/json"})
        self._log(f"验证码发送状态: {response.status_code}")
        return response.status_code == 200
    def _get_verification_code(self) -> Optional[str]:
        email_id = self.email_info.get("service_id") if self.email_info else None
        code = self.email_service.get_verification_code(email=self.email, email_id=email_id, timeout=120, pattern=OTP_CODE_PATTERN, otp_sent_at=self._otp_sent_at)
        if code:
            self._log(f"验证码已收到: {code}")
        return code
    def _validate_verification_code(self, code: str) -> bool:
        response = self.session.post(OPENAI_API_ENDPOINTS["validate_otp"], headers={"referer": "https://auth.openai.com/email-verification", "accept": "application/json", "content-type": "application/json"}, data=json.dumps({"code": code}))
        self._log(f"验证码校验状态: {response.status_code}")
        return response.status_code == 200
    def _create_user_account(self) -> bool:
        response = self.session.post(OPENAI_API_ENDPOINTS["create_account"], headers={"referer": "https://auth.openai.com/about-you", "accept": "application/json", "content-type": "application/json"}, data=json.dumps(generate_random_user_info()))
        self._log(f"账号创建状态: {response.status_code}")
        return response.status_code == 200
    def _login_with_oauth(self) -> bool:
        if not self.oauth_start:
            return False
        response = self.session.get(self.oauth_start.auth_url, timeout=20, allow_redirects=True)
        self._log(f"OAuth 登录跳转完成")
        return "code=" in response.url and "state=" in response.url
    def _fallback_login_for_workspace(self) -> bool:
        old_oauth_start = self.oauth_start
        self._log("Workspace 未找到，启动备用无密码登录流程")
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
            self._log(f"备用登录提交状态: {login_response.status_code}")
            if login_response.status_code != 200:
                self.oauth_start = old_oauth_start
                return False
            self._otp_sent_at = time.time()
            otp_response = self.session.post(OPENAI_API_ENDPOINTS["passwordless_send_otp"], headers={"referer": "https://auth.openai.com/", "accept": "application/json", "content-type": "application/json"}, data="{}")
            self._log(f"备用验证码发送状态: {otp_response.status_code}")
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
            self._log("备用登录完成，Workspace Cookie 已就绪")
            return True
        except Exception as exc:
            self._log(f"备用登录失败: {exc}")
            self.oauth_start = old_oauth_start
            return False

    # ========== 多方案 Workspace 提取方法（来自 Codex Manager）==========
    
    def _decode_cookie_json_candidates(self, cookie_value: str) -> List[Dict[str, Any]]:
        """尝试从完整 Cookie 或其分段中解码出 JSON（方法 1：Cookie 解析）"""
        decoded_objects = []
        candidates = [cookie_value]
        if "." in cookie_value:
            candidates.extend(cookie_value.split("."))
        for candidate in candidates:
            raw = (candidate or "").strip()
            if not raw:
                continue
            pad = "=" * ((4 - (len(raw) % 4)) % 4)
            try:
                decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
            except Exception:
                continue
            try:
                payload = json.loads(decoded.decode("utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                decoded_objects.append(payload)
        return decoded_objects

    def _extract_workspace_id_from_auth_json(self, auth_json: Dict[str, Any]) -> Optional[str]:
        """从解码后的授权 JSON 中提取 Workspace ID"""
        workspaces = auth_json.get("workspaces") or []
        if isinstance(workspaces, list):
            for workspace in workspaces:
                if not isinstance(workspace, dict):
                    continue
                workspace_id = str(workspace.get("id") or "").strip()
                if workspace_id:
                    return workspace_id
        for key in ("workspace_id", "workspaceId", "default_workspace_id", "defaultWorkspaceId", "active_workspace_id", "activeWorkspaceId"):
            workspace_id = str(auth_json.get(key) or "").strip()
            if workspace_id:
                return workspace_id
        for key in ("workspace", "default_workspace", "active_workspace", "defaultWorkspace", "activeWorkspace"):
            workspace = auth_json.get(key)
            if not isinstance(workspace, dict):
                continue
            workspace_id = str(workspace.get("id") or "").strip()
            if workspace_id:
                return workspace_id
        return None

    def _extract_workspace_id_from_cookie(self, cookie_value: str) -> Optional[str]:
        """方法 1：从授权 Cookie 中提取 Workspace ID（优先级最高）"""
        for auth_json in self._decode_cookie_json_candidates(cookie_value):
            workspace_id = self._extract_workspace_id_from_auth_json(auth_json)
            if workspace_id:
                self._log(f"[方法1-Cookie] 成功提取 Workspace ID: {workspace_id}")
                return workspace_id
        return None

    def _extract_workspace_id_from_html(self, html: str) -> Optional[str]:
        """方法 2：从 HTML 隐藏表单字段中提取 Workspace ID"""
        if not html:
            return None
        patterns = [
            r'name="workspace_id"[^>]*value="([^"]+)"',
            r"name='workspace_id'[^>]*value='([^']+)'",
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                workspace_id = str(match.group(1) or "").strip()
                if workspace_id:
                    self._log(f"[方法2-HTML] 成功提取 Workspace ID: {workspace_id}")
                    return workspace_id
        return None

    def _extract_workspace_id_from_json(self, payload: Any, depth: int = 0) -> Optional[str]:
        """方法 3：从 JSON 响应中递归提取 Workspace ID"""
        if payload is None or depth > 5:
            return None
        if isinstance(payload, dict):
            workspace_id = self._extract_workspace_id_from_auth_json(payload)
            if workspace_id:
                self._log(f"[方法3-JSON] 成功提取 Workspace ID: {workspace_id}")
                return workspace_id
            for value in payload.values():
                workspace_id = self._extract_workspace_id_from_json(value, depth + 1)
                if workspace_id:
                    return workspace_id
            return None
        if isinstance(payload, list):
            for item in payload:
                workspace_id = self._extract_workspace_id_from_json(item, depth + 1)
                if workspace_id:
                    return workspace_id
        return None

    def _extract_workspace_id_from_url(self, url: str) -> Optional[str]:
        """方法 4：从 URL 查询参数或片段中提取 Workspace ID"""
        if not url:
            return None
        parsed = urllib.parse.urlparse(url)
        for raw_query in (parsed.query, parsed.fragment):
            query = urllib.parse.parse_qs(raw_query)
            for key in ("workspace_id", "workspaceId", "default_workspace_id", "active_workspace_id"):
                values = query.get(key) or []
                if values:
                    workspace_id = str(values[0] or "").strip()
                    if workspace_id:
                        self._log(f"[方法4-URL] 成功提取 Workspace ID: {workspace_id}")
                        return workspace_id
        return None

    def _extract_workspace_id_from_text(self, text: str) -> Optional[str]:
        """方法 5：从 HTML/脚本文本中通过正则提取 Workspace ID"""
        if not text:
            return None
        patterns = [
            r'"workspace_id"\s*:\s*"([^"]+)"',
            r'"workspaceId"\s*:\s*"([^"]+)"',
            r'"default_workspace_id"\s*:\s*"([^"]+)"',
            r'"defaultWorkspaceId"\s*:\s*"([^"]+)"',
            r'"active_workspace_id"\s*:\s*"([^"]+)"',
            r'"activeWorkspaceId"\s*:\s*"([^"]+)"',
            r'"workspace"\s*:\s*\{[^{}]*"id"\s*:\s*"([^"]+)"',
            r'"default_workspace"\s*:\s*\{[^{}]*"id"\s*:\s*"([^"]+)"',
            r'"active_workspace"\s*:\s*\{[^{}]*"id"\s*:\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                workspace_id = str(match.group(1) or "").strip()
                if workspace_id:
                    self._log(f"[方法5-文本] 成功提取 Workspace ID: {workspace_id}")
                    return workspace_id
        return None

    def _get_workspace_id(self) -> Optional[str]:
        """
        多方案提取 Workspace ID（按优先级依次尝试）
        优先级：Cookie → HTML → JSON → URL → 文本 → 备用登录
        """
        self._log("开始多方案提取 Workspace ID")
        
        # 方法 1：从 Cookie 提取（优先级最高）
        cookie_names = ("oai-client-auth-session", "oai_client_auth_session", "oai-client-auth-info", "oai_client_auth_info")
        for cookie_name in cookie_names:
            auth_cookie = self.session.cookies.get(cookie_name)
            if auth_cookie:
                self._log(f"尝试从 Cookie '{cookie_name}' 提取")
                workspace_id = self._extract_workspace_id_from_cookie(auth_cookie)
                if workspace_id:
                    return workspace_id
        
        self._log("Cookie 方法未找到 Workspace ID，尝试其他方法")
        
        # 方法 2-5：从最近的响应中提取
        # 尝试重新请求授权页面获取更多信息
        try:
            if self.oauth_start:
                self._log("请求授权页面以获取更多 Workspace 信息")
                response = self.session.get(self.oauth_start.auth_url, timeout=15)
                html = response.text or ""
                current_url = str(getattr(response, "url", "") or "")
                
                # 方法 2：HTML 表单字段
                workspace_id = self._extract_workspace_id_from_html(html)
                if workspace_id:
                    return workspace_id
                
                # 方法 3：JSON 响应
                try:
                    json_data = response.json()
                    workspace_id = self._extract_workspace_id_from_json(json_data)
                    if workspace_id:
                        return workspace_id
                except Exception:
                    pass
                
                # 方法 4：URL 参数
                workspace_id = self._extract_workspace_id_from_url(current_url)
                if workspace_id:
                    return workspace_id
                
                # 方法 5：文本正则
                workspace_id = self._extract_workspace_id_from_text(html)
                if workspace_id:
                    return workspace_id
        except Exception as exc:
            self._log(f"请求授权页面失败: {exc}")
        
        # 所有方法都失败，返回 None（将触发备用登录）
        self._log("所有 Workspace 提取方法均失败")
        return None

    # ========== 原有方法保持不变 ==========
    
    def _select_workspace(self, workspace_id: str) -> Optional[str]:
        response = self.session.post(OPENAI_API_ENDPOINTS["select_workspace"], headers={"referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent", "content-type": "application/json"}, data=json.dumps({"workspace_id": workspace_id}))
        self._log(f"Workspace 选择状态: {response.status_code}")
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
            self._log("开始注册流程")
            ip_ok, location = self.http_client.check_ip_location()
            if not ip_ok:
                result.error_message = f"不支持的 IP 地区: {location}"
                return result
            self._log(f"IP 地区: {location}")
            self._create_email(); result.email = self.email
            self._init_session(); self._start_oauth()
            did = self._get_device_id()
            if not did:
                result.error_message = "获取设备 ID 失败"; return result
            sen_token = self._check_sentinel(did)
            signup_result = self._submit_signup_form(did, sen_token)
            if not signup_result.success:
                result.error_message = signup_result.error_message; return result
            if self._is_existing_account:
                self._otp_sent_at = time.time(); self._log("检测到已有账号，使用登录流程")
            else:
                ok, _ = self._register_password()
                if not ok:
                    result.error_message = "密码注册失败"; return result
                if not self._send_verification_code():
                    result.error_message = "验证码发送失败"; return result
            code = self._get_verification_code()
            if not code:
                result.error_message = "获取验证码失败"; return result
            if not self._validate_verification_code(code):
                result.error_message = "验证码校验失败"; return result
            if not self._is_existing_account and not self._create_user_account():
                result.error_message = "账号创建失败"; return result
            self._login_with_oauth()
            workspace_id = self._get_workspace_id()
            if not workspace_id:
                if not self._fallback_login_for_workspace():
                    result.error_message = "Workspace 获取失败，备用登录也失败"; return result
                workspace_id = self._get_workspace_id()
                if not workspace_id:
                    result.error_message = "备用登录后 Workspace 仍未获取"; return result
            continue_url = self._select_workspace(workspace_id)
            if not continue_url:
                result.error_message = "Workspace 选择失败"; return result
            callback_url = self._follow_redirects(continue_url)
            if not callback_url:
                result.error_message = "OAuth 回调链接获取失败"; return result
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
            self._log(f"未处理异常: {exc}")
            return result
        finally:
            result.logs = self.logs

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone OpenAI register + fallback-login tool")
    parser.add_argument("--email-service", default="tempmail", choices=["tempmail"])
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--count", type=int, default=5, help="注册账号数量（默认 5）")
    parser.add_argument("--interval", type=int, default=3, help="账号间隔秒数（默认 3）")
    parser.add_argument("--output", default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    results: List[Dict[str, Any]] = []
    success_count = 0
    failure_count = 0
    
    def printer(msg: str):
        if not args.quiet:
            print(msg, file=sys.stderr)
    
    printer(f"\n{'='*60}")
    printer(f"开始批量注册 OpenAI 账号")
    printer(f"总数量: {args.count} | 间隔: {args.interval}秒")
    printer(f"{'='*60}\n")
    
    for index in range(args.count):
        printer(f"\n{'─'*60}")
        printer(f"[{index + 1}/{args.count}] 正在注册账号...")
        printer(f"{'─'*60}")
        
        try:
            engine = RegistrationEngine(
                TempmailService(proxy_url=args.proxy), 
                proxy_url=args.proxy, 
                callback_logger=printer
            )
            result = engine.run()
            result_dict = asdict(result)
            results.append(result_dict)
            
            if result.success:
                success_count += 1
                printer(f"\n✅ [{index + 1}/{args.count}] 注册成功")
                printer(f"   邮箱: {result.email}")
                printer(f"   密码: {result.password}")
                printer(f"   账号ID: {result.account_id}")
            else:
                failure_count += 1
                printer(f"\n❌ [{index + 1}/{args.count}] 注册失败")
                printer(f"   错误: {result.error_message}")
        
        except Exception as exc:
            # 捕获任何未预期的异常，确保循环继续
            failure_count += 1
            error_msg = f"未捕获的异常: {type(exc).__name__}: {str(exc)}"
            printer(f"\n❌ [{index + 1}/{args.count}] 注册过程异常")
            printer(f"   错误: {error_msg}")
            
            # 记录失败结果
            failed_result = {
                "success": False,
                "email": "",
                "password": "",
                "account_id": "",
                "workspace_id": "",
                "access_token": "",
                "refresh_token": "",
                "id_token": "",
                "session_token": "",
                "source": "register",
                "error_message": error_msg,
                "logs": [f"[异常] {error_msg}"],
                "metadata": {"exception_type": type(exc).__name__}
            }
            results.append(failed_result)
        
        # 无论成功或失败，都等待间隔时间（除非是最后一个）
        if index < args.count - 1:
            printer(f"\n⏳ 等待 {args.interval} 秒后继续...")
            time.sleep(args.interval)
    
    # 输出汇总统计
    printer(f"\n{'='*60}")
    printer(f"批量注册完成")
    printer(f"{'='*60}")
    printer(f"✅ 成功: {success_count}/{args.count}")
    printer(f"❌ 失败: {failure_count}/{args.count}")
    printer(f"{'='*60}\n")
    
    # 输出结果到标准输出
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    
    output_data = {
        "timestamp": timestamp,
        "total": args.count,
        "success": success_count,
        "failure": failure_count,
        "accounts": results
    }
    
    rendered = json.dumps(output_data, ensure_ascii=False, indent=2)
    print(rendered)
    
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
