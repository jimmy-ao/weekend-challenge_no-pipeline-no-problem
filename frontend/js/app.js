import * as auth from './auth.js';
import { mountLogin } from './login.js';
import { api, uploadToS3 } from './api.js';
import { renderPreview, frameFor, FRAME_RATIOS } from './preview.js';

const $ = (id) => document.getElementById(id);
const NO_EVENT = '';

const state = {
  formats: [],
  maxUploadBytes: 25 * 1024 * 1024,
  retentionDays: 7,
  events: [],
  eventId: NO_EVENT,
  images: new Map(), // S3 key of a logo / banner / frame -> ImageBitmap
  photo: null, // { file, bitmap, url }
  focus: { x: 0.5, y: 0.5 },
  selected: new Set(),
  busy: false,
  editing: null, // event being edited in the dialog (null = new)
  draftLogo: null, // ImageBitmap picked in the dialog
  draftBanner: null, // custom banner ImageBitmap picked in the dialog
  frameDraft: {}, // shape -> { file, bitmap, url } (new file) | { removed: true }
};

const MAX_LOGO_BYTES = 2 * 1024 * 1024;
const MAX_BANNER_BYTES = 5 * 1024 * 1024;
const MAX_FRAME_BYTES = 8 * 1024 * 1024;
const FRAME_SLOTS = [
  { shape: 'square', label: 'Square 1:1', size: '1080×1080', uses: 'Instagram post' },
  { shape: 'portrait', label: 'Portrait 4:5', size: '1080×1350', uses: 'Instagram portrait' },
  { shape: 'story', label: 'Story 9:16', size: '1080×1920', uses: 'Stories, Reels, TikTok' },
  { shape: 'landscape', label: 'Landscape 16:9', size: '1600×900', uses: 'LinkedIn, X, Facebook, Bluesky' },
];

// ------------------------------------------------------------------ helpers
function toast(message, kind = 'info') {
  const el = $('toast');
  el.textContent = message;
  el.dataset.kind = kind;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, kind === 'error' ? 7000 : 3500);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'style') Object.assign(node.style, v); // CSSOM, allowed by the CSP (inline style attributes are not)
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
  }
  children.flat().forEach((c) => c != null && node.append(c));
  return node;
}

