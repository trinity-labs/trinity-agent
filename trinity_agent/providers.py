"""Provider-neutral API configuration for TRINITY Agent."""
from dataclasses import dataclass
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    label: str
    api_key_env: str
    base_url_env: str
    default_base_url: str
    default_model: str
    currency: str
    monthly_credit_env: str

    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, '').strip()

    def require_api_key(self) -> str:
        value = self.api_key()
        if not value:
            raise ValueError(f'{self.api_key_env} is required for provider {self.id}.')
        return value

    def base_url(self) -> str:
        value=os.environ.get(self.base_url_env,self.default_base_url).strip().rstrip('/')
        parsed=urlsplit(value)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f'{self.base_url_env} must be an HTTP(S) API base URL without credentials, query, or fragment.')
        return value

    def setting(self, name: str, fallback: str | None = None) -> str | None:
        """Read an API-specific limit before the shared agent default."""
        return os.environ.get(f'TRINITY_{self.id.upper()}_{name}',os.environ.get(f'TRINITY_AGENT_{name}',fallback))


PROVIDERS={item.id:item for item in (
    ProviderConfig('ovhcloud','OVHcloud AI Endpoints','TRINITY_OVHCLOUD_API_KEY','TRINITY_OVHCLOUD_BASE_URL',
                   'https://oai.endpoints.kepler.ai.cloud.ovh.net/v1','gpt-oss-120b','EUR','TRINITY_OVHCLOUD_MONTHLY_CREDIT_EUR'),
    ProviderConfig('deepseek','DeepSeek','TRINITY_DEEPSEEK_API_KEY','TRINITY_DEEPSEEK_BASE_URL',
                   'https://api.deepseek.com','deepseek-flash','USD','TRINITY_DEEPSEEK_MONTHLY_CREDIT_USD'),
    ProviderConfig('openai','OpenAI Codex API','TRINITY_OPENAI_API_KEY','TRINITY_OPENAI_BASE_URL',
                   'https://api.openai.com/v1','gpt-5.3-codex','USD','TRINITY_OPENAI_MONTHLY_CREDIT_USD'),
)}


def selected_provider() -> ProviderConfig:
    provider=os.environ.get('TRINITY_AGENT_PROVIDER','ovhcloud').strip().lower()
    try:return PROVIDERS[provider]
    except KeyError as error:raise ValueError(f"TRINITY_AGENT_PROVIDER must be one of: {', '.join(PROVIDERS)}") from error
