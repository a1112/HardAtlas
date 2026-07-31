import { createHash, randomBytes } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import {
  RETURN_COOKIE,
  STATE_COOKIE,
  VERIFIER_COOKIE,
  authMode,
  discovery,
  oidcClientId,
  tokenCookieOptions,
} from "../../../../lib/server-oidc";

function base64Url(value: Buffer) {
  return value.toString("base64url");
}

export async function GET(request: NextRequest) {
  if (authMode() === "development") {
    const returnTo = request.nextUrl.searchParams.get("returnTo") ?? "/";
    return NextResponse.redirect(
      new URL(returnTo.startsWith("/") ? returnTo : "/", request.url),
    );
  }
  const metadata = await discovery();
  const verifier = base64Url(randomBytes(48));
  const challenge = base64Url(createHash("sha256").update(verifier).digest());
  const state = base64Url(randomBytes(32));
  const callback = new URL("/api/auth/callback", request.url);
  const returnTo = request.nextUrl.searchParams.get("returnTo") ?? "/";
  const safeReturnTo = returnTo.startsWith("/") ? returnTo : "/";
  const authorization = new URL(metadata.authorization_endpoint);
  authorization.search = new URLSearchParams({
    response_type: "code",
    client_id: oidcClientId(),
    redirect_uri: callback.toString(),
    scope: "openid profile email",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  }).toString();
  const response = NextResponse.redirect(authorization);
  response.cookies.set(STATE_COOKIE, state, tokenCookieOptions(600));
  response.cookies.set(VERIFIER_COOKIE, verifier, tokenCookieOptions(600));
  response.cookies.set(RETURN_COOKIE, safeReturnTo, tokenCookieOptions(600));
  return response;
}
