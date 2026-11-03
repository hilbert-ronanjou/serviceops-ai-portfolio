"""Fixed approved models/region/pricing; local owner attestation gates ALL paid calls."""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

CHAT_MODEL='qwen3.8-flash'
EMBED_MODEL='text-embedding-v4'
REGION='cn-beijing'
PRICE_VERSION='aliyun-cn-beijing-2026-10-14'
BUDGET_ID='M2-20261014'
INTERNAL_LIMIT_NANO=20_000_000_000
AUTHORIZED_TOTAL_NANO=30_000_000_000
INPUT_RATE_NANO=800
OUTPUT_RATE_NANO=2700
EMBED_RATE_NANO=500
DIMENSION=1024
MAX_INPUT_TOKENS=24000
MAX_OUTPUT_TOKENS=1024
MAX_CONCURRENT=2


class AIError(Exception):
    def __init__(self,code,status=503):self.code,self.status=code,status


@dataclass(frozen=True)
class AIConfig:
    api_key: str=os.environ.get('BAILIAN_API_KEY','')
    base_url: str=os.environ.get('BAILIAN_BASE_URL','https://dashscope.aliyuncs.com/compatible-mode/v1')
    live_enabled: bool=os.environ.get('MODEL_LIVE_ENABLED','false')=='true'
    authorization_path: str=os.environ.get('MODEL_AUTHORIZATION_PATH','/run/serviceops/model-authorization.json')

    def validate(self):
        try:
            parsed=urlsplit(self.base_url)
            port=parsed.port
        except ValueError:raise AIError('model_region_or_endpoint_denied') from None
        allowed=(parsed.hostname=='dashscope.aliyuncs.com' or
            bool(re.fullmatch(r'[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com',parsed.hostname or '')))
        if not allowed or (parsed.hostname or '').startswith(('token-plan.','coding.')) or parsed.scheme!='https' or parsed.path.rstrip('/')!='/compatible-mode/v1' or parsed.query or parsed.fragment or parsed.username or port not in (None,443):
            raise AIError('model_region_or_endpoint_denied')
        if not self.api_key:raise AIError('model_credentials_missing')
        if not self.live_enabled:raise AIError('model_live_calls_disabled')
        try:attestation=json.loads(Path(self.authorization_path).read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):raise AIError('cloud_budget_confirmation_missing') from None
        if not isinstance(attestation,dict):raise AIError('cloud_budget_confirmation_invalid')
        import hashlib
        expected=hashlib.sha256(self.api_key.encode()).hexdigest()
        if (attestation.get('provider')!='aliyun-model-studio' or attestation.get('region')!=REGION or
            attestation.get('budget_id')!=BUDGET_ID or attestation.get('key_sha256')!=expected or
            attestation.get('monthly_budget_yuan')!=30 or attestation.get('warning_percent')!=80 or
            attestation.get('stop_at_budget') is not True or attestation.get('dedicated_key') is not True or
            attestation.get('owner_confirmed') is not True):
            raise AIError('cloud_budget_confirmation_invalid')
        return attestation


config=AIConfig()
