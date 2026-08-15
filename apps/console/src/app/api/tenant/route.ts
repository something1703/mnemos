import { NextResponse } from "next/server";
import { TENANTS, TENANT_COOKIE } from "@/lib/api/tenants";

/**
 * Records which demo tenant the console is reading.
 *
 * The cookie carries a slug, never a key. It is validated against the list
 * this app controls, and even if a visitor forged one, the API resolves the
 * tenant from the credential the server attaches — not from anything the
 * browser said.
 */
export async function POST(request: Request) {
  const { slug } = await request.json().catch(() => ({ slug: null }));
  if (!TENANTS.some((t) => t.slug === slug)) {
    return NextResponse.json({ error: "unknown tenant" }, { status: 400 });
  }
  const response = NextResponse.json({ slug });
  response.cookies.set(TENANT_COOKIE, slug, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return response;
}
