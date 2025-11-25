import uuid
import httpx

from fastapi import APIRouter, Request, Cookie
from fastapi.responses import RedirectResponse, JSONResponse

from config.redis_config import get_redis
from sosial_oauth.application.usecase.google_oauth2_usecase import GoogleOAuth2UseCase
from sosial_oauth.infrastructure.service.google_oauth2_service import GoogleOAuth2Service
from account.application.usecase.account_usecase import AccountUseCase

# Singleton 방식으로 변경
authentication_router = APIRouter()
usecase = GoogleOAuth2UseCase().get_instance()
account_usecase = AccountUseCase().get_instance()
redis_client = get_redis()

@authentication_router.get("/google")
async def redirect_to_google():
    url = usecase.get_authorization_url()
    print("[DEBUG] Redirecting to Google:", url)
    return RedirectResponse(url)

@authentication_router.post("/logout")
async def logout_to_google(request: Request, session_id: str | None = Cookie(None)):
    print("[DEBUG] Logout called")

    print("[DEBUG] Request headers:", request.headers)

    if not session_id:
        print("[DEBUG] No session_id received. Returning logged_in: False")
        response = JSONResponse({"logged_in": False})
        response.delete_cookie(key="session_id")
        return response

    exists = redis_client.exists(session_id)
    print("[DEBUG] Redis has session_id?", exists)

    if exists:
        redis_client.delete(session_id)
        print("[DEBUG] Redis session_id deleted:", redis_client.exists(session_id))

    print("[DEBUG] TEST : ", redis_client.exists(session_id))

    # 쿠키 삭제와 함께 응답 반환
    response = JSONResponse({"logged_out": bool(exists)})
    response.delete_cookie(key="session_id")
    print("[DEBUG] Cookie deleted from response")
    return response

@authentication_router.post("/departure")
async def departure(request: Request, session_id: str | None = Cookie(None)):
    print("[DEBUG] Departure (회원탈퇴) called")
    print("[DEBUG] Request headers:", request.headers)

    if not session_id:
        print("[DEBUG] No session_id received. Returning error")
        response = JSONResponse({"success": False, "message": "No session found"}, status_code=400)
        response.delete_cookie(key="session_id")
        return response

    # Redis 세션 확인
    exists = redis_client.exists(session_id)
    print("[DEBUG] Redis has session_id?", exists)

    if not exists:
        print("[DEBUG] Session not found in Redis")
        response = JSONResponse({"success": False, "message": "Session not found"}, status_code=400)
        response.delete_cookie(key="session_id")
        return response

    # Redis에서 access_token 가져오기
    access_token = redis_client.hget(session_id, "USER_TOKEN")
    print("[DEBUG] Access token from Redis:", access_token)

    # Google token revoke (회원탈퇴 시에만 실행)
    if access_token:
        try:
            if isinstance(access_token, bytes):
                access_token = access_token.decode("utf-8")

            # GUEST 토큰이 아닌 경우에만 revoke 시도
            if access_token != "GUEST":
                GoogleOAuth2Service.revoke_token(access_token)
                print("[DEBUG] Google token revoked successfully")
            else:
                print("[DEBUG] Skipping token revoke for GUEST user")
        except Exception as e:
            # Token revoke 실패해도 계속 진행 (이미 만료됐을 수 있음)
            print(f"[WARNING] Failed to revoke Google token: {str(e)}")

    # session_id로 계정 조회
    account = account_usecase.get_account_by_session_id(session_id)
    print("[DEBUG] Account found:", account)

    if not account:
        print("[DEBUG] Account not found for session_id")
        # 계정이 없어도 세션과 쿠키는 삭제
        redis_client.delete(session_id)
        response = JSONResponse({"success": False, "message": "Account not found"}, status_code=404)
        response.delete_cookie(key="session_id")
        return response

    # 계정 삭제 (향후 table이 account 외에 더 늘어날 경우 이쪽에 테이블 삭제 로직 추가)
    deleted = account_usecase.delete_account_by_oauth_id(account.oauth_type, account.oauth_id)
    print("[DEBUG] Account deleted:", deleted)

    # Redis 세션 삭제
    redis_client.delete(session_id)
    print("[DEBUG] Redis session deleted")

    # 쿠키 삭제와 함께 응답 반환
    response = JSONResponse({"success": True, "message": "Account deleted successfully"})
    response.delete_cookie(key="session_id")
    print("[DEBUG] Cookie deleted from response")
    return response

@authentication_router.get("/google/redirect")
async def process_google_redirect(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None
):
    # Google OAuth 에러 처리 (access_denied 등)
    if error:
        print(f"[DEBUG] Google OAuth error: {error}")
        return RedirectResponse("http://localhost:3000")
    print("[DEBUG] /google/redirect called")

    # session_id 생성
    session_id = str(uuid.uuid4())
    print("[DEBUG] Generated session_id:", session_id)

    # code -> access token
    access_token, session_id = await usecase.login_and_fetch_user(state or "", code, session_id)

    print("[[[[DEBUG]]]] ACCESS_TOKEN ", access_token)
    print(session_id)
    r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": access_token.access_token})
    print(r.status_code, r.text)

    # Redis에 session 저장 (1시간 TTL)
    redis_client.hset(
        session_id,
        "USER_TOKEN",
        access_token.access_token,
    )
    redis_client.expire(session_id, 24 * 60 * 60)
    print("[DEBUG] Session saved in Redis:", redis_client.exists(session_id))

    # 브라우저 쿠키 발급
    response = RedirectResponse("http://localhost:3000")
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=False,
        max_age=3600
    )
    print("[DEBUG] Cookie set in RedirectResponse directly")
    return response


@authentication_router.get("/status")
async def auth_status(request: Request, session_id: str | None = Cookie(None)):
    print("[DEBUG] /status called")

    # 모든 요청 헤더 출력
    print("[DEBUG] Request headers:", request.headers)

    # 쿠키 확인
    print("[DEBUG] Received session_id cookie:", session_id)

    if not session_id:
        print("[DEBUG] No session_id received. Returning logged_in: False")
        return {"logged_in": False}

    exists = redis_client.exists(session_id)
    print("[DEBUG] Redis has session_id?", exists)

    return {"logged_in": bool(exists)}
