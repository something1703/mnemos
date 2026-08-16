import { SiteHeader } from "@/components/marketing/site-header";
import { SiteFooter } from "@/components/marketing/site-footer";

/**
 * The public site — landing page and the technical explainer. A route
 * group, not a URL segment: `(marketing)/page.tsx` is still `/`. Kept apart
 * from `/console`'s layout so a visitor who has not opened the dashboard
 * never sees its nav rail, and a judge deep in the dashboard never sees a
 * marketing header competing with the memory trace.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="flex-1">{children}</main>
      <SiteFooter />
    </div>
  );
}
