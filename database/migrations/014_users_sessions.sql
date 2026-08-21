-- Login auth: Users + Sessions tables. See api/routers/auth.py, api/deps.py.
-- Sessions.Token_Hash stores a SHA-256 hash of the session cookie's random
-- token, never the raw token itself — a leaked DB backup shouldn't hand over
-- live sessions.

CREATE TABLE IF NOT EXISTS Users (
    Users_Id      SERIAL PRIMARY KEY,
    Username      VARCHAR(100) UNIQUE NOT NULL,
    Password_Hash TEXT NOT NULL,
    Created_At    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS Sessions (
    Sessions_Id SERIAL PRIMARY KEY,
    Token_Hash  TEXT UNIQUE NOT NULL,
    Users_Id    INTEGER NOT NULL REFERENCES Users(Users_Id) ON DELETE CASCADE,
    Created_At  TIMESTAMPTZ DEFAULT now(),
    Expires_At  TIMESTAMPTZ NOT NULL
);
