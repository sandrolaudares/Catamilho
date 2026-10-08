"""Autenticacao, gestao de usuarios e auditoria (analytics) — SQLite persistente.

- Usuarios com hash PBKDF2 (salt por usuario), papeis: admin | user
- Sessoes por token Bearer (32 bytes hex), expiracao de 30 dias
- Eventos de auditoria: page_view, click, analyze, vectorize, calibrate,
  car_select, login, export — com usuario, timestamp, pagina, alvo e metadados
- Admin padrao: usuario "admin", senha definida por ADMIN_PASSWORD
  (default "cornbacon") — criado no primeiro boot

Persistencia: DB_PATH aponta para o volume Fly (/data) em producao.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import secrets
import sqlite3

DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "data", "catamilho.db"))
SESSION_DAYS = 30


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pass_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            page TEXT,
            target TEXT,
            meta TEXT,
            ip TEXT);
        CREATE INDEX IF NOT EXISTS idx_events_user ON events(user);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        """)
    # semeia o admin
    if not get_user("admin"):
        create_user("admin", os.getenv("ADMIN_PASSWORD", "cornbacon"),
                    role="admin")


def _hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(),
                               100_000).hex()


def _now():
    return dt.datetime.utcnow().isoformat() + "Z"


# ---------------- usuarios ----------------
def create_user(username: str, password: str, role: str = "user") -> dict:
    username = username.strip().lower()
    if not username or len(password) < 4:
        raise ValueError("usuario vazio ou senha muito curta (min 4)")
    salt = secrets.token_hex(16)
    with _conn() as c:
        c.execute(
            "INSERT INTO users(username,pass_hash,salt,role,active,created_at)"
            " VALUES(?,?,?,?,1,?)",
            (username, _hash(password, salt), salt, role, _now()))
    return {"username": username, "role": role}


def get_user(username: str):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE username=?",
                         (username.strip().lower(),)).fetchone()


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT u.username, u.role, u.active, u.created_at,
                   (SELECT COUNT(*) FROM events e WHERE e.user=u.username) AS eventos,
                   (SELECT MAX(ts) FROM events e WHERE e.user=u.username) AS ultimo_acesso
            FROM users u ORDER BY u.username""").fetchall()
    return [dict(r) for r in rows]


def set_active(username: str, active: bool) -> bool:
    with _conn() as c:
        n = c.execute("UPDATE users SET active=? WHERE username=?",
                      (1 if active else 0, username.strip().lower())).rowcount
    return n > 0


def delete_user(username: str) -> bool:
    username = username.strip().lower()
    if username == "admin":
        raise ValueError("o usuario admin nao pode ser removido")
    with _conn() as c:
        n = c.execute("DELETE FROM users WHERE username=?", (username,)).rowcount
    return n > 0


# ---------------- sessao ----------------
def login(username: str, password: str, ip: str = "") -> dict | None:
    u = get_user(username)
    if not u or not u["active"]:
        return None
    if not hmac.compare_digest(_hash(password, u["salt"]), u["pass_hash"]):
        return None
    token = secrets.token_hex(32)
    exp = (dt.datetime.utcnow()
           + dt.timedelta(days=SESSION_DAYS)).isoformat() + "Z"
    with _conn() as c:
        c.execute("INSERT INTO sessions(token,user_id,created_at,expires_at)"
                  " VALUES(?,?,?,?)", (token, u["id"], _now(), exp))
    track(u["username"], "login", "/", None, None, ip)
    return {"token": token, "username": u["username"], "role": u["role"]}


def user_by_token(token: str | None):
    if not token:
        return None
    with _conn() as c:
        r = c.execute("""
            SELECT u.username, u.role, u.active, s.expires_at
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token=?""", (token,)).fetchone()
    if not r or not r["active"] or r["expires_at"] < _now():
        return None
    return {"username": r["username"], "role": r["role"]}


def logout(token: str):
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


# ---------------- auditoria ----------------
def track(user: str, event: str, page: str | None, target: str | None,
          meta: str | None, ip: str = ""):
    with _conn() as c:
        c.execute(
            "INSERT INTO events(user,ts,event,page,target,meta,ip)"
            " VALUES(?,?,?,?,?,?,?)",
            (user, _now(), event, page, (target or "")[:200],
             (meta or "")[:500], ip))


def clear_demo() -> int:
    """Remove os eventos de demonstracao (meta com '"demo":true').
    Retorna quantos foram apagados."""
    with _conn() as c:
        n = c.execute("DELETE FROM events WHERE meta LIKE '%\"demo\":true%'").rowcount
    return n


def clear_events(only_demo: bool = True) -> int:
    """Limpa eventos. only_demo=True apaga so os de demo; False apaga TUDO."""
    with _conn() as c:
        if only_demo:
            n = c.execute("DELETE FROM events WHERE meta LIKE '%\"demo\":true%'").rowcount
        else:
            n = c.execute("DELETE FROM events").rowcount
    return n


def analytics() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
        usuarios = [dict(r) for r in c.execute("""
            SELECT user,
                   COUNT(*) AS eventos,
                   COUNT(DISTINCT substr(ts,1,10)) AS dias_ativos,
                   SUM(event='login') AS logins,
                   SUM(event='click') AS cliques,
                   SUM(event='analyze') AS analises,
                   SUM(event='vectorize') AS vetorizacoes,
                   SUM(event='calibrate') AS calibracoes,
                   MIN(ts) AS primeiro_evento,
                   MAX(ts) AS ultimo_evento
            FROM events GROUP BY user ORDER BY eventos DESC""").fetchall()]
        por_evento = [dict(r) for r in c.execute(
            "SELECT event, COUNT(*) n FROM events GROUP BY event"
            " ORDER BY n DESC").fetchall()]
        por_dia = [dict(r) for r in c.execute("""
            SELECT substr(ts,1,10) AS dia, COUNT(*) n
            FROM events GROUP BY dia ORDER BY dia DESC LIMIT 30""").fetchall()]
        por_pagina = [dict(r) for r in c.execute(
            "SELECT page, COUNT(*) n FROM events WHERE page IS NOT NULL"
            " GROUP BY page ORDER BY n DESC LIMIT 20").fetchall()]
        clicks = [dict(r) for r in c.execute("""
            SELECT target, COUNT(*) n FROM events
            WHERE event='click' AND target != ''
            GROUP BY target ORDER BY n DESC LIMIT 30""").fetchall()]
        recentes = [dict(r) for r in c.execute(
            "SELECT user, ts, event, page, target, ip FROM events"
            " ORDER BY id DESC LIMIT 100").fetchall()]
    return {"total_eventos": total, "usuarios": usuarios,
            "por_evento": por_evento, "por_dia": list(reversed(por_dia)),
            "por_pagina": por_pagina, "cliques_frequentes": clicks,
            "recentes": recentes}


def change_password(username: str, new_password: str) -> bool:
    """Troca a senha de um usuario existente (novo salt + hash)."""
    username = username.strip().lower()
    if len(new_password) < 4:
        raise ValueError("senha muito curta (min 4)")
    salt = secrets.token_hex(16)
    with _conn() as c:
        n = c.execute("UPDATE users SET pass_hash=?, salt=? WHERE username=?",
                      (_hash(new_password, salt), salt, username)).rowcount
        # invalida sessoes antigas do usuario (forca novo login)
        c.execute("DELETE FROM sessions WHERE user_id IN"
                  " (SELECT id FROM users WHERE username=?)", (username,))
    return n > 0
