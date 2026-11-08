# InvoiceFlow mobile (Expo / React Native)

The native client app: sign in with the firm's user account
(password + TOTP), see your documents and their status, photograph or pick an invoice and send it,
answer the accountant's questions. It uses the public API only (`docs/api.md`) — no new endpoints.

```bash
cd apps/mobile
npm install
npm test            # vitest: API client, i18n
npm run typecheck   # tsc
npx expo start      # Expo Go / dev client; set the server URL on the sign-in screen
```

- The session token is kept in the device secure store (`expo-secure-store`); nothing else is
  cached on the device.
- Uploads go through `POST /api/v1/documents` as multipart; a `402 license_required` answer
  shows the "intake paused" notice and keeps the documents readable.
- Quality hints at capture time stay a web PWA feature; the pipeline judges the photo and asks for
  a retake through the normal "request info" flow.
- Default server: `expo.extra.apiUrl` in `app.json`; the sign-in screen lets the user change it
  (self-hosted firms).
- Translations: EN and DE in `src/i18n.ts`; FR, NL, ES, IT and PL fall back to English until
  translated.
