import { describe, expect, it } from "vitest";

import { assessFrame, hintKeys, toGray } from "./capture";

function frame(width: number, height: number, fill: (x: number, y: number) => number): Float32Array {
  const g = new Float32Array(width * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) g[y * width + x] = fill(x, y);
  return g;
}

describe("capture quality hints", () => {
  it("flags a flat, blurry, low-contrast frame", () => {
    const h = assessFrame(frame(64, 64, () => 120), 64, 64);
    expect(h.blurry).toBe(true);
    expect(h.lowContrast).toBe(true);
    expect(h.glare).toBe(false);
    expect(hintKeys(h)).toEqual(["capture.hint.blurry", "capture.hint.low_contrast"]);
  });
  it("accepts a sharp document with dark text on a centred white page", () => {
    // white page inset by 8 px, black 'text' stripes inside it, dark table around
    const h = assessFrame(
      frame(96, 96, (x, y) => {
        if (x < 8 || x >= 88 || y < 8 || y >= 88) return 40;
        return y % 8 < 2 && x > 16 && x < 80 ? 0 : 245;
      }),
      96,
      96,
    );
    expect(h.blurry).toBe(false);
    expect(h.glare).toBe(false);
    expect(h.cutOff).toEqual({ top: false, bottom: false, left: false, right: false });
    expect(h.ok).toBe(true);
  });
  it("detects glare and a page running off the right edge", () => {
    const h = assessFrame(
      frame(96, 96, (x, y) => {
        if (x > 30 && x < 70 && y > 30 && y < 70) return 255; // reflection (17 % of the frame)
        if (x >= 30) return y % 6 < 2 ? 0 : 240; // page touches the right border
        return 30;
      }),
      96,
      96,
    );
    expect(h.glare).toBe(true);
    expect(h.cutOff.right).toBe(true);
    expect(h.cutOff.left).toBe(false);
    expect(hintKeys(h)).toContain("capture.hint.cut_off");
    expect(h.ok).toBe(false);
  });
  it("converts RGBA to gray", () => {
    const rgba = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]);
    const g = toGray(rgba, 2, 1);
    expect(Math.round(g[0])).toBe(255);
    expect(g[1]).toBe(0);
  });
});
