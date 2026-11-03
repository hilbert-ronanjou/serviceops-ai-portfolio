"""Environment-only runtime configuration. No POC credentials are consumed."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get('DATABASE_URL', '')
    glpi_url: str = os.environ.get('GLPI_URL', 'http://glpi')
    glpi_client_id: str = os.environ.get('GLPI_CLIENT_ID', '')
    glpi_client_secret: str = os.environ.get('GLPI_CLIENT_SECRET', '')
    glpi_username: str = os.environ.get('GLPI_USERNAME', '')
    glpi_password: str = os.environ.get('GLPI_PASSWORD', '')
    entity_id: int = int(os.environ.get('GLPI_ENTITY_ID', '0'))
    employee_origin: str = os.environ.get('EMPLOYEE_ORIGIN', 'http://127.0.0.1:18181')
    technician_origin: str = os.environ.get('TECHNICIAN_ORIGIN', 'http://127.0.0.1:18182')
    secure_cookie: bool = os.environ.get('SECURE_COOKIE', 'false') == 'true'


settings = Settings()
