from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
 database_url:str="sqlite:///./devs.db"
 jwt_secret:str="INSECURE-DEV-ONLY-CHANGE-ME"
 jwt_algorithm:str="HS256"
 access_token_minutes:int=30
 dev_bootstrap:bool=False
 bootstrap_username:str="admin"
 bootstrap_password:str="ChangeMe-Immediately-123!"
 storage_dir:str="./private_uploads"
 max_upload_mb:int=25
 model_config=SettingsConfigDict(env_file=".env",extra="ignore",case_sensitive=False)
settings=Settings()
