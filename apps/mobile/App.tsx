// InvoiceFlow for clients: sign in, see your documents and their status, photograph or
// pick an invoice to send it, answer your accountant's questions. Everything goes through the
// existing public API; nothing is cached on the device beyond the session token.
import Constants from "expo-constants";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, FlatList, Linking, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { ApiError, InvoiceFlowClient, describeAsset, type Comment, type DocumentRow, type Summary, type User } from "./src/api";
import { localeFrom, t as translate, type Locale, type MessageKey } from "./src/i18n";
import { loadServer, loadToken, saveServer, saveToken } from "./src/storage";
import { colors, radius } from "./src/theme";

const DEFAULT_API = (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl ?? "https://api.invoiceflow.example";

type Screen = { name: "home" } | { name: "document"; id: string };

export default function App() {
  const [server, setServer] = useState(DEFAULT_API);
  const [client, setClient] = useState<InvoiceFlowClient | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);
  const locale: Locale = localeFrom(user?.locale ?? Intl.DateTimeFormat().resolvedOptions().locale);
  const t = useCallback((key: MessageKey, vars?: Record<string, string | number>) => translate(locale, key, vars), [locale]);

  useEffect(() => {
    void (async () => {
      const url = await loadServer(DEFAULT_API);
      const token = await loadToken();
      const c = new InvoiceFlowClient(url, token);
      setServer(url);
      setClient(c);
      if (token) {
        try {
          setUser(await c.me());
        } catch {
          c.setToken(null);
          await saveToken(null);
        }
      }
      setBooting(false);
    })();
  }, []);

  if (booting || !client) {
    return (
      <SafeAreaView style={styles.screen}>
        <ActivityIndicator color={colors.amber} />
      </SafeAreaView>
    );
  }
  if (!user) {
    return (
      <Login
        t={t}
        server={server}
        onServer={async (url) => {
          setServer(url);
          client.setToken(null);
          setClient(new InvoiceFlowClient(url, null));
          await saveServer(url);
        }}
        onLogin={async (email, password, totp, recovery) => {
          const u = await client.login(email, password, totp, recovery);
          await saveToken(client.hasToken ? "set" : null);
          setUser(u);
        }}
        client={client}
      />
    );
  }
  if (user.mfa_setup_required) {
    return <TwoFactorSetup t={t} client={client} onDone={async () => setUser(await client.me())} />;
  }
  return (
    <Shell
      t={t}
      client={client}
      user={user}
      onSignOut={async () => {
        await client.logout();
        await saveToken(null);
        setUser(null);
      }}
    />
  );
}

