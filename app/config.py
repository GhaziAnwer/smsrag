# app/config.py - Updated with cross-platform support
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

from pydantic import Field, AliasChoices, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from loguru import logger


def _default_static_dir() -> str:
    """Default static directory relative to this file."""
    return str((Path(__file__).resolve().parents[1] / "static").resolve())


def _detect_base_dir() -> str:
    """
    Auto-detect base directory based on environment.
    
    Priority:
    1. BASE_DIR or CLIENTS_BASE environment variable (explicit override)
    2. Docker: /app/data (if running in container)
    3. Windows: C:/sms (if exists)
    4. Fallback: ./data relative to project
    
    This allows the same code to work on:
    - Windows development: C:/sms
    - Docker production: /app/data
    - Any custom location via env var
    """
    # 1. Check for explicit override via environment variable
    if base_env := os.getenv("BASE_DIR") or os.getenv("CLIENTS_BASE"):
        logger.info(f"[CONFIG] Using BASE_DIR from environment: {base_env}")
        return base_env
    
    # 2. Detect if running in Docker container
    is_docker = (
        os.path.exists('/.dockerenv') or 
        os.path.exists('/run/.containerenv') or
        os.getenv('DOCKER_CONTAINER') == 'true'
    )
    
    if is_docker:
        base = "/app/data"
        logger.info(f"[CONFIG] Detected Docker environment, using: {base}")
        return base
    
    # 3. Check operating system
    import platform
    system = platform.system()
    
    if system == "Windows":
        # Try C:/sms first (your current setup)
        default_win = "C:/sms"
        if Path(default_win).exists():
            logger.info(f"[CONFIG] Detected Windows with C:/sms, using: {default_win}")
            return default_win
        else:
            # Fallback to ./data if C:/sms doesn't exist
            fallback = str(Path.cwd() / "data")
            logger.warning(f"[CONFIG] C:/sms not found, using fallback: {fallback}")
            return fallback
    
    else:
        # Linux/Mac (non-Docker): Try common locations
        for candidate in ["/opt/sms", "/opt/sms-rag/data", str(Path.cwd() / "data")]:
            if Path(candidate).exists():
                logger.info(f"[CONFIG] Detected Linux/Mac, using: {candidate}")
                return candidate
        
        # Ultimate fallback
        fallback = str(Path.cwd() / "data")
        logger.warning(f"[CONFIG] No standard paths found, using: {fallback}")
        return fallback


