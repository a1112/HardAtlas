from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import jwt
from fastapi import Request
from hardatlas_domain import Principal

from .config import Settings


class AuthenticationError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class ApiAuthenticator:
    def __init__(
        self,
        settings: Settings,
        *,
        signing_key_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        if settings.auth_mode not in {"development", "oidc"}:
            raise ValueError(f"unsupported auth mode: {settings.auth_mode}")
        if (
            settings.environment.casefold() in {"prod", "production"}
            and settings.auth_mode == "development"
        ):
            raise ValueError("development authentication is forbidden in production")
        self.signing_key_resolver = signing_key_resolver
        self._jwks_client: jwt.PyJWKClient | None = None

    def authenticate(self, request: Request) -> Principal | None:
        if self.settings.auth_mode == "development":
            return self._authenticate_development(request)
        return self._authenticate_oidc(request)

    def _authenticate_development(self, request: Request) -> Principal | None:
        subject = request.headers.get("x-hardatlas-dev-principal")
        if not subject:
            return None
        roles = sorted(
            {
                role.strip()
                for role in request.headers.get("x-hardatlas-dev-roles", "").split(",")
                if role.strip()
            }
        )
        return Principal(
            subject=subject,
            display_name=request.headers.get(
                "x-hardatlas-dev-display-name",
                subject,
            ),
            email=request.headers.get("x-hardatlas-dev-email"),
            workspace_id=self._workspace_id(
                request.headers.get("x-hardatlas-dev-workspace-id"),
                subject=subject,
            ),
            roles=roles,
            authentication_method="development",
        )

    def _authenticate_oidc(self, request: Request) -> Principal | None:
        authorization = request.headers.get("authorization")
        if not authorization:
            return None
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token:
            raise AuthenticationError("authorization header must use Bearer")
        try:
            unverified_header = jwt.get_unverified_header(token)
            algorithm = str(unverified_header.get("alg", ""))
            allowed_algorithms = {
                item.strip()
                for item in self.settings.oidc_algorithms.split(",")
                if item.strip()
            }
            if algorithm not in allowed_algorithms:
                raise AuthenticationError("token signing algorithm is not allowed")
            if self.signing_key_resolver is not None:
                key = self.signing_key_resolver(token)
            else:
                if self._jwks_client is None:
                    self._jwks_client = jwt.PyJWKClient(
                        self.settings.oidc_jwks_url,
                        cache_jwk_set=True,
                        lifespan=300,
                    )
                key = self._jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=sorted(allowed_algorithms),
                audience=self.settings.oidc_audience,
                issuer=self.settings.oidc_issuer,
                leeway=self.settings.oidc_clock_skew_seconds,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                },
            )
        except AuthenticationError:
            raise
        except jwt.PyJWTError as error:
            raise AuthenticationError(f"invalid access token: {error}") from error

        roles = self._extract_roles(claims)
        subject = str(claims["sub"])
        return Principal(
            subject=subject,
            display_name=str(
                claims.get("name")
                or claims.get("preferred_username")
                or subject
            ),
            email=str(claims["email"]) if claims.get("email") else None,
            workspace_id=self._workspace_id(
                claims.get(self.settings.oidc_workspace_claim),
                subject=subject,
            ),
            roles=roles,
            authentication_method="oidc",
        )

    def _workspace_id(self, claimed: object, *, subject: str) -> str:
        if claimed is None or str(claimed).strip() == "":
            return str(
                uuid5(
                    NAMESPACE_URL,
                    f"hardatlas:{self.settings.oidc_issuer}:{subject}",
                )
            )
        try:
            return str(UUID(str(claimed)))
        except ValueError as error:
            raise AuthenticationError("workspace claim must be a UUID") from error

    def _extract_roles(self, claims: dict[str, Any]) -> list[str]:
        roles: set[str] = set()
        direct_roles = claims.get(self.settings.oidc_roles_claim, [])
        if isinstance(direct_roles, list):
            roles.update(str(role) for role in direct_roles)
        realm_access = claims.get("realm_access")
        if isinstance(realm_access, dict) and isinstance(realm_access.get("roles"), list):
            roles.update(str(role) for role in realm_access["roles"])
        resource_access = claims.get("resource_access")
        if isinstance(resource_access, dict):
            audience_access = resource_access.get(self.settings.oidc_audience)
            if isinstance(audience_access, dict) and isinstance(
                audience_access.get("roles"),
                list,
            ):
                roles.update(str(role) for role in audience_access["roles"])
        return sorted(roles)