function Login({ t, server, onServer, onLogin, client }: { t: (k: MessageKey, v?: Record<string, string | number>) => string; server: string; onServer: (url: string) => Promise<void>; onLogin: (email: string, password: string, totp?: string, recovery?: string) => Promise<void>; client: InvoiceFlowClient }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [serverDraft, setServerDraft] = useState(server);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (serverDraft !== client.baseUrl) await onServer(serverDraft.replace(/\/+$/, ""));
      await onLogin(email.trim(), password, needTotp && !useRecovery ? totp.trim() : undefined, needTotp && useRecovery ? totp.trim() : undefined);
    } catch (e) {
      if (e instanceof ApiError && e.needsTotp) setNeedTotp(true);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.brand}>{t("app.name")}</Text>
        <Text style={styles.h1}>{t("login.title")}</Text>
        <Text style={styles.label}>{t("login.server")}</Text>
        <TextInput style={styles.input} autoCapitalize="none" autoCorrect={false} keyboardType="url" value={serverDraft} onChangeText={setServerDraft} />
        <Text style={styles.label}>{t("login.email")}</Text>
        <TextInput style={styles.input} autoCapitalize="none" autoComplete="email" keyboardType="email-address" value={email} onChangeText={setEmail} />
        <Text style={styles.label}>{t("login.password")}</Text>
        <TextInput style={styles.input} secureTextEntry autoComplete="password" value={password} onChangeText={setPassword} />
        {needTotp ? (
          <>
            <Text style={styles.label}>{useRecovery ? t("login.recovery_code") : t("login.totp")}</Text>
            <TextInput style={styles.input} keyboardType={useRecovery ? "default" : "number-pad"} autoCapitalize="none" autoComplete="one-time-code" value={totp} onChangeText={setTotp} />
            <Pressable accessibilityRole="button" onPress={() => setUseRecovery(!useRecovery)}>
              <Text style={styles.label}>{useRecovery ? t("login.totp") : t("login.recovery")}</Text>
            </Pressable>
          </>
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable accessibilityRole="button" style={[styles.button, busy && styles.disabled]} disabled={busy || !email || !password} onPress={submit}>
          <Text style={styles.buttonText}>{t("login.button")}</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

function TwoFactorSetup({ t, client, onDone }: { t: (k: MessageKey, v?: Record<string, string | number>) => string; client: InvoiceFlowClient; onDone: () => Promise<void> }) {
  const [setup, setSetup] = useState<{ otpauth_uri: string; secret: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void client
      .totpSetup()
      .then(setSetup)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [client]);

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{t("mfa.title")}</Text>
        <Text style={styles.label}>{t("mfa.intro")}</Text>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {codes ? (
          <>
            <Text style={styles.label}>{t("mfa.codes")}</Text>
            {codes.map((c) => (
              <Text key={c} selectable style={styles.input}>
                {c}
              </Text>
            ))}
            <Pressable accessibilityRole="button" style={styles.button} onPress={() => void onDone()}>
              <Text style={styles.buttonText}>{t("mfa.continue")}</Text>
            </Pressable>
          </>
        ) : setup ? (
          <>
            <Pressable accessibilityRole="button" style={styles.button} onPress={() => void Linking.openURL(setup.otpauth_uri)}>
              <Text style={styles.buttonText}>{t("mfa.open")}</Text>
            </Pressable>
            <Text style={styles.label}>{t("mfa.manual")}</Text>
            <Text selectable style={styles.input}>
              {setup.secret}
            </Text>
            <Text style={styles.label}>{t("mfa.code")}</Text>
            <TextInput style={styles.input} keyboardType="number-pad" autoComplete="one-time-code" value={code} onChangeText={setCode} />
            <Pressable
              accessibilityRole="button"
              style={[styles.button, code.length < 6 && styles.disabled]}
              disabled={code.length < 6}
              onPress={async () => {
                try {
                  setCodes((await client.totpConfirm(code.trim())).recovery_codes);
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                }
              }}
            >
              <Text style={styles.buttonText}>{t("mfa.confirm")}</Text>
            </Pressable>
          </>
        ) : (
          <ActivityIndicator color={colors.amber} />
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function Shell({ t, client, user, onSignOut }: { t: (k: MessageKey, v?: Record<string, string | number>) => string; client: InvoiceFlowClient; user: User; onSignOut: () => Promise<void> }) {
  const [screen, setScreen] = useState<Screen>({ name: "home" });
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [paused, setPaused] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [page, s] = await Promise.all([client.documents(), client.summary()]);
      setDocs(page.items);
      setSummary(s);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  }, [client]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function send(picked: ReturnType<typeof describeAsset>) {
    setBusy(true);
    setNotice(t("upload.sending"));
    try {
      await client.upload(picked);
      setNotice(t("upload.done"));
      setPaused(false);
      await reload();
    } catch (e) {
      if (e instanceof ApiError && e.isLicenseError) {
        setPaused(true);
        setNotice(t("home.paused"));
      } else setNotice(t("upload.failed", { m: e instanceof Error ? e.message : String(e) }));
    } finally {
      setBusy(false);
    }
  }

  async function capture() {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) return;
    const result = await ImagePicker.launchCameraAsync({ quality: 0.9, allowsEditing: false });
    const asset = result.assets?.[0];
    if (result.canceled || !asset) return;
    await send(describeAsset(asset.uri, asset.fileName, asset.mimeType));
  }

  async function pick() {
    const result = await DocumentPicker.getDocumentAsync({ type: ["application/pdf", "image/*", "application/xml", "text/xml"], copyToCacheDirectory: true, multiple: false });
    const asset = result.assets?.[0];
    if (result.canceled || !asset) return;
    await send(describeAsset(asset.uri, asset.name, asset.mimeType));
  }

  if (screen.name === "document") {
    return <DocumentScreen t={t} client={client} id={screen.id} onBack={() => setScreen({ name: "home" })} />;
  }
  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar style="light" />
      <View style={styles.header}>
        <Text style={styles.brand}>{t("app.name")}</Text>
        <Pressable accessibilityRole="button" onPress={onSignOut}>
          <Text style={styles.link}>{t("home.signout")}</Text>
        </Pressable>
      </View>
      <View style={styles.pad}>
        <Text style={styles.h1}>{t("home.title")}</Text>
        <Text style={styles.dim}>{user.display_name || user.email}</Text>
        {summary && summary.open_questions > 0 ? <Text style={styles.warn}>{t("home.open_questions", { n: summary.open_questions })}</Text> : null}
        {paused ? <Text style={styles.warn}>{t("home.paused")}</Text> : null}
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}
        <View style={styles.row}>
          <Pressable accessibilityRole="button" style={[styles.button, (busy || paused) && styles.disabled]} disabled={busy || paused} onPress={capture}>
            <Text style={styles.buttonText}>{t("home.capture")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={[styles.buttonSecondary, (busy || paused) && styles.disabled]} disabled={busy || paused} onPress={pick}>
            <Text style={styles.buttonSecondaryText}>{t("home.pick")}</Text>
          </Pressable>
        </View>
      </View>
      <FlatList
        data={docs}
        keyExtractor={(d) => d.id}
        refreshing={busy}
        onRefresh={reload}
        contentContainerStyle={styles.pad}
        ListEmptyComponent={<Text style={styles.dim}>{t("home.empty")}</Text>}
        renderItem={({ item }) => (
          <Pressable accessibilityRole="button" style={styles.card} onPress={() => setScreen({ name: "document", id: item.id })}>
            <Text style={styles.cardTitle}>{item.supplier_name ?? item.original_filename}</Text>
            <Text style={styles.dim}>
              {item.invoice_number ?? "—"} · {item.gross_total ?? "—"} {item.currency ?? ""}
            </Text>
            <View style={styles.row}>
              <Badge text={statusText(t, item.status)} tone={item.status === "needs_info" ? "warn" : item.status === "approved" ? "ok" : "neutral"} />
              {item.payment_status === "paid" ? <Badge text={t("payment.paid")} tone="ok" /> : item.payment_status === "partial" ? <Badge text={t("payment.partial")} tone="warn" /> : null}
            </View>
          </Pressable>
        )}
      />
    </SafeAreaView>
  );
}

function statusText(t: (k: MessageKey) => string, status: string): string {
  const key = `status.${status}` as MessageKey;
  return key in MESSAGE_KEYS ? t(key) : status;
}
const MESSAGE_KEYS: Record<string, true> = Object.fromEntries(["received", "processing", "needs_review", "needs_info", "approved", "rejected", "quarantined", "failed"].map((s) => [`status.${s}`, true]));

function DocumentScreen({ t, client, id, onBack }: { t: (k: MessageKey, v?: Record<string, string | number>) => string; client: InvoiceFlowClient; id: string; onBack: () => void }) {
  const [doc, setDoc] = useState<DocumentRow | null>(null);
  const [comments, setComments] = useState<Comment[]>([]);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, c] = await Promise.all([client.document(id), client.comments(id)]);
      setDoc(d);
      setComments(c.filter((x) => x.kind !== "note"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [client, id]);
  useEffect(() => {
    void load();
  }, [load]);
  const openQuestion = useMemo(() => comments.some((c) => c.kind === "question" && !c.resolved_at), [comments]);

  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={styles.pad}>
        <Pressable accessibilityRole="button" onPress={onBack}>
          <Text style={styles.link}>← {t("doc.back")}</Text>
        </Pressable>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {doc ? (
          <>
            <Text style={styles.h1}>{doc.supplier_name ?? doc.original_filename}</Text>
            <Text style={styles.dim}>
              {doc.invoice_number ?? "—"} · {doc.issue_date ?? "—"} · {doc.gross_total ?? "—"} {doc.currency ?? ""}
            </Text>
            <Text style={styles.label}>{t("doc.status")}</Text>
            <Badge text={statusText(t, doc.status)} tone={doc.status === "needs_info" ? "warn" : doc.status === "approved" ? "ok" : "neutral"} />
            {doc.error ? <Text style={styles.warn}>{doc.error}</Text> : null}
          </>
        ) : (
          <ActivityIndicator color={colors.amber} />
        )}
        <Text style={styles.h2}>{t("doc.questions")}</Text>
        {comments.length === 0 ? <Text style={styles.dim}>{t("doc.none")}</Text> : null}
        {comments.map((c) => (
          <View key={c.id} style={[styles.card, c.kind === "answer" && styles.cardAnswer]}>
            <Text style={styles.dim}>
              {c.kind} · {c.created_at.slice(0, 10)}
            </Text>
            <Text style={styles.text}>{c.body}</Text>
          </View>
        ))}
        {openQuestion ? (
          <>
            <Text style={styles.label}>{t("doc.answer")}</Text>
            <TextInput style={[styles.input, styles.multiline]} multiline value={answer} onChangeText={setAnswer} />
            <Pressable
              accessibilityRole="button"
              style={[styles.button, (busy || !answer.trim()) && styles.disabled]}
              disabled={busy || !answer.trim()}
              onPress={async () => {
                setBusy(true);
                try {
                  await client.answer(id, answer.trim());
                  setAnswer("");
                  await load();
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Text style={styles.buttonText}>{t("doc.send")}</Text>
            </Pressable>
          </>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function Badge({ text, tone }: { text: string; tone: "ok" | "warn" | "neutral" }) {
  return (
    <View style={[styles.badge, tone === "ok" ? styles.badgeOk : tone === "warn" ? styles.badgeWarn : null]}>
      <Text style={styles.badgeText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  pad: { padding: 16, gap: 8 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.line },
  brand: { color: colors.amber, fontWeight: "700", fontSize: 18 },
  h1: { color: colors.text, fontSize: 24, fontWeight: "700", marginTop: 8 },
  h2: { color: colors.text, fontSize: 18, fontWeight: "600", marginTop: 16 },
  text: { color: colors.text, fontSize: 15 },
  dim: { color: colors.dim, fontSize: 14 },
  label: { color: colors.dim, fontSize: 13, marginTop: 10 },
  input: { backgroundColor: colors.surface, color: colors.text, borderColor: colors.line, borderWidth: 1, borderRadius: radius.md, padding: 12, fontSize: 16 },
  multiline: { minHeight: 90, textAlignVertical: "top" },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 },
  button: { backgroundColor: colors.amber, borderRadius: radius.md, paddingVertical: 12, paddingHorizontal: 16, alignItems: "center", marginTop: 12 },
  buttonText: { color: colors.onAmber, fontWeight: "700", fontSize: 16 },
  buttonSecondary: { borderColor: colors.amber, borderWidth: 1, borderRadius: radius.md, paddingVertical: 12, paddingHorizontal: 16, alignItems: "center", marginTop: 12 },
  buttonSecondaryText: { color: colors.amber, fontWeight: "600", fontSize: 16 },
  disabled: { opacity: 0.5 },
  link: { color: colors.amber, fontSize: 15 },
  error: { color: colors.danger, marginTop: 8 },
  warn: { color: colors.amber, marginTop: 8 },
  notice: { color: colors.text, marginTop: 8 },
  card: { backgroundColor: colors.surface, borderRadius: radius.lg, padding: 14, borderWidth: 1, borderColor: colors.line, marginTop: 8 },
  cardAnswer: { backgroundColor: colors.surface2 },
  cardTitle: { color: colors.text, fontSize: 16, fontWeight: "600" },
  badge: { backgroundColor: colors.surface2, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3, alignSelf: "flex-start" },
  badgeOk: { backgroundColor: "rgba(110,231,160,0.15)" },
  badgeWarn: { backgroundColor: "rgba(245,165,36,0.15)" },
  badgeText: { color: colors.text, fontSize: 12 },
});
