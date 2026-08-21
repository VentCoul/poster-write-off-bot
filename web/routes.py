import structlog
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from core.config import settings
from db.repositories.clients import ClientRepo
from db.session import AsyncSessionLocal

app = FastAPI(docs_url=None, redoc_url=None, root_path="/writeoff")
log = structlog.get_logger()


@app.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(request: Request) -> HTMLResponse:
    log.info("oauth.callback_received", query=dict(request.query_params))

    code = request.query_params.get("code")
    account = request.query_params.get("account", "").strip().lower()
    # Poster doesn't relay a `state` param back — our per-admin linking token
    # instead rides along as part of `redirect_uri` itself (see bot/handlers/
    # oauth.py), which Poster does echo back verbatim.
    oauth_token = request.query_params.get("oauth_token")

    if not code:
        return HTMLResponse("<h2>❌ Помилка: код авторизації відсутній</h2>", status_code=400)
    if not account:
        return HTMLResponse("<h2>❌ Помилка: account відсутній</h2>", status_code=400)

    # Must match the URI registered in Poster app settings EXACTLY — Poster
    # rejects the token exchange otherwise ("Redirect URI does not match
    # registered redirect URI"), so no query params may be appended here.
    redirect_uri_used = f"{settings.web_base_url}/oauth/callback"

    token_url = f"https://{account}.joinposter.com/api/v2/auth/access_token"
    log.info("oauth.token_exchange", account=account, redirect=redirect_uri_used)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(token_url, data={
            "code": code,
            "client_id": settings.poster_app_id,
            "client_secret": settings.poster_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri_used,
        })
        data = resp.json()

    log.info("oauth.token_response", status=resp.status_code, body=data)

    access_token = data.get("access_token")
    account_name = account or data.get("account_name", "unknown")

    if not access_token:
        log.error("oauth.no_token", response=data)
        return HTMLResponse(
            f"<h2>❌ Помилка авторизації</h2><pre>{data}</pre>",
            status_code=400,
        )

    async with AsyncSessionLocal() as session:
        client_repo = ClientRepo(session)
        client = await client_repo.upsert(account_name, access_token)

        # Link only the specific admin who initiated this OAuth flow
        if oauth_token:
            tg_id = await client_repo.pop_pending_oauth(oauth_token)
            if tg_id:
                await client_repo.add_admin(tg_id, client.id)
                log.info("oauth.linked_admin", tg_id=tg_id, account=account_name)
            else:
                log.warning("oauth.token_not_found", oauth_token=oauth_token)
                return HTMLResponse("<h2>❌ Помилка: сесія авторизації не знайдена або застаріла</h2>", status_code=400)
        else:
            log.warning("oauth.no_token_provided")
            return HTMLResponse("<h2>❌ Помилка: відсутній токен ініціатора</h2>", status_code=400)

        await session.commit()

    return HTMLResponse(_success_page())


def _success_page() -> str:
    """OAuth success page that bounces the user straight back into the bot."""
    tg_link = f"https://t.me/{settings.bot_username}?start=connected"
    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Poster підключено</title>
<meta http-equiv="refresh" content="1;url={tg_link}">
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0e1621;
         color:#fff; display:flex; min-height:100vh; margin:0; align-items:center;
         justify-content:center; text-align:center; }}
  .card {{ padding:32px; }}
  .ok {{ font-size:56px; }}
  a.btn {{ display:inline-block; margin-top:20px; padding:14px 28px; background:#2ea6ff;
          color:#fff; text-decoration:none; border-radius:12px; font-size:17px; font-weight:600; }}
  p {{ color:#8a9bb0; }}
</style>
</head>
<body>
  <div class="card">
    <div class="ok">✅</div>
    <h2>Poster успішно підключено!</h2>
    <p>Повертаємо вас у Telegram…</p>
    <a class="btn" href="{tg_link}">Відкрити бота</a>
  </div>
  <script>
    setTimeout(function () {{ window.location.href = "{tg_link}"; }}, 800);
  </script>
</body>
</html>"""


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
