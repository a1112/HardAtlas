import { NextRequest, NextResponse } from "next/server";
import {
  ACCESS_COOKIE,
  REFRESH_COOKIE,
  RETURN_COOKIE,
  STATE_COOKIE,
  VERIFIER_COOKIE,
  exchangeToken,
  oidcClientId,
  tokenCookieOptions,
} from "../../../../lib/server-oidc";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  const expectedState = request.cookies.get(STATE_COOKIE)?.value;
  const verifier = request.cookies.get(VERIFIER_COOKIE)?.value;
  if (
    !code ||
    !state ||
    !expectedState ||
    state !== expectedState ||
    !verifier
  ) {
    return NextResponse.json(
      { detail: "OIDC callback state or PKCE verifier is invalid" },
      { status: 400 },
    );
  }
  const callback = new URL("/api/auth/callback", request.url);
  const tokens = await exchangeToken(
    new URLSearchParams({
      grant_type: "authorization_code",
      client_id: oidcClientId(),
      code,
      code_verifier: verifier,
      redirect_uri: callback.toString(),
    }),
  );
  const returnTo = request.cookies.get(RETURN_COOKIE)?.value ?? "/";
  const response = NextResponse.redirect(new URL(returnTo, request.url));
  response.cookies.set(
    ACCESS_COOKIE,
    tokens.access_token,
    tokenCookieOptions(tokens.expires_in),
  );
  if (tokens.refresh_token) {
    response.cookies.set(
      REFRESH_COOKIE,
      tokens.refresh_token,
      tokenCookieOptions(tokens.refresh_expires_in ?? 3600),
    );
  }
  for (const name of [STATE_COOKIE, VERIFIER_COOKIE, RETURN_COOKIE]) {
    response.cookies.delete(name);
  }
  return response;
}
