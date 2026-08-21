"""Login / logout / session-check endpoints.

Deliberately NOT gated by require_auth (registered without that dependency in
api/main.py) — you can't require a session to create one — except GET /me,
which is how the frontend checks whether a valid session already exists on
page load (see frontend/src/components/RequireAuth.tsx).
"""
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.deps import SESSION_COOKIE_NAME, SESSION_TTL_DAYS, hash_token, mint_token, require_auth, session_expiry
from database.queries import create_session, delete_session, get_user_by_username

router = APIRouter()


@router.post("/login")
def login(body: dict, response: Response):
    username = str(body.get("username") or "")
    password = str(body.get("password") or "")

    user = get_user_by_username(username)
    # Generic message either way — don't reveal whether the username itself was valid.
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Invalid username or password")

    token = mint_token()
    create_session(user["users_id"], hash_token(token), session_expiry())
    response.set_cookie(
        SESSION_COOKIE_NAME, token,
        httponly=True, secure=True, samesite="lax", path="/",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
    )
    return {"username": user["username"]}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(hash_token(token))  # server-side revocation, not just clearing the cookie
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(require_auth)):
    return {"username": user["username"]}
