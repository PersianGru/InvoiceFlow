// The session token lives in the device's secure store (Keychain / Keystore), never in plain storage.
import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "invoiceflow.session";
const SERVER_KEY = "invoiceflow.server";

export async function loadToken(): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(TOKEN_KEY);
  } catch {
    return null;
  }
}

export async function saveToken(token: string | null): Promise<void> {
  try {
    if (token) await SecureStore.setItemAsync(TOKEN_KEY, token);
    else await SecureStore.deleteItemAsync(TOKEN_KEY);
  } catch {
    /* secure store unavailable: the session lives in memory only */
  }
}

export async function loadServer(fallback: string): Promise<string> {
  try {
    return (await SecureStore.getItemAsync(SERVER_KEY)) ?? fallback;
  } catch {
    return fallback;
  }
}

export async function saveServer(url: string): Promise<void> {
  try {
    await SecureStore.setItemAsync(SERVER_KEY, url);
  } catch {
    /* ignore */
  }
}
