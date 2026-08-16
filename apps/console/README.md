# The Mnemos console

A Next.js app in two halves, both reading the same live deployment:

- **The marketing site** (`/`, `/how-it-works`) — what Mnemos is and why, for
  a visitor who has not opened the dashboard yet. Route group
  `src/app/(marketing)/`, its own header/footer, no dashboard chrome.
- **The console** (`/console/*`) — the nine operating screens Phase 08.2
  specifies: Overview, Memory Explorer, Time Machine, Residency, Ledger,
  Custodian, Deposition, Blast Radius, Forget. `src/app/console/`, behind
  `Shell`'s nav rail and the live memory-trace footer.

Both are a real URL segment, not a route group — `/console/explorer` is an
actual path, so the dashboard's nav links, `router.push` calls, and the
in-browser back button all just work without a rewrite layer.

## Running it locally

```bash
pnpm install
cp .env.example .env.local   # fill in MNEMOS_API_URL and at least one read key
pnpm dev
```

`predev`/`prebuild` run `scripts/sync-brand.mjs` first, which copies
`brand/tokens.css`, `brand/favicon.svg`, and `brand/logo(type).svg` from the
workspace root into this app (gitignored, regenerated every time — see that
script's own docstring for why). Nothing here hand-maintains a copy of the
brand system.

```bash
pnpm typecheck && pnpm lint && pnpm build   # what `make console-check` runs
```

The console is the one part of Mnemos that is not on AWS. It is a Next.js app
with a server-side BFF, and Vercel runs that shape natively; everything it
talks to (the API on Lambda, the cluster on CockroachDB Cloud) stays where it
was. The trade is a deliberate one — see `docs/decisions.md`.

```bash
pnpm dlx vercel@latest login          # once, interactive
pnpm dlx vercel@latest link           # from apps/console — root dir is this folder
pnpm dlx vercel@latest env add MNEMOS_API_URL production
pnpm dlx vercel@latest env add MNEMOS_API_KEY_READ_CLINIC production
pnpm dlx vercel@latest env add MNEMOS_API_KEY_READ_OPS production
pnpm dlx vercel@latest env add MNEMOS_API_KEY_READ_FINANCE production
pnpm dlx vercel@latest --prod
```

`make deploy-console` runs the last step once the project is linked.

**Environment variables are the whole configuration.** There is no admin key
among them, on purpose: the admin key is typed into the Forget and Revoke
flows at the moment it is needed, posted to a route handler, used for one
call, and gone when the request ends (`src/components/admin-gate.tsx`). A
console that stored an admin key would be a console that could destroy
memory while nobody was looking.

**What a visitor holds: nothing.** All three read keys live in Vercel's
server environment and are read only in server components. The browser never
receives a Mnemos credential, which is also enforced structurally — the CSP
in `next.config.ts` sets `connect-src 'self'`, so the page cannot reach the
Mnemos API directly even if some future component tried.
