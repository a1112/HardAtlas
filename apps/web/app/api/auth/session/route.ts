import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, authMode } from "../../../../lib/server-oidc";

export async function GET(request: NextRequest) {
  const development = authMode() === "development";
  return NextResponse.json({
    authenticated: development || Boolean(request.cookies.get(ACCESS_COOKIE)),
    mode: development ? "development" : "oidc",
  });
}
