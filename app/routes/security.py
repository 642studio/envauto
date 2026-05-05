"""Auth simple por bearer token. Suficiente para una API personal en VPS."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

bearer_scheme = HTTPBearer(auto_error=True)


def require_token(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> None:
    if creds.credentials != settings.api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )
