"use client";
// Camera capture with live quality hints. Frames are analysed on-device;
// only the captured JPEG is uploaded.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button, Notice } from "@/components/ui";
import { ApiError, upload } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { assessFrame, hintKeys, toGray, type FrameHints } from "@/lib/capture";

const SAMPLE_W = 160;

export default function CapturePage() {
  const { t, intakeOn, refreshLicense } = useApp();
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [hints, setHints] = useState<FrameHints | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shots, setShots] = useState<Blob[]>([]);
  const paused = intakeOn === false;

  useEffect(() => {
    let stream: MediaStream | null = null;
    let timer: number | undefined;
    const video = videoRef.current;
    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } }, audio: false });
        if (video) {
          video.srcObject = stream;
          await video.play();
        }
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        const tick = () => {
          if (video && ctx && video.videoWidth) {
            const h = Math.round((SAMPLE_W * video.videoHeight) / video.videoWidth);
            canvas.width = SAMPLE_W;
            canvas.height = h;
            ctx.drawImage(video, 0, 0, SAMPLE_W, h);
            const data = ctx.getImageData(0, 0, SAMPLE_W, h).data;
            setHints(assessFrame(toGray(data, SAMPLE_W, h), SAMPLE_W, h));
          }
          timer = window.setTimeout(tick, 500);
        };
        tick();
      } catch {
        setError("camera");
      }
    };
    const handle = setTimeout(() => void start(), 0);
    return () => {
      clearTimeout(handle);
      if (timer) clearTimeout(timer);
      stream?.getTracks().forEach((tr) => tr.stop());
    };
  }, []);

  function snap() {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    canvas.toBlob((blob) => blob && setShots((s) => [...s, blob]), "image/jpeg", 0.92);
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      for (const [i, blob] of shots.entries()) {
        const form = new FormData();
        form.append("file", new File([blob], `capture-${Date.now()}-${i + 1}.jpg`, { type: "image/jpeg" }));
        await upload("/api/v1/documents", form);
      }
      router.replace("/my");
    } catch (err) {
      setError(err instanceof ApiError && err.isLicenseError ? t("intake.paused") : err instanceof ApiError ? err.message : t("common.error"));
      if (err instanceof ApiError && err.isLicenseError) await refreshLicense();
    } finally {
      setBusy(false);
    }
  }

  const keys = hints ? hintKeys(hints) : [];
  return (
    <div className="flex min-h-[calc(100vh-3rem)] flex-col bg-black text-white">
      <div className="relative flex-1">
        <video ref={videoRef} className="h-full w-full object-cover" playsInline muted />
        <canvas ref={canvasRef} className="hidden" />
        <div className="pointer-events-none absolute inset-4 rounded-lg border-2 border-white/60" aria-hidden="true" />
        <div className="absolute left-0 right-0 top-2 flex flex-col items-center gap-1 px-4" aria-live="polite">
          {error === "camera" ? <Notice tone="danger">{t("capture.no_camera")}</Notice> : null}
          {keys.length ? (
            keys.map((k) => (
              <span key={k} className="rounded-full bg-amber-500/90 px-3 py-1 text-sm text-black">
                {t(k)}
              </span>
            ))
          ) : hints ? (
            <span className="rounded-full bg-green-600/90 px-3 py-1 text-sm">{t("capture.hint.ok")}</span>
          ) : null}
        </div>
      </div>
      {error && error !== "camera" ? (
        <Notice tone="danger" role="alert" className="m-2">
          {error}
        </Notice>
      ) : null}
      <div className="flex items-center justify-between gap-2 p-3">
        <Link href="/my" className="text-sm underline">
          {t("common.cancel")}
        </Link>
        <Button size="md" className="h-14 w-14 rounded-full" aria-label={t("capture.snap")} onClick={snap} disabled={paused || !!error}>
          ●
        </Button>
        <Button size="sm" variant="secondary" onClick={send} disabled={busy || shots.length === 0 || paused}>
          {t("capture.send", { n: shots.length })}
        </Button>
      </div>
    </div>
  );
}
