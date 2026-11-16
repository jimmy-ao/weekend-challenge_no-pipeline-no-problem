// Client-side preview of what the render Lambda produces (backend/functions/pipeline/imaging.py).
// Same crop maths and the same banner layout, drawn on a canvas, so the producer can pick the
// focal point and check the overlay before anything is uploaded.

const FONT = { bold: '700', semibold: '600', medium: '500' };
const font = (weight, px) => `${weight} ${px}px Inter, system-ui, sans-serif`;

export function coverBox(srcW, srcH, w, h, fx = 0.5, fy = 0.5) {
  const scale = Math.max(w / srcW, h / srcH);
  const cw = w / scale;
  const ch = h / scale;
  const left = Math.min(Math.max(fx * srcW - cw / 2, 0), srcW - cw);
  const top = Math.min(Math.max(fy * srcH - ch / 2, 0), srcH - ch);
  return { sx: left, sy: top, sw: cw, sh: ch };
}

function fitText(ctx, text, maxWidth) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let lo = 0;
  let hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (ctx.measureText(`${text.slice(0, mid).trimEnd()}…`).width <= maxWidth) lo = mid;
    else hi = mid - 1;
  }
  return lo ? `${text.slice(0, lo).trimEnd()}…` : '';
}

function lineHeight(px) {
  return Math.round(px * 1.21); // Inter ascent + descent
}

// Mirrors FRAME_RATIOS / frame_key_for() in imaging.py: a format uses the frame of its own slot,
// else the uploaded frame with the nearest ratio (stretched to fit).
export const FRAME_RATIOS = { square: 1, portrait: 4 / 5, story: 9 / 16, landscape: 16 / 9 };

export function frameFor(fmt, frames) {
  const shapes = Object.keys(frames || {}).filter((s) => frames[s] && s in FRAME_RATIOS).sort();
  if (!shapes.length) return null;
  if (frames[fmt.frame]) return frames[fmt.frame];
  const ratio = fmt.width / fmt.height;
  const dist = (s) => Math.abs(Math.log(FRAME_RATIOS[s] / ratio));
  return frames[shapes.reduce((best, s) => (dist(s) < dist(best) ? s : best))];
}

// Mirrors banner_box() / BANNER_MAX_HEIGHT in imaging.py: full width, aspect kept, outside the UI zones,
// capped at 35 % of the height (then centred).
export function bannerBox(w, h, bw, bh, position, safeTop = 0, safeBottom = 0) {
  const top = Math.round(h * safeTop);
  const bottom = h - Math.round(h * safeBottom);
  const maxH = Math.min(Math.round(h * 0.35), bottom - top);
  let tw = w;
  let th = Math.max(1, Math.round((bh * w) / bw));
  if (th > maxH) {
    th = Math.max(1, maxH);
    tw = Math.max(1, Math.round((bw * th) / bh));
  }
  return { x: Math.floor((w - tw) / 2), y: position === 'top' ? top : bottom - th, w: tw, h: th };
}

function drawCustomBanner(ctx, w, h, banner, position, safeTop, safeBottom) {
  const b = bannerBox(w, h, banner.width, banner.height, position === 'top' ? 'top' : 'bottom', safeTop, safeBottom);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(banner, b.x, b.y, b.w, b.h);
}

