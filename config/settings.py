# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PROJECT_NAME: str = "AI Quant Python"
    VERSION: str = "1.0.0"

    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "quant_db")
    DB_USER: str = os.getenv("DB_USER", "quant")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "quant123")

    TUSHARE_TOKEN: str = os.getenv("TUSHARE_TOKEN", "")

    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def ASYNC_DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"


settings = Settings()
