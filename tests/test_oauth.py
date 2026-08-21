import pytest
from unittest.mock import patch, MagicMock
from httpx import ASGITransport, AsyncClient
from web.routes import app

@pytest.fixture
def mock_httpx_post():
    with patch("web.routes.httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "fake_token", "account_name": "test"}
        mock_post.return_value = mock_response
        yield mock_post

@pytest.mark.asyncio
async def test_oauth_callback_no_token(mock_httpx_post):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/oauth/callback", params={"code": "123", "account": "test"})
    assert response.status_code == 400
    assert "відсутній токен ініціатора" in response.text

@pytest.mark.asyncio
async def test_oauth_callback_invalid_token(mock_httpx_post):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/oauth/callback", params={"code": "123", "account": "test", "oauth_token": "invalid"})
    assert response.status_code == 400
    assert "сесія авторизації не знайдена" in response.text
