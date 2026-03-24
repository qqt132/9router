"""Standalone OpenAI registration constants and helpers."""
from __future__ import annotations
import random
from datetime import datetime

OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_SCOPE = "openid email profile offline_access"

OPENAI_API_ENDPOINTS = {
    "sentinel": "https://sentinel.openai.com/backend-api/sentinel/req",
    "signup": "https://auth.openai.com/api/accounts/authorize/continue",
    "register": "https://auth.openai.com/api/accounts/user/register",
    "send_otp": "https://auth.openai.com/api/accounts/email-otp/send",
    "passwordless_send_otp": "https://auth.openai.com/api/accounts/passwordless/send-otp",
    "validate_otp": "https://auth.openai.com/api/accounts/email-otp/validate",
    "create_account": "https://auth.openai.com/api/accounts/create_account",
    "select_workspace": "https://auth.openai.com/api/accounts/workspace/select",
}

OPENAI_PAGE_TYPES = {
    "EMAIL_OTP_VERIFICATION": "email_otp_verification",
    "PASSWORD_REGISTRATION": "password",
}

TEMPMAIL_BASE_URL = "https://api.tempmail.lol/v2"
OTP_CODE_PATTERN = r"(?<!\d)(\d{6})(?!\d)"
PASSWORD_CHARSET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
DEFAULT_PASSWORD_LENGTH = 12
FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles","Emma","Olivia","Ava","Isabella","Sophia","Mia","Charlotte","Amelia","Harper","Evelyn","Alex","Jordan","Taylor","Morgan","Casey","Riley","Jamie","Avery","Quinn","Skyler","Liam","Noah","Ethan","Lucas","Mason","Oliver","Elijah","Aiden","Henry","Sebastian","Grace","Lily","Chloe","Zoey","Nora","Aria","Hazel","Aurora","Stella","Ivy"]

def generate_random_user_info() -> dict:
    name = random.choice(FIRST_NAMES)
    current_year = datetime.now().year
    birth_year = random.randint(current_year - 45, current_year - 18)
    birth_month = random.randint(1, 12)
    if birth_month in [1,3,5,7,8,10,12]:
        birth_day = random.randint(1, 31)
    elif birth_month in [4,6,9,11]:
        birth_day = random.randint(1, 30)
    else:
        birth_day = random.randint(1, 28)
    return {"name": name, "birthdate": f"{birth_year}-{birth_month:02d}-{birth_day:02d}"}
