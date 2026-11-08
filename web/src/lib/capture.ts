// Live quality hints for camera capture: blur, glare and cut-off edges, computed on a
// small grayscale frame. The measures mirror the server's `quality_hints` so both agree.

export interface FrameHints {
  sharpness: number;
  blurry: boolean;
  glareRatio: number;
  glare: boolean;
  contrast: number;
  lowContrast: boolean;
  cutOff: { top: boolean; bottom: boolean; left: boolean; right: boolean };
  ok: boolean;
}

export const THRESHOLDS = { sharpness: 35, glare: 0.08, contrast: 25, edgeBright: 200, edgeShare: 0.35 } as const;

/** Grayscale (0..255) from RGBA pixels. */
export function toGray(rgba: Uint8ClampedArray, width: number, height: number): Float32Array {
  const out = new Float32Array(width * height);
  for (let i = 0, p = 0; i < out.length; i++, p += 4) {
    out[i] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  }
  return out;
}

function percentile(values: Float32Array, q: number): number {
  const sorted = Float32Array.from(values).sort();
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(q * (sorted.length - 1))));
  return sorted[idx] ?? 0;
}

/** Hints for one grayscale frame. `gray` is row-major, width × height. */
export function assessFrame(gray: Float32Array, width: number, height: number): FrameHints {
  const n = width * height;
  // 3×3 box blur (the server uses a Gaussian σ=1; close enough for a hint), then gradient magnitude
  const smooth = new Float32Array(n);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let sum = 0;
      let count = 0;
      for (let dy = -1; dy <= 1; dy++) {
        const yy = y + dy;
        if (yy < 0 || yy >= height) continue;
        for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx;
          if (xx < 0 || xx >= width) continue;
          sum += gray[yy * width + xx];
          count++;
        }
      }
      smooth[y * width + x] = sum / count;
    }
  }
  const grad = new Float32Array(n);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = y * width + x;
      const gx = (smooth[y * width + Math.min(width - 1, x + 1)] - smooth[y * width + Math.max(0, x - 1)]) / 2;
      const gy = (smooth[Math.min(height - 1, y + 1) * width + x] - smooth[Math.max(0, y - 1) * width + x]) / 2;
      grad[i] = Math.hypot(gx, gy);
    }
  }
  const sharpness = percentile(grad, 0.99);
  let bright = 0;
  let sum = 0;
  let sumSq = 0;
  for (let i = 0; i < n; i++) {
    const v = gray[i];
    if (v > 250) bright++;
    sum += v;
    sumSq += v * v;
  }
  const mean = sum / n;
  const contrast = Math.sqrt(Math.max(0, sumSq / n - mean * mean));
  const glareRatio = bright / n;
  // cut-off: a bright (paper) band touching a border means the document continues outside the frame
  const band = Math.max(1, Math.round(Math.min(width, height) * 0.03));
  const share = (sel: (x: number, y: number) => boolean) => {
    let hits = 0;
    let total = 0;
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (!sel(x, y)) continue;
        total++;
        if (gray[y * width + x] > THRESHOLDS.edgeBright) hits++;
      }
    }
    return total ? hits / total : 0;
  };
  const cutOff = {
    top: share((_x, y) => y < band) > THRESHOLDS.edgeShare,
    bottom: share((_x, y) => y >= height - band) > THRESHOLDS.edgeShare,
    left: share((x) => x < band) > THRESHOLDS.edgeShare,
    right: share((x) => x >= width - band) > THRESHOLDS.edgeShare,
  };
  const blurry = sharpness < THRESHOLDS.sharpness;
  const glare = glareRatio > THRESHOLDS.glare;
  const lowContrast = contrast < THRESHOLDS.contrast;
  return {
    sharpness,
    blurry,
    glareRatio,
    glare,
    contrast,
    lowContrast,
    cutOff,
    ok: !blurry && !glare && !lowContrast && !cutOff.top && !cutOff.bottom && !cutOff.left && !cutOff.right,
  };
}

/** The message keys to show for a frame, worst first; empty when the frame is good. */
export function hintKeys(h: FrameHints): Array<"capture.hint.blurry" | "capture.hint.glare" | "capture.hint.low_contrast" | "capture.hint.cut_off"> {
  const out: Array<"capture.hint.blurry" | "capture.hint.glare" | "capture.hint.low_contrast" | "capture.hint.cut_off"> = [];
  if (h.blurry) out.push("capture.hint.blurry");
  if (h.glare) out.push("capture.hint.glare");
  if (h.cutOff.top || h.cutOff.bottom || h.cutOff.left || h.cutOff.right) out.push("capture.hint.cut_off");
  if (h.lowContrast) out.push("capture.hint.low_contrast");
  return out;
}
