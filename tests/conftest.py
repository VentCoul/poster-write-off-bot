import pytest
from unittest.mock import patch, AsyncMock

@pytest.fixture(autouse=True)
def mock_db_session():
    with patch("web.routes.AsyncSessionLocal") as mock_session_maker:
        mock_session = AsyncMock()
        mock_client_repo = AsyncMock()
        mock_client_repo.pop_pending_oauth.return_value = None  # Always return None so we hit the invalid token path
        
        # Patch the ClientRepo class directly if it's imported and instantiated in the route
        with patch("web.routes.ClientRepo", return_value=mock_client_repo):
            mock_session_maker.return_value.__aenter__.return_value = mock_session
            yield mock_session