class Settings(BaseSettings):
    """
    Application settings with cross-platform support.
    
    Key Feature: base_dir auto-detects Windows vs Docker
    - Windows: C:/sms
    - Docker: /app/data
    - Override with BASE_DIR environment variable
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Tenants / filesystem ───────────────────────────────────────────────────
    base_dir: str = Field(
        default_factory=_detect_base_dir,  # Changed: Now auto-detects!
        validation_alias=AliasChoices("BASE_DIR", "CLIENTS_BASE"),
        description="Parent folder containing each client's folder. "
                    "Auto-detects: Windows=C:/sms, Docker=/app/data",
    )
    default_client_id: str = Field(
        default="rsms",
        validation_alias="DEFAULT_CLIENT_ID",
        description="Tenant warmed on startup and used as default.",
    )

    # ── Static SPA ─────────────────────────────────────────────────────────────
    static_dir: str = Field(
        default_factory=_default_static_dir,
        validation_alias="STATIC_DIR",
        description="Directory containing index.html, script.js, style.css.",
    )

    # ── Server / CORS ─────────────────────────────────────────────────────────
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("HOST", "API_HOST"),
    )
    port: int = Field(
        default=8000,
        validation_alias=AliasChoices("PORT", "API_PORT"),
    )
    allow_origins: str = Field(
        default="*",
        validation_alias="ALLOW_ORIGINS",
        description="Comma-separated origins for CORS.",
    )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: Optional[str] = Field(
        default=None, 
        alias="OPENAI_API_KEY",
        description="OpenAI API key (can also come from OS env).",
    )

    # ── Optional query-time reranker knobs (kept for future) ──────────────────
    reranker: str = Field(
        default="none",
        validation_alias="RERANKER",
        description="none | llm | sbert",
    )
    rerank_topk: int = Field(
        default=8,
        validation_alias="RERANK_TOPK",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )
    
    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Helper methods for path management
    # ══════════════════════════════════════════════════════════════════════════
    
    @property
    def is_docker(self) -> bool:
        """Check if running in Docker container."""
        return (
            os.path.exists('/.dockerenv') or 
            os.path.exists('/run/.containerenv') or
            os.getenv('DOCKER_CONTAINER') == 'true'
        )
    
    def get_client_path(self, client_id: str) -> Path:
        """Get the root path for a specific client/tenant."""
        return Path(self.base_dir) / client_id
    
    def get_index_path(self, client_id: str) -> Path:
        """Get the index_store path for a client."""
        return self.get_client_path(client_id) / "index_store"
    
    def get_chroma_path(self, client_id: str) -> Path:
        """Get the chroma database path for a client."""
        return self.get_index_path(client_id) / "chroma"
    
    def get_chunks_path(self, client_id: str) -> Path:
        """Get the chunks.jsonl path for a client."""
        return self.get_index_path(client_id) / "chunks.jsonl"
    
    def get_docs_base(self) -> Path:
        """
        Get the documents base directory.
        
        Documents structure:
        - Windows: C:/sms/{client}/documents/
        - Docker: /app/docs/{client}/
        """
        if self.is_docker:
            return Path("/app/docs")
        elif self.base_dir.startswith("C:/sms"):
            # Windows with C:/sms structure - docs in same location
            return Path(self.base_dir)
        else:
            # Other cases - parallel docs directory
            return Path(self.base_dir).parent / "docs"
    
    def get_client_docs_path(self, client_id: str) -> Path:
        """
        Get the documents path for a client.
        
        Returns:
        - Windows C:/sms: C:/sms/{client}/documents/
        - Docker: /app/docs/{client}/
        - Other: {base_dir}/../docs/{client}/
        """
        if self.base_dir.startswith("C:/sms"):
            # Windows C:/sms structure includes documents subfolder
            return Path(self.base_dir) / client_id / "documents"
        else:
            # Docker or other structures - flat docs structure
            return self.get_docs_base() / client_id
    
    def list_available_clients(self) -> list[str]:
        """List all available client directories that have index_store."""
        try:
            base_path = Path(self.base_dir)
            if not base_path.exists():
                logger.warning(f"Base directory does not exist: {base_path}")
                return []
            
            clients = []
            for item in base_path.iterdir():
                if item.is_dir() and (item / "index_store").exists():
                    clients.append(item.name)
            
            return sorted(clients)
        except Exception as e:
            logger.error(f"Error listing clients: {e}")
            return []
    
    def validate_client_paths(self, client_id: str) -> dict:
        """Validate that all required paths exist for a client."""
        client_path = self.get_client_path(client_id)
        index_path = self.get_index_path(client_id)
        chroma_path = self.get_chroma_path(client_id)
        chunks_path = self.get_chunks_path(client_id)
        docs_path = self.get_client_docs_path(client_id)
        
        return {
            "client_id": client_id,
            "base_dir": self.base_dir,
            "is_docker": self.is_docker,
            "client_path": str(client_path),
            "client_exists": client_path.exists(),
            "index_path": str(index_path),
            "index_exists": index_path.exists(),
            "chroma_path": str(chroma_path),
            "chroma_exists": chroma_path.exists(),
            "chunks_path": str(chunks_path),
            "chunks_exists": chunks_path.exists(),
            "docs_path": str(docs_path),
            "docs_exists": docs_path.exists(),
        }


# Global settings instance
settings = Settings()


# ══════════════════════════════════════════════════════════════════════════
# Startup logging
# ══════════════════════════════════════════════════════════════════════════

def log_startup_config():
    """Log configuration on startup (useful for debugging)."""
    logger.info("=" * 70)
    logger.info("SMS RAG APPLICATION CONFIGURATION")
    logger.info("=" * 70)
    logger.info(f"Environment: {'Docker' if settings.is_docker else 'Local'}")
    logger.info(f"Base Directory: {settings.base_dir}")
    logger.info(f"Default Client: {settings.default_client_id}")
    logger.info(f"Static Directory: {settings.static_dir}")
    logger.info(f"Host: {settings.host}:{settings.port}")
    logger.info(f"OpenAI API Key: {'***' + settings.openai_api_key[-4:] if settings.openai_api_key else 'Not Set'}")
    logger.info(f"Log Level: {settings.log_level}")
    
    # List available clients
    clients = settings.list_available_clients()
    if clients:
        logger.info(f"Available Clients ({len(clients)}): {', '.join(clients)}")
    else:
        logger.warning("No clients found in base directory")
    
    # Validate default client
    if settings.default_client_id in clients:
        validation = settings.validate_client_paths(settings.default_client_id)
        logger.info(f"Default Client '{settings.default_client_id}' Validation:")
        logger.info(f"  - Index exists: {validation['index_exists']}")
        logger.info(f"  - Chroma exists: {validation['chroma_exists']}")
        logger.info(f"  - Chunks exists: {validation['chunks_exists']}")
        logger.info(f"  - Docs exist: {validation['docs_exists']}")
    else:
        logger.warning(f"Default client '{settings.default_client_id}' not found!")
    
    logger.info("=" * 70)


# Log on import (disable with env var if needed)
if os.getenv("LOG_CONFIG_ON_IMPORT", "true").lower() == "true":
    log_startup_config()