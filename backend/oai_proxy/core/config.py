from pydantic import Secret
from pydantic_settings import BaseSettings, SettingsConfigDict

from api.util.fs import ROOT_DIR


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / '.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    OAI_PROXY_HOST: str = '127.0.0.1'
    OAI_PROXY_PORT: int = 8084
    OAI_PROXY_WORKERS: int = 1
    OAI_PROXY_AES_KEY: Secret[str]
    # Static OpenAI key - when set, requests with "Bearer STATIC" use this key
    # The real key never leaves this service
    OAI_PROXY_STATIC_KEY: Secret[str] | None = None

    # x402 configuration
    # Service wallet private key (base58) for paying x402 API calls
    X402_SERVICE_WALLET_KEY: Secret[str] | None = None
    X402_GATEWAY_URL: str = 'https://x402-gateway-production.up.railway.app'
    # Solana RPC for payment transactions
    PAYMENT_SOLANA_RPC_URL: str = 'https://deborah-q5m00f-fast-mainnet.helius-rpc.com'


settings = Settings()  # type: ignore[missing-argument]
