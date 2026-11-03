# rewst-migration — a Bifrost standalone_v2 app

## Local dev (no token pasting)

`bifrost solution start` supplies its selected instance and token to Vite.
Direct `npm run dev` uses the CLI's selected default profile unless this app
directory contains a BIFROST_API_URL override.

    bifrost solution start

The command installs the `bifrost` SDK transiently from the selected instance
without writing that instance into package.json. After the first start, you can
also run `npm run dev` directly at http://localhost:5173.

(To override the selected default for this app, copy `.env.example` to `.env`
in the directory where you run Vite and set BIFROST_API_URL. Authenticate that URL
once with the CLI; its credentials remain in the global credential store.)

## Deploy

The platform builds the app server-side and serves it at `/apps/rewst-migration`:

    bifrost deploy
