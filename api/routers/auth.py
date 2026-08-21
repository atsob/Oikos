"""Login / logout / session-check endpoints.

Deliberately NOT gated by require_auth (registered without that dependency in
api/main.py) — you can't require a session to create one — except GET /me,
which is how the frontend checks whether a valid session already exists on
page load (see frontend/src/components/RequireAuth.tsx).
"""
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.deps import SESSION_COOKIE_NAME, SESSION_TTL_DAYS, hash_token, mint_token, require_auth, session_expiry
from database.queries import (
    create_session, create_user, delete_session, delete_user,
    get_user_by_username, list_users, update_password,
)

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


# ── Account management ──────────────────────────────────────────────────────
# No admin/role concept — anyone already logged in can manage accounts,
# matching the single-trusted-household model the rest of the app assumes.

@router.post("/change-password")
def change_password(body: dict, user: dict = Depends(require_auth)):
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    if len(new) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")

    full_user = get_user_by_username(user["username"])
    if not full_user or not bcrypt.checkpw(current.encode(), full_user["password_hash"].encode()):
        raise HTTPException(401, "Current password is incorrect")

    new_hash = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
    update_password(user["users_id"], new_hash)
    return {"ok": True}


@router.get("/users")
def get_users(_user: dict = Depends(require_auth)):
    return list_users()


@router.post("/users")
def add_user(body: dict, _user: dict = Depends(require_auth)):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username:
        raise HTTPException(400, "Username is required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        users_id = create_user(username, password_hash)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"users_id": users_id, "username": username}


@router.delete("/users/{users_id}")
def remove_user(users_id: int, _user: dict = Depends(require_auth)):
    if not delete_user(users_id):
        raise HTTPException(400, "Can't delete the last remaining account")
    return {"ok": True}
