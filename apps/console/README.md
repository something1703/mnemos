This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

## Deploying to Vercel

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
