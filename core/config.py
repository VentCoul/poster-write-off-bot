from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_token: str
    poster_app_id: str
    poster_app_secret: str
    db_url: str = "postgresql+asyncpg://poster_bot:secret_password@db:5432/writeoff"
    redis_url: str = "redis://redis:6379/0"
    admin_chat_ids: list[int] = []
    super_admin_chat_id: int | None = None
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_base_url: str = "http://localhost:8080"
    bot_username: str = "poster_write_off_bot"  # used to bounce the OAuth page back into Telegram

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @field_validator("admin_chat_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: str | list | int) -> list[int]:
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return [int(x) for x in v]
        return [int(x.strip()) for x in str(v).split(",") if x.strip()]


settings = Settings()
