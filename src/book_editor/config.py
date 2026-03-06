from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    openrouter_api_key: str

    # OpenRouter base
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Model assignments
    editor_model: str = "google/gemini-2.5-pro"  # 1M context, highest quality
    stylist_model: str = "google/gemini-2.5-flash"  # 1M context, fast + opinionated
    judge_model: str = "anthropic/claude-sonnet-4"  # strong reasoning for validation
    worker_model: str = "google/gemini-2.5-flash"  # fast chapter editing
    audience_model: str = "google/gemini-2.5-flash"  # roleplay feedback
    micro_model: str = "google/gemini-2.0-flash-001"  # cheapest 1M ctx model ($0.13/M input)

    # Concurrency
    max_concurrent_workers: int = 5

    # App
    app_name: str = "book-editor"
    log_level: str = "INFO"
    access_key: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