function drawOverlay(ctx, w, h, ev, logo, safeTop, safeBottom) {
  const u = Math.min(Math.sqrt(w * h), w * 1.1) / 100;
  const margin = Math.round(4 * u);
  const padX = Math.round(2.6 * u);
  const padY = Math.round(2.0 * u);
  const gap = Math.round(0.7 * u);
  const stripe = Math.max(3, Math.round(0.7 * u));
  const radius = Math.round(1.4 * u);
  const nameSize = Math.max(12, Math.round(4.4 * u));
  const metaSize = Math.max(10, Math.round(2.5 * u));
  const position = ev.position === 'top' ? 'top' : 'bottom';

  // scrim
  const band = Math.round(h * 0.42);
  const grad = position === 'bottom' ? ctx.createLinearGradient(0, h - band, 0, h) : ctx.createLinearGradient(0, band, 0, 0);
  for (let i = 0; i <= 10; i += 1) grad.addColorStop(i / 10, `rgba(0,0,0,${((150 / 255) * (i / 10) ** 1.6).toFixed(3)})`);
  ctx.fillStyle = grad;
  ctx.fillRect(0, position === 'bottom' ? h - band : 0, w, band);

  const meta = [ev.dateLabel, ev.location].map((s) => (s || '').trim()).filter(Boolean).join('  ·  ');
  let tag = (ev.hashtag || '').trim();
  if (tag && !tag.startsWith('#')) tag = `#${tag}`;
  const lines = [];
  if ((ev.name || '').trim()) lines.push({ text: ev.name.trim(), weight: FONT.bold, size: nameSize, color: ev.textColor });
  if (meta) lines.push({ text: meta, weight: FONT.medium, size: metaSize, color: ev.textColor });
  if (tag) lines.push({ text: tag, weight: FONT.semibold, size: metaSize, color: ev.accentColor });
  if (!lines.length && !logo) return;

  const textHeight = () => lines.reduce((s, l) => s + lineHeight(l.size), 0) + gap * Math.max(lines.length - 1, 0);
  let contentH = Math.max(textHeight(), logo ? Math.round(9 * u) : 0);

  let logoW = 0;
  let logoBox = null;
  if (logo) {
    let lh = contentH;
    let lw = Math.round((logo.width * lh) / logo.height);
    const maxLw = Math.round(w * 0.28);
    if (lw > maxLw) { lh = Math.round((logo.height * maxLw) / logo.width); lw = maxLw; }
    logoBox = { w: lw, h: lh };
    logoW = lw + Math.round(2.2 * u);
  }

  const maxTextW = w - 2 * margin - stripe - 2 * padX - logoW;
  if (lines[0] && lines[0].weight === FONT.bold) {
    ctx.font = font(FONT.bold, lines[0].size);
    while (lines[0].size > nameSize * 0.7 && ctx.measureText(lines[0].text).width > maxTextW) {
      lines[0].size -= 1;
      ctx.font = font(FONT.bold, lines[0].size);
    }
    contentH = Math.max(textHeight(), logoBox ? logoBox.h : 0);
  }
  let textW = 0;
  for (const l of lines) {
    ctx.font = font(l.weight, l.size);
    l.text = fitText(ctx, l.text, maxTextW);
    textW = Math.max(textW, ctx.measureText(l.text).width);
  }

  const panelW = Math.round(stripe + 2 * padX + logoW + textW);
  const panelH = Math.round(contentH + 2 * padY);
  const x0 = margin;
  const y0 = position === 'top' ? Math.round(h * safeTop) + margin : h - Math.round(h * safeBottom) - margin - panelH;

  ctx.save();
  ctx.beginPath();
  ctx.roundRect(x0, y0, panelW, panelH, radius);
  ctx.clip();
  ctx.globalAlpha = 235 / 255;
  ctx.fillStyle = ev.primaryColor;
  ctx.fillRect(x0, y0, panelW, panelH);
  ctx.globalAlpha = 1;
  ctx.fillStyle = ev.accentColor;
  ctx.fillRect(x0, y0, stripe, panelH);
  ctx.restore();

  let cx = x0 + stripe + padX;
  if (logo) {
    ctx.drawImage(logo, cx, y0 + padY + (contentH - logoBox.h) / 2, logoBox.w, logoBox.h);
    cx += logoW;
  }
  ctx.textBaseline = 'top';
  let ty = y0 + padY + Math.floor((contentH - textHeight()) / 2);
  for (const l of lines) {
    ctx.font = font(l.weight, l.size);
    ctx.fillStyle = l.color;
    ctx.fillText(l.text, cx, ty + Math.round(l.size * 0.03));
    ty += lineHeight(l.size) + gap;
  }
}

/**
 * Draw one format preview into `canvas` (sized to the real output, scaled down by CSS).
 * photo: ImageBitmap | null, ev: event or null,
 * images: { logo, banner, frames: { shape: bitmap } }; like in the pipeline, a frame replaces the banners
 * and a custom banner replaces the generated one.
 */
export function renderPreview(canvas, fmt, photo, ev, images = {}, focus = { x: 0.5, y: 0.5 }) {
  const { logo = null, banner = null, frames = {} } = images;
  const frame = ev ? frameFor(fmt, frames) : null;
  const scale = Math.min(1, 720 / Math.max(fmt.width, fmt.height)); // keep previews light
  const w = Math.round(fmt.width * scale);
  const h = Math.round(fmt.height * scale);
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#dde6ef';
  ctx.fillRect(0, 0, w, h);
  if (photo) {
    const b = coverBox(photo.width, photo.height, w, h, focus.x, focus.y);
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(photo, b.sx, b.sy, b.sw, b.sh, 0, 0, w, h);
  }
  if (frame) {
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(frame, 0, 0, w, h);
  } else if (ev && banner) {
    drawCustomBanner(ctx, w, h, banner, ev.position, fmt.safeTop || 0, fmt.safeBottom || 0);
  } else if (ev) {
    drawOverlay(ctx, w, h, {
      primaryColor: '#111827', accentColor: '#F59E0B', textColor: '#FFFFFF', ...ev,
    }, logo, fmt.safeTop || 0, fmt.safeBottom || 0);
  }
}
