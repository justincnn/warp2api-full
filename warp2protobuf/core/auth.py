#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JWT Authentication for Warp API

Handles JWT token management, refresh, and validation.
Integrates functionality from refresh_jwt.py.
"""
import base64
import json
import os
import time
from pathlib import Path
import httpx
import asyncio
from dotenv import load_dotenv, set_key

from ..config.settings import REFRESH_TOKEN_B64, REFRESH_URL, CLIENT_VERSION, OS_CATEGORY, OS_NAME, OS_VERSION
from .logging import logger, log


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload to check expiration"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes.decode('utf-8'))
        return payload
    except Exception as e:
        logger.debug(f"Error decoding JWT: {e}")
        return {}


def is_token_expired(token: str, buffer_minutes: int = 5) -> bool:
    payload = decode_jwt_payload(token)
    if not payload or 'exp' not in payload:
        return True
    expiry_time = payload['exp']
    current_time = time.time()
    buffer_time = buffer_minutes * 60
    return (expiry_time - current_time) <= buffer_time


async def refresh_jwt_token() -> dict:
    """Refresh the JWT token using the refresh token.

    Prefers environment variable WARP_REFRESH_TOKEN when present; otherwise
    falls back to the baked-in REFRESH_TOKEN_B64 payload.
    """
    logger.info("Refreshing JWT token...")
    # Prefer dynamic refresh token from environment if present
    env_refresh = os.getenv("WARP_REFRESH_TOKEN")
    if env_refresh:
        payload = f"grant_type=refresh_token&refresh_token={env_refresh}".encode("utf-8")
    else:
        payload = base64.b64decode(REFRESH_TOKEN_B64)
    headers = {
        "x-warp-client-version": CLIENT_VERSION,
        "x-warp-os-category": OS_CATEGORY,
        "x-warp-os-name": OS_NAME,
        "x-warp-os-version": OS_VERSION,
        "content-type": "application/x-www-form-urlencoded",
        "accept": "*/*",
        "accept-encoding": "gzip, br",
        "content-length": str(len(payload))
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                REFRESH_URL,
                headers=headers,
                content=payload
            )
            if response.status_code == 200:
                token_data = response.json()
                logger.info("Token refresh successful")
                return token_data
            else:
                logger.error(f"Token refresh failed: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return {}
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        return {}


def update_env_file(new_jwt: str) -> bool:
    env_path = Path(".env")
    try:
        set_key(str(env_path), "WARP_JWT", new_jwt)
        logger.info("Updated .env file with new JWT token")
        return True
    except Exception as e:
        logger.error(f"Error updating .env file: {e}")
        return False


def update_env_refresh_token(refresh_token: str) -> bool:
    env_path = Path(".env")
    try:
        set_key(str(env_path), "WARP_REFRESH_TOKEN", refresh_token)
        logger.info("Updated .env with WARP_REFRESH_TOKEN")
        return True
    except Exception as e:
        logger.error(f"Error updating .env WARP_REFRESH_TOKEN: {e}")
        return False


async def check_and_refresh_token() -> bool:
    current_jwt = os.getenv("WARP_JWT")
    if not current_jwt:
        logger.warning("No JWT token found in environment")
        token_data = await refresh_jwt_token()
        if token_data and "access_token" in token_data:
            return update_env_file(token_data["access_token"])
        return False
    logger.debug("Checking current JWT token expiration...")
    if is_token_expired(current_jwt, buffer_minutes=15):
        logger.info("JWT token is expired or expiring soon, refreshing...")
        token_data = await refresh_jwt_token()
        if token_data and "access_token" in token_data:
            new_jwt = token_data["access_token"]
            if not is_token_expired(new_jwt, buffer_minutes=0):
                logger.info("New token is valid")
                return update_env_file(new_jwt)
            else:
                logger.warning("New token appears to be invalid or expired")
                return False
        else:
            logger.error("Failed to get new token from refresh")
            return False
    else:
        payload = decode_jwt_payload(current_jwt)
        if payload and 'exp' in payload:
            expiry_time = payload['exp']
            time_left = expiry_time - time.time()
            hours_left = time_left / 3600
            logger.debug(f"Current token is still valid ({hours_left:.1f} hours remaining)")
        else:
            logger.debug("Current token appears valid")
        return True


async def get_valid_jwt() -> str:
    """仅通过账号池获取有效的 JWT，失败即报错；不再回退多账号/本地/匿名路径。

    要求设置 POOL_SERVICE_BASE_URL 或 POOL_SERVICE_URL。
    令牌的释放应由上层请求生命周期在完成后调用账号池 release 接口处理。
    """
    pool_base = os.getenv("POOL_SERVICE_BASE_URL") or os.getenv("POOL_SERVICE_URL")
    if not pool_base:
        raise RuntimeError("账号池未配置：请设置 POOL_SERVICE_BASE_URL 或 POOL_SERVICE_URL")

    try:
        from .pool_auth import get_pool_manager
        manager = get_pool_manager()
        session = await manager.acquire_session()
        if not session or not session.get("access_token"):
            raise RuntimeError("从账号池获取会话失败或未返回 access_token")
        # 兼容旧逻辑：pool_auth.acquire_session 内部已调用 update_env_file
        return session["access_token"]
    except Exception as e:
        logger.error(f"通过账号池获取 JWT 失败: {e}")
        raise


def get_jwt_token() -> str:
    from dotenv import load_dotenv as _load
    _load()
    return os.getenv("WARP_JWT", "")


async def refresh_jwt_if_needed() -> bool:
    try:
        return await check_and_refresh_token()
    except Exception as e:
        logger.error(f"JWT refresh failed: {e}")
        return False


# ============ Anonymous token acquisition (quota refresh) ============

_ANON_GQL_URL = "https://app.warp.dev/graphql/v2?op=CreateAnonymousUser"
_IDENTITY_TOOLKIT_BASE = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"


def _extract_google_api_key_from_refresh_url() -> str:
    try:
        # REFRESH_URL like: https://app.warp.dev/proxy/token?key=API_KEY
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(REFRESH_URL)
        qs = parse_qs(parsed.query)
        key = qs.get("key", [""])[0]
        return key
    except Exception:
        return ""


async def _create_anonymous_user() -> dict:
    headers = {
        "accept-encoding": "gzip, br",
        "content-type": "application/json",
        "x-warp-client-version": CLIENT_VERSION,
        "x-warp-os-category": OS_CATEGORY,
        "x-warp-os-name": OS_NAME,
        "x-warp-os-version": OS_VERSION,
    }
    # GraphQL payload per anonymous.MD
    query = (
        "mutation CreateAnonymousUser($input: CreateAnonymousUserInput!, $requestContext: RequestContext!) {\n"
        "  createAnonymousUser(input: $input, requestContext: $requestContext) {\n"
        "    __typename\n"
        "    ... on CreateAnonymousUserOutput {\n"
        "      expiresAt\n"
        "      anonymousUserType\n"
        "      firebaseUid\n"
        "      idToken\n"
        "      isInviteValid\n"
        "      responseContext { serverVersion }\n"
        "    }\n"
        "    ... on UserFacingError {\n"
        "      error { __typename message }\n"
        "      responseContext { serverVersion }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    variables = {
        "input": {
            "anonymousUserType": "NATIVE_CLIENT_ANONYMOUS_USER_FEATURE_GATED",
            "expirationType": "NO_EXPIRATION",
            "referralCode": None
        },
        "requestContext": {
            "clientContext": {"version": CLIENT_VERSION},
            "osContext": {
                "category": OS_CATEGORY,
                "linuxKernelVersion": None,
                "name": OS_NAME,
                "version": OS_VERSION,
            }
        }
    }
    body = {"query": query, "variables": variables, "operationName": "CreateAnonymousUser"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(_ANON_GQL_URL, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"CreateAnonymousUser failed: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        return data


async def _exchange_id_token_for_refresh_token(id_token: str) -> dict:
    key = _extract_google_api_key_from_refresh_url()
    url = f"{_IDENTITY_TOOLKIT_BASE}?key={key}" if key else f"{_IDENTITY_TOOLKIT_BASE}?key=AIzaSyBdy3O3S9hrdayLJxJ7mriBR4qgUaUygAs"
    headers = {
        "accept-encoding": "gzip, br",
        "content-type": "application/x-www-form-urlencoded",
        "x-warp-client-version": CLIENT_VERSION,
        "x-warp-os-category": OS_CATEGORY,
        "x-warp-os-name": OS_NAME,
        "x-warp-os-version": OS_VERSION,
    }
    form = {
        "returnSecureToken": "true",
        "token": id_token,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(url, headers=headers, data=form)
        if resp.status_code != 200:
            raise RuntimeError(f"signInWithCustomToken failed: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()


async def acquire_anonymous_access_token() -> str:
    """Acquire a new anonymous access token (quota refresh) and persist to .env.

    优先使用 Token 池，然后是 Cloudflare Worker 动态部署方案，最后回退到直接请求。

    Returns the new access token string. Raises on failure.
    """
    # 优先尝试使用 Token 池
    try:
        # 导入 Token 池管理器
        import sys
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent
        sys.path.insert(0, str(project_root))
        from warp_token_pool import get_pooled_token

        logger.info("尝试从 Token 池获取 token...")
        token = await get_pooled_token()
        logger.info("成功从 Token 池获取 token")
        update_env_file(token)
        return token

    except Exception as e:
        logger.warning(f"Token 池获取失败: {e}，尝试多账号服务")

        # 回退到多账号服务
        try:
            logger.info("尝试使用多账号服务获取 token...")

            # 导入多账号服务
            from warp_token_manager import MultiAccountTokenService

            # MultiAccountTokenService 会自动从环境变量加载配置
            multi_service = MultiAccountTokenService()
            access_token = await multi_service.acquire_fresh_token()

            if access_token:
                logger.info("通过多账号服务成功获取 token")
                update_env_file(access_token)
                return access_token
            else:
                logger.warning("多账号服务获取失败，尝试单账号服务")

        except Exception as e2:
            logger.warning(f"多账号服务失败: {e2}，尝试单账号服务")

            # 回退到单账号服务（向后兼容）
            cf_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
            cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")

            if cf_api_token and cf_account_id:
                try:
                    logger.info("尝试使用单账号 Cloudflare Worker 获取 token...")

                    from warp_token_manager import WarpTokenService

                    service = WarpTokenService(cf_api_token, cf_account_id)
                    access_token = await service.acquire_fresh_token()

                    if access_token:
                        logger.info("通过单账号 Cloudflare Worker 成功获取 token")
                        update_env_file(access_token)
                        return access_token
                    else:
                        logger.warning("单账号 Cloudflare Worker 方案失败，回退到直接请求")

                except Exception as e3:
                    logger.warning(f"单账号 Cloudflare Worker 方案失败: {e3}，回退到直接请求")

    # 回退到原始的直接请求方案
    logger.info("使用直接请求方案获取匿名访问令牌...")

    try:
        data = await _create_anonymous_user()
        id_token = None
        try:
            id_token = data["data"]["createAnonymousUser"].get("idToken")
        except Exception:
            pass
        if not id_token:
            raise RuntimeError(f"CreateAnonymousUser did not return idToken: {data}")

        signin = await _exchange_id_token_for_refresh_token(id_token)
        refresh_token = signin.get("refreshToken")
        if not refresh_token:
            raise RuntimeError(f"signInWithCustomToken did not return refreshToken: {signin}")

        # Persist refresh token for future time-based refreshes
        update_env_refresh_token(refresh_token)

        # Now call Warp proxy token endpoint to get access_token using this refresh token
        payload = f"grant_type=refresh_token&refresh_token={refresh_token}".encode("utf-8")
        headers = {
            "x-warp-client-version": CLIENT_VERSION,
            "x-warp-os-category": OS_CATEGORY,
            "x-warp-os-name": OS_NAME,
            "x-warp-os-version": OS_VERSION,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "*/*",
            "accept-encoding": "gzip, br",
            "content-length": str(len(payload))
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(REFRESH_URL, headers=headers, content=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Acquire access_token failed: HTTP {resp.status_code} {resp.text[:200]}")
            token_data = resp.json()
            access = token_data.get("access_token")
            if not access:
                raise RuntimeError(f"No access_token in response: {token_data}")
            update_env_file(access)
            return access

    except Exception as e:
        logger.error(f"直接请求方案也失败: {e}")
        raise RuntimeError(f"所有获取 token 的方案都失败了: {e}")


def print_token_info():
    current_jwt = os.getenv("WARP_JWT")
    if not current_jwt:
        logger.info("No JWT token found")
        return
    payload = decode_jwt_payload(current_jwt)
    if not payload:
        logger.info("Cannot decode JWT token")
        return
    logger.info("=== JWT Token Information ===")
    if 'email' in payload:
        logger.info(f"Email: {payload['email']}")
    if 'user_id' in payload:
        logger.info(f"User ID: {payload['user_id']}") 