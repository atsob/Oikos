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
    ROLES, clear_login_failures, create_session, create_user, delete_session, delete_user,
    get_user_by_username, list_users, login_failure_counts, record_login_failure,
    set_user_access, update_password,
)

router = APIRouter()

# Login lockout: with Oikos reachable from the internet, password guessing has to be slowed to a crawl. Failures are kept in the database
# (Login_Failures), not in memory, so the limit holds across the uvicorn workers and restarts.
LOCKOUT_MINUTES = 15
MAX_FAILURES_PER_USER = 5      # a username with this many failures in the window is refused, from anywhere
MAX_FAILURES_PER_IP = 20       # and so is an address that has failed this often, whatever usernames it tried


def client_ip(request: Request) -> str:
    """The real visitor's address. Behind the Cloudflare tunnel every request arrives from cloudflared, so the address Cloudflare saw
    (CF-Connecting-IP) comes first, then nginx's X-Real-IP, then the socket itself."""
    for header in ("cf-connecting-ip", "x-real-ip"):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


@router.post("/login")
def login(body: dict, request: Request, response: Response):
    username = str(body.get("username") or "")
    password = str(body.get("password") or "")
    ip = client_ip(request)

    # Checked before the password, so a locked-out guesser learns nothing — not even a right password gets in until the lockout ends.
    by_user, by_ip = login_failure_counts(username, ip, LOCKOUT_MINUTES)
    if by_user >= MAX_FAILURES_PER_USER or by_ip >= MAX_FAILURES_PER_IP:
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {LOCKOUT_MINUTES} minutes.",
                            headers={"Retry-After": str(LOCKOUT_MINUTES * 60)})

    user = get_user_by_username(username)
    # Generic message either way — don't reveal whether the username itself was valid.
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        record_login_failure(username, ip)
        raise HTTPException(401, "Invalid username or password")

    clear_login_failures(username)
    token = mint_token()
    create_session(user["users_id"], hash_token(token), session_expiry())
    response.set_cookie(
        SESSION_COOKIE_NAME, token,
        httponly=True, secure=True, samesite="lax", path="/",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
    )
    return {"username": user["username"], "role": user.get("role", "admin")}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        delete_session(hash_token(token))  # server-side revocation, not just clearing the cookie
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(require_auth)):
    # `accounts` lists the accounts a limited / read-only user may use (None for an admin, who may use all of them).
    return {"username": user["username"], "role": user.get("role", "admin"), "accounts": user.get("accounts")}


def require_admin(user: dict = Depends(require_auth)) -> dict:
    if user.get("role", "admin") != "admin":
        raise HTTPException(403, "Only an admin can manage users")
    return user


# ── Account management ──────────────────────────────────────────────────────
# Roles: admin (everything), limited (read / write on the accounts mapped to the user) and readonly (read only on them) — api/access.py. Only an admin
# manages users; anyone can change their own password.

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
def get_users(_user: dict = Depends(require_admin)):
    return list_users()


@router.post("/users")
def add_user(body: dict, _user: dict = Depends(require_admin)):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    role = str(body.get("role") or "admin")
    if role not in ROLES:
        raise HTTPException(400, "Role must be admin, limited or readonly")
    account_ids = [int(a) for a in (body.get("account_ids") or [])]
    if role != "admin" and not account_ids:
        raise HTTPException(400, "A limited or read-only user needs at least one account")
    if not username:
        raise HTTPException(400, "Username is required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        users_id = create_user(username, password_hash, role, account_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"users_id": users_id, "username": username}


@router.put("/users/{users_id}")
def update_user_access(users_id: int, body: dict, _user: dict = Depends(require_admin)):
    """Set a user's role and, for limited / read-only, the accounts they can use. Optionally resets their password too."""
    role = str(body.get("role") or "")
    if role not in ROLES:
        raise HTTPException(400, "Role must be admin, limited or readonly")
    account_ids = [int(a) for a in (body.get("account_ids") or [])]
    if role != "admin" and not account_ids:
        raise HTTPException(400, "A limited or read-only user needs at least one account")
    new_pw = str(body.get("new_password") or "")
    if new_pw and len(new_pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    try:
        set_user_access(users_id, role, account_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if new_pw:
        update_password(users_id, bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode())
    return {"ok": True}


@router.delete("/users/{users_id}")
def remove_user(users_id: int, user: dict = Depends(require_admin)):
    if users_id == user["users_id"]:
        raise HTTPException(400, "You can't delete your own account")
    if not delete_user(users_id):
        raise HTTPException(400, "Can't delete the last remaining admin")
    return {"ok": True}