const currentEvent = () => state.events.find((e) => e.eventId === state.eventId) || null;
const fmtBytes = (n) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`);
const fmtDate = (s) => new Date(s * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });

function errorText(raw) {
  if (!raw) return 'Something went wrong.';
  try { return JSON.parse(raw).errorMessage || raw; } catch { return raw; }
}

// A stored event image as an ImageBitmap (cached by S3 key), or null.
async function imageAt(key, url) {
  if (!key || !url) return null;
  if (state.images.has(key)) return state.images.get(key);
  try {
    const bmp = await createImageBitmap(await (await fetch(url)).blob());
    state.images.set(key, bmp);
    return bmp;
  } catch {
    return null;
  }
}

const imageFor = (ev, kind) => imageAt(ev?.[`${kind}Key`], ev?.[`${kind}Url`]); // kind: 'logo' | 'banner'

async function framesFor(ev) {
  const frames = {};
  await Promise.all(Object.entries(ev?.frameKeys || {}).map(async ([shape, key]) => {
    frames[shape] = await imageAt(key, ev.frameUrls?.[shape]);
  }));
  return frames;
}

async function imagesFor(ev) {
  const [logo, banner, frames] = await Promise.all([imageFor(ev, 'logo'), imageFor(ev, 'banner'), framesFor(ev)]);
  return { logo, banner, frames };
}

// ------------------------------------------------------------------ events
function renderEventSelect() {
  const sel = $('event-select');
  sel.replaceChildren(
    el('option', { value: NO_EVENT, text: 'No overlay (crop only)' }),
    ...state.events.map((e) => el('option', { value: e.eventId, text: e.name })),
  );
  sel.value = state.eventId;
  $('edit-event-btn').disabled = !state.eventId;
}

async function loadEvents(selectId) {
  const { events } = await api('GET', '/events');
  state.events = events;
  if (selectId !== undefined) state.eventId = selectId;
  if (!state.events.some((e) => e.eventId === state.eventId)) state.eventId = state.events[0]?.eventId || NO_EVENT;
  renderEventSelect();
  await renderPreviews();
}

function readEventForm() {
  const f = $('event-form').elements;
  return {
    name: f.name.value.trim(),
    dateLabel: f.dateLabel.value.trim(),
    location: f.location.value.trim(),
    hashtag: f.hashtag.value.trim(),
    position: f.position.value,
    primaryColor: f.primaryColor.value,
    accentColor: f.accentColor.value,
    textColor: f.textColor.value,
  };
}

const bannerMode = () => $('event-form').elements.bannerMode.value;

function syncBannerMode() {
  $('event-fields').dataset.mode = bannerMode();
}

// Frame slots as they will be after saving: shape -> { source: 'new' | 'saved', url, name }.
function effectiveFrames() {
  const out = {};
  for (const { shape } of FRAME_SLOTS) {
    const d = state.frameDraft[shape];
    if (d?.file) out[shape] = { source: 'new', url: d.url, name: d.file.name };
    else if (!d?.removed && state.editing?.frameKeys?.[shape]) out[shape] = { source: 'saved', url: state.editing.frameUrls?.[shape], name: 'Saved frame' };
  }
  return out;
}

function renderFrameSlots() {
  const frames = effectiveFrames();
  const available = Object.fromEntries(Object.keys(frames).map((s) => [s, s]));
  $('frame-slots').replaceChildren(...FRAME_SLOTS.map((slot) => {
    const own = frames[slot.shape];
    const borrowed = own ? null : frameFor({ frame: slot.shape, width: FRAME_RATIOS[slot.shape], height: 1 }, available);
    const status = own
      ? (own.source === 'new' ? `New: ${own.name}` : 'Saved frame')
      : borrowed ? `Borrows the ${borrowed} frame, stretched` : 'No frame';
    const input = el('input', { type: 'file', accept: 'image/png,image/webp,image/jpeg', onchange: (e) => pickFrame(slot.shape, e.target.files[0]) });
    return el('div', { class: `frame-slot${own ? ' frame-slot--set' : ''}` },
      own?.url ? el('img', { class: 'frame-slot__thumb', src: own.url, alt: '' }) : el('span', { class: 'frame-slot__thumb' }),
      el('span', { class: 'frame-slot__text' },
        el('strong', { text: slot.label }),
        el('small', { text: `${slot.size} · ${slot.uses}` }),
        el('span', { class: `frame-slot__status${borrowed ? ' frame-slot__status--warn' : ''}`, text: status })),
      el('span', { class: 'frame-slot__actions' },
        el('label', { class: 'btn btn--ghost btn--small' }, own ? 'Replace' : 'Choose', input),
        own ? el('button', { class: 'btn btn--ghost btn--small', type: 'button', text: 'Remove', onclick: () => removeFrame(slot.shape) }) : null),
    );
  }));
}

async function pickFrame(shape, file) {
  if (!file) return;
  if (file.size > MAX_FRAME_BYTES) {
    toast('Frame images must be 8 MB or less.', 'error');
    return;
  }
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) {
    toast('That frame image could not be read.', 'error');
    return;
  }
  if (state.frameDraft[shape]?.url) URL.revokeObjectURL(state.frameDraft[shape].url);
  state.frameDraft[shape] = { file, bitmap, url: URL.createObjectURL(file) };
  renderFrameSlots();
  drawEventPreview();
}

function removeFrame(shape) {
  if (state.frameDraft[shape]?.url) URL.revokeObjectURL(state.frameDraft[shape].url);
  state.frameDraft[shape] = { removed: true };
  renderFrameSlots();
  drawEventPreview();
}

async function draftFrameBitmaps() {
  const saved = state.editing ? await framesFor(state.editing) : {};
  const frames = {};
  for (const { shape } of FRAME_SLOTS) {
    const d = state.frameDraft[shape];
    if (d?.bitmap) frames[shape] = d.bitmap;
    else if (!d?.removed && saved[shape]) frames[shape] = saved[shape];
  }
  return frames;
}

async function drawEventPreview() {
  syncBannerMode();
  const mode = bannerMode();
  const draft = { ...readEventForm(), name: readEventForm().name || 'Your event name' };
  const images = { logo: null, banner: null, frames: {} };
  if (mode === 'banner') {
    images.banner = state.draftBanner || (state.editing ? await imageFor(state.editing, 'banner') : null);
  } else if (mode === 'frame') {
    images.frames = await draftFrameBitmaps();
  } else {
    images.logo = state.draftLogo;
    if (!images.logo && state.editing && !$('event-form').elements.removeLogo.checked) images.logo = await imageFor(state.editing, 'logo');
  }
  const photo = state.photo?.bitmap || null;
  $('event-preview').hidden = mode === 'frame';
  $('frame-previews').hidden = mode !== 'frame';
  if (mode === 'frame') {
    // One preview per slot, using the first output format of that shape.
    $('frame-previews').replaceChildren(...FRAME_SLOTS.map(({ shape, label }) => {
      const fmt = state.formats.find((f) => f.frame === shape);
      if (!fmt) return null;
      const canvas = el('canvas', { 'aria-label': `${label} preview` });
      renderPreview(canvas, fmt, photo, draft, images, state.focus);
      return el('figure', {}, canvas, el('figcaption', { text: label }));
    }));
    return;
  }
  const fmt = state.formats.find((f) => f.id === 'instagram-square') || state.formats[0];
  renderPreview($('event-preview'), fmt, photo, draft, images, state.focus);
}

function openEventDialog(ev) {
  state.editing = ev;
  state.draftLogo = null;
  state.draftBanner = null;
  Object.values(state.frameDraft).forEach((d) => d.url && URL.revokeObjectURL(d.url));
  state.frameDraft = {};
  const form = $('event-form');
  form.reset();
  const f = form.elements;
  $('event-dialog-title').textContent = ev ? 'Edit event' : 'New event';
  $('delete-event-btn').hidden = !ev;
  $('remove-logo-wrap').hidden = !ev?.logoKey;
  $('banner-current').hidden = !ev?.bannerKey;
  f.bannerMode.value = Object.keys(ev?.frameKeys || {}).length ? 'frame' : ev?.bannerKey ? 'banner' : 'generated';
  renderFrameSlots();
  if (ev) {
    for (const k of ['name', 'dateLabel', 'location', 'hashtag', 'position']) f[k].value = ev[k] || '';
    f.position.value = ev.position || 'bottom';
    for (const k of ['primaryColor', 'accentColor', 'textColor']) f[k].value = (ev[k] || f[k].defaultValue).toLowerCase();
  }
  $('event-dialog').showModal();
  drawEventPreview();
}

async function saveEvent() {
  const form = $('event-form');
  const f = form.elements;
  const data = readEventForm();
  const mode = bannerMode(); // 'generated' | 'banner' | 'frame': only the chosen style's files are kept
  const logoFile = mode === 'generated' ? f.logo.files[0] : null;
  const bannerFile = mode === 'banner' ? f.banner.files[0] : null;
  const frameFiles = mode === 'frame'
    ? Object.entries(state.frameDraft).filter(([, d]) => d.file).map(([shape, d]) => ({ shape, file: d.file }))
    : [];
  if (logoFile && logoFile.size > MAX_LOGO_BYTES) throw new Error('Logo must be 2 MB or less');
  if (bannerFile && bannerFile.size > MAX_BANNER_BYTES) throw new Error('Banner image must be 5 MB or less');
  if (mode === 'banner' && !bannerFile && !state.editing?.bannerKey) throw new Error('Choose a banner image, or pick another banner style.');
  if (mode === 'frame' && !Object.keys(effectiveFrames()).length) throw new Error('Add at least one frame image, or pick another banner style.');

  // Saved frames that stay (removed ones are dropped now, replaced ones once the new file is up).
  const keptFrames = Object.fromEntries(Object.entries(state.editing?.frameKeys || {})
    .filter(([shape]) => mode === 'frame' && !state.frameDraft[shape]?.removed));
  const changes = {};
  if (state.editing && f.removeLogo.checked && mode === 'generated') changes.logoKey = '';
  if (state.editing?.bannerKey && mode !== 'banner') changes.bannerKey = '';
  if (state.editing && Object.keys(state.editing.frameKeys || {}).length) changes.frameKeys = keptFrames;
  let ev = state.editing
    ? await api('PUT', `/events/${state.editing.eventId}`, { ...data, ...changes })
    : await api('POST', '/events', data);

  const uploads = [
    ...(logoFile ? [{ kind: 'logo', file: logoFile }] : []),
    ...(bannerFile ? [{ kind: 'banner', file: bannerFile }] : []),
    ...frameFiles.map((x) => ({ kind: 'frame', ...x })),
  ];
  const keys = await Promise.all(uploads.map(async ({ kind, file, shape }) => {
    const { key, upload } = await api('POST', `/events/${ev.eventId}/logo`, { contentType: file.type, kind, ...(shape ? { shape } : {}) });
    await uploadToS3(upload, file);
    return { kind, shape, key };
  }));
  if (keys.length) {
    const update = { ...data };
    for (const { kind, shape, key } of keys) {
      if (kind === 'frame') update.frameKeys = { ...(update.frameKeys || keptFrames), [shape]: key };
      else update[`${kind}Key`] = key;
    }
    ev = await api('PUT', `/events/${ev.eventId}`, update);
  }
  return ev;
}

// ------------------------------------------------------------------ photo + focus
async function setPhoto(file) {
  if (!file) return;
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    toast('Please choose a JPEG, PNG or WebP photo.', 'error');
    return;
  }
  if (file.size > state.maxUploadBytes) {
    toast(`That photo is ${fmtBytes(file.size)}; the limit is ${fmtBytes(state.maxUploadBytes)}.`, 'error');
    return;
  }
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch {
    toast('This browser cannot read that photo.', 'error');
    return;
  }
  if (state.photo) URL.revokeObjectURL(state.photo.url);
  state.photo = { file, bitmap, url: URL.createObjectURL(file) };
  state.focus = { x: 0.5, y: 0.5 };
  $('focus-img').src = state.photo.url;
  $('dropzone-empty').hidden = true;
  $('focus').hidden = false;
  $('focus-hint').hidden = false;
  $('dropzone').classList.add('dropzone--filled');
  placeMarker();
  updateGenerate();
  await renderPreviews();
}

function placeMarker() {
  const m = $('focus-marker');
  m.style.left = `${state.focus.x * 100}%`;
  m.style.top = `${state.focus.y * 100}%`;
}

// ------------------------------------------------------------------ formats + previews
function renderChips() {
  $('format-chips').replaceChildren(...state.formats.map((f) => el('label', { class: 'chip', title: f.network },
    el('input', {
      type: 'checkbox',
      value: f.id,
      checked: state.selected.has(f.id),
      onchange: (e) => {
        if (e.target.checked) state.selected.add(f.id); else state.selected.delete(f.id);
        updateGenerate();
        renderPreviews();
      },
    }),
    el('span', { class: 'chip__ratio', style: { aspectRatio: `${f.width} / ${f.height}` } }),
    el('span', { class: 'chip__text' }, el('strong', { text: f.label }), el('small', { text: `${f.width}×${f.height}` })),
  )));
}

let previewToken = 0;
async function renderPreviews() {
  const token = ++previewToken;
  const ev = currentEvent();
  const images = await imagesFor(ev);
  if (token !== previewToken) return;
  const grid = $('preview-grid');
  const fmts = state.formats.filter((f) => state.selected.has(f.id));
  if (!fmts.length) {
    grid.replaceChildren(el('p', { class: 'empty', text: 'Pick at least one format.' }));
    return;
  }
  grid.replaceChildren(...fmts.map((f) => {
    const canvas = el('canvas', { 'aria-label': `${f.label} preview` });
    renderPreview(canvas, f, state.photo?.bitmap || null, ev, images, state.focus);
    return el('figure', { class: 'preview' }, canvas, el('figcaption', {}, el('strong', { text: f.label }), el('span', { text: `${f.width}×${f.height}` })));
  }));
}

function updateGenerate() {
  $('generate-btn').disabled = state.busy || !state.photo || state.selected.size === 0;
}

// ------------------------------------------------------------------ jobs
function setProgress(fraction, label) {
  $('progress').hidden = false;
  $('progress-fill').style.width = `${Math.round(fraction * 100)}%`;
  $('progress-label').textContent = label;
}

async function generate() {
  if (!state.photo) return;
  state.busy = true;
  updateGenerate();
  $('results').hidden = true;
  try {
    setProgress(0.02, 'Creating the batch…');
    const job = await api('POST', '/jobs', {
      eventId: state.eventId || '',
      formats: [...state.selected],
      focusX: state.focus.x,
      focusY: state.focus.y,
      fileName: state.photo.file.name,
      contentType: state.photo.file.type,
      size: state.photo.file.size,
    });
    await uploadToS3(job.upload, state.photo.file, (p) => setProgress(0.05 + p * 0.45, `Uploading… ${Math.round(p * 100)}%`));
    setProgress(0.55, 'Rendering every format…');
    const done = await waitForJob(job.jobId);
    setProgress(1, `Done: ${done.outputs.length} photos ready.`);
    showResults(done);
    loadHistory();
  } catch (err) {
    setProgress(1, '');
    $('progress').hidden = true;
    if (sessionLost(err)) backToLogin();
    else toast(err.message, 'error');
  } finally {
    state.busy = false;
    updateGenerate();
  }
}

async function waitForJob(jobId) {
  const started = Date.now();
  let delay = 800;
  while (Date.now() - started < 180000) {
    await new Promise((r) => setTimeout(r, delay));
    const job = await api('GET', `/jobs/${jobId}`);
    if (job.status === 'DONE') return job;
    if (job.status === 'FAILED') throw new Error(errorText(job.error));
    const t = (Date.now() - started) / 15000;
    setProgress(0.55 + 0.4 * (1 - Math.exp(-t)), job.status === 'PROCESSING' ? 'Rendering every format…' : 'Waiting for the pipeline to start…');
    delay = Math.min(delay * 1.3, 2500);
  }
  throw new Error('This is taking longer than expected. Check "Recent batches" in a minute.');
}

function showResults(job) {
  const byId = Object.fromEntries(state.formats.map((f) => [f.id, f]));
  $('result-grid').replaceChildren(...job.outputs.map((o) => el('figure', { class: 'result' },
    el('a', { href: o.url, target: '_blank', rel: 'noopener' }, el('img', { src: o.url, alt: byId[o.format]?.label || o.format, loading: 'lazy', width: o.width, height: o.height })),
    el('figcaption', {},
      el('span', {}, el('strong', { text: byId[o.format]?.label || o.format }), el('small', { text: `${o.width}×${o.height} · ${fmtBytes(o.bytes)}` })),
      el('a', { class: 'btn btn--ghost btn--small', href: o.url, download: `${o.format}.jpg`, text: 'Download' }),
    ),
  )));
  $('zip-link').href = job.zipUrl;
  $('results').hidden = false;
  $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function loadHistory() {
  const { jobs } = await api('GET', '/jobs');
  $('history').hidden = jobs.length === 0;
  $('retention-hint').textContent = `Batches are deleted automatically after ${state.retentionDays} days.`;
  $('history-list').replaceChildren(...jobs.map((j) => el('li', { class: `job job--${j.status.toLowerCase()}` },
    j.thumbnailUrl ? el('img', { src: j.thumbnailUrl, alt: '', loading: 'lazy' }) : el('span', { class: 'job__thumb' }),
    el('span', { class: 'job__meta' },
      el('strong', { text: j.eventName || 'No overlay' }),
      el('small', { text: `${j.originalName || 'photo'} · ${fmtDate(j.createdAt)} · ${j.formats.length} formats` }),
      j.status === 'FAILED' ? el('small', { class: 'job__error', text: errorText(j.error) }) : null,
    ),
    el('span', { class: 'badge', text: j.status.replace('_', ' ').toLowerCase() }),
    el('span', { class: 'job__actions' },
      j.status === 'DONE' ? el('button', { class: 'btn btn--ghost btn--small', type: 'button', text: 'Open', onclick: async () => showResults(await api('GET', `/jobs/${j.jobId}`)) }) : null,
      j.status !== 'PROCESSING' ? el('button', {
        class: 'btn btn--ghost btn--small', type: 'button', text: 'Delete',
        onclick: async () => {
          if (!confirm('Delete this batch and its photos?')) return;
          await api('DELETE', `/jobs/${j.jobId}`);
          loadHistory();
        },
      }) : null,
    ),
  )));
}

// ------------------------------------------------------------------ wiring
function wire() {
  $('logout-btn').addEventListener('click', async () => {
    await auth.signOut();
    location.reload();
  });

  $('event-select').addEventListener('change', (e) => {
    state.eventId = e.target.value;
    $('edit-event-btn').disabled = !state.eventId;
    renderPreviews();
  });
  $('new-event-btn').addEventListener('click', () => openEventDialog(null));
  $('edit-event-btn').addEventListener('click', () => openEventDialog(currentEvent()));

  const form = $('event-form');
  form.addEventListener('input', () => drawEventPreview());
  $('logo-input').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    state.draftLogo = file ? await createImageBitmap(file).catch(() => null) : null;
    drawEventPreview();
  });
  $('banner-input').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    state.draftBanner = file ? await createImageBitmap(file).catch(() => null) : null;
    if (file && !state.draftBanner) toast('That banner image could not be read.', 'error');
    drawEventPreview();
  });
  form.addEventListener('submit', async (e) => {
    if (e.submitter?.value !== 'save') return; // cancel / close
    e.preventDefault();
    if (!form.reportValidity()) return;
    const btn = $('save-event-btn');
    btn.disabled = true;
    try {
      const ev = await saveEvent();
      $('event-dialog').close();
      await loadEvents(ev.eventId);
      toast('Event saved.');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });
  $('delete-event-btn').addEventListener('click', async () => {
    if (!state.editing || !confirm(`Delete "${state.editing.name}"? Past batches keep their photos.`)) return;
    try {
      await api('DELETE', `/events/${state.editing.eventId}`);
      $('event-dialog').close();
      await loadEvents(NO_EVENT);
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  const dz = $('dropzone');
  $('photo-input').addEventListener('change', (e) => setPhoto(e.target.files[0]));
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dropzone--over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dropzone--over'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('dropzone--over');
    setPhoto(e.dataTransfer.files[0]);
  });
  $('focus').addEventListener('click', (e) => {
    e.preventDefault(); // the dropzone is a <label>: do not reopen the file picker
    const r = $('focus-img').getBoundingClientRect();
    state.focus = {
      x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1),
      y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1),
    };
    placeMarker();
    renderPreviews();
  });
  $('change-photo').addEventListener('click', () => $('photo-input').click());
  $('generate-btn').addEventListener('click', generate);
}

const sessionLost = (err) => err.status === 401 || err.code === 'NoSession';

async function backToLogin() {
  await auth.signOut();
  location.reload();
}

async function startApp(user) {
  $('hero').hidden = true;
  $('user-email').textContent = user.email;
  $('user-box').hidden = false;
  $('workspace').hidden = false;
  wire();

  try {
    await document.fonts.load('700 20px Inter');
    const meta = await api('GET', '/formats');
    state.formats = meta.formats;
    state.maxUploadBytes = meta.maxUploadBytes;
    state.retentionDays = meta.retentionDays;
    $('max-mb').textContent = Math.round(meta.maxUploadBytes / 1024 / 1024);
    state.selected = new Set(state.formats.map((f) => f.id));
    renderChips();
    await Promise.all([loadEvents(), loadHistory()]);
  } catch (err) {
    if (sessionLost(err)) backToLogin();
    else toast(err.message, 'error');
  }
}

function main() {
  if (!window.CROPAGANDA_CONFIG) {
    document.body.prepend(el('p', { class: 'banner-error', text: 'config.js is missing: deploy with `make deploy`, or run `make dev` for local work.' }));
    return;
  }
  const user = auth.user();
  if (user) {
    startApp(user);
    return;
  }
  $('hero').hidden = false;
  mountLogin((signedIn) => startApp(signedIn));
}

main();
