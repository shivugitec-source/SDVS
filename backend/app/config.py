from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite:///./devs.db"
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    dev_bootstrap: bool = False
    bootstrap_username: str = "admin"
    bootstrap_password: str = ""
    bootstrap_name: str = "SDVS Master Administrator"
    bootstrap_email: str = ""
    bootstrap_phone: str = ""
    storage_dir: str = "./private_uploads"
    max_upload_mb: int = 25
    require_r2: bool = False
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    otp_secret: str = ""
    otp_ttl_minutes: int = 5
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

settings = Settings()
