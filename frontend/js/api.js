import { idToken } from './auth.js';

const base = () => window.CROPAGANDA_CONFIG.apiUrl;

export async function api(method, path, body) {
  const res = await fetch(`${base()}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${await idToken()}`,
      ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.message || `request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

/** Upload a file with an S3 presigned POST ({url, fields}), reporting progress 0..1. */
export function uploadToS3(post, file, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    Object.entries(post.fields).forEach(([k, v]) => form.append(k, v));
    form.append('file', file); // must be the last field
    const xhr = new XMLHttpRequest();
    xhr.open('POST', post.url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error('upload failed (network)'));
    xhr.send(form);
  });
}
