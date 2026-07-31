import "server-only";

export interface OidcDiscovery {
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

export interface OidcTokenResponse {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
  refresh_expires_in?: number;
  token_type: string;
}

export const ACCESS_COOKIE = "hardatlas_web_access";
export const REFRESH_COOKIE = "hardatlas_web_refresh";
export const STATE_COOKIE = "hardatlas_web_oauth_state";
export const VERIFIER_COOKIE = "hardatlas_web_pkce_verifier";
export const RETURN_COOKIE = "hardatlas_web_return_to";

export function authMode() {
  const mode =
    process.env.HARDATLAS_AUTH_MODE ??
    (process.env.NODE_ENV === "production" ? "oidc" : "development");
  if (process.env.NODE_ENV === "production" && mode === "development") {
    throw new Error("production Web may not use development authentication");
  }
  return mode;
}

export function oidcIssuer() {
  const issuer = process.env.HARDATLAS_OIDC_ISSUER;
  if (!issuer && process.env.NODE_ENV === "production") {
    throw new Error("HARDATLAS_OIDC_ISSUER is required in production");
  }
  return (issuer ?? "http://localhost:8080/realms/hardatlas").replace(
    /\/$/,
    "",
  );
}

export function oidcClientId() {
  return process.env.HARDATLAS_WEB_OIDC_CLIENT_ID ?? "hardatlas-web";
}

export async function discovery(): Promise<OidcDiscovery> {
  const response = await fetch(
    `${oidcIssuer()}/.well-known/openid-configuration`,
    { next: { revalidate: 3600 } },
  );
  if (!response.ok) {
    throw new Error(`OIDC discovery failed: ${response.status}`);
  }
  return response.json() as Promise<OidcDiscovery>;
}

export function secureCookie() {
  return process.env.NODE_ENV === "production";
}

export function tokenCookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    maxAge,
    path: "/",
    sameSite: "lax" as const,
    secure: secureCookie(),
  };
}

export async function exchangeToken(parameters: URLSearchParams) {
  const metadata = await discovery();
  const response = await fetch(metadata.token_endpoint, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: parameters,
    cache: "no-store",
  });
  const payload = (await response.json()) as OidcTokenResponse & {
    error?: string;
    error_description?: string;
  };
  if (!response.ok || payload.error) {
    throw new Error(
      payload.error_description ??
        payload.error ??
        "OIDC token exchange failed",
    );
  }
  return payload;
}

export async function refreshAccessToken(refreshToken: string) {
  return exchangeToken(
    new URLSearchParams({
      grant_type: "refresh_token",
      client_id: oidcClientId(),
      refresh_token: refreshToken,
    }),
  );
}
