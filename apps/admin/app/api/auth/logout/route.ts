import { NextRequest, NextResponse } from "next/server";
import { ACCESS_COOKIE, REFRESH_COOKIE } from "../../../../lib/server-oidc";

export async function POST(request: NextRequest) {
  const response = NextResponse.redirect(new URL("/", request.url), {
    status: 303,
  });
  response.cookies.delete(ACCESS_COOKIE);
  response.cookies.delete(REFRESH_COOKIE);
  return response;
}
