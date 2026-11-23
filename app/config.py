from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    keitaro_base_url: str = "https://tlgk.host/admin_api/v1"
    keitaro_api_key: str
    # Предохранитель: пока false, ни одна запись в Keitaro невозможна.
    keitaro_write_enabled: bool = False


settings = Settings()
