// Sign-in against the Cognito user pool API directly, from the app's own login page.
// No SDK and no Hosted UI: a few JSON calls to https://cognito-idp.<region>.amazonaws.com/.
//
//   signIn(email, password)         -> { user } or { challenge: 'NEW_PASSWORD_REQUIRED' }
//   completeNewPassword(password)   -> { user }   (first sign-in with the invitation password)
//   forgotPassword(email)           -> sends a code by email
//   confirmForgotPassword(email, code, password)
//   signOut()                       -> revokes the refresh token, clears the session

const cfg = () => window.CROPAGANDA_CONFIG;
const KEY = 'cropaganda.tokens';
const memory = {}; // fallback when sessionStorage is unavailable (some private modes)

const store = {
  get() {
    if (memory.tokens) return memory.tokens;
    try { const raw = sessionStorage.getItem(KEY); return raw ? (memory.tokens = JSON.parse(raw)) : null; } catch { return null; }
  },
  set(tokens) {
    memory.tokens = tokens;
    try { sessionStorage.setItem(KEY, JSON.stringify(tokens)); } catch { /* memory only */ }
  },
  clear() {
    memory.tokens = null;
    try { sessionStorage.removeItem(KEY); } catch { /* ignore */ }
  },
};

/** Error with Cognito's exception name in `code` and a message fit for the login page. */
export class AuthError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const FRIENDLY = {
  NotAuthorizedException: 'Incorrect email or password.',
  UserNotFoundException: 'Incorrect email or password.',
  PasswordResetRequiredException: 'You need to reset your password. Use "Forgot password?".',
  UserNotConfirmedException: 'This account is not confirmed yet. Ask your organiser.',
  CodeMismatchException: 'That code is not right. Check the email and try again.',
  ExpiredCodeException: 'That code has expired. Ask for a new one.',
  InvalidPasswordException: 'That password does not meet the rules below.',
  LimitExceededException: 'Too many attempts. Wait a few minutes and try again.',
  TooManyRequestsException: 'Too many attempts. Wait a few minutes and try again.',
  TooManyFailedAttemptsException: 'Too many attempts. Wait a few minutes and try again.',
};

async function cognito(action, body) {
  let res;
  try {
    res = await fetch(`https://cognito-idp.${cfg().region}.amazonaws.com/`, {
      method: 'POST',
      headers: {
        'content-type': 'application/x-amz-json-1.1',
        'x-amz-target': `AWSCognitoIdentityProviderService.${action}`,
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new AuthError('NetworkError', 'Cannot reach the sign-in service. Check your connection.');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const code = (data.__type || '').split('#').pop() || `HTTP${res.status}`;
    // A temporary password that expired is reported as NotAuthorizedException with this text.
    const expiredTemp = code === 'NotAuthorizedException' && /temporary password has expired/i.test(data.message || '');
    throw new AuthError(code, expiredTemp
      ? 'Your invitation password has expired. Ask your organiser to resend the invitation.'
      : FRIENDLY[code] || data.message || 'Something went wrong. Try again.');
  }
  return data;
}

function decodeJwt(token) {
  const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
  const json = decodeURIComponent([...atob(part)].map((c) => `%${c.charCodeAt(0).toString(16).padStart(2, '0')}`).join(''));
  return JSON.parse(json);
}

function saveTokens(result, refreshToken) {
  store.set({
    idToken: result.IdToken,
    refreshToken: result.RefreshToken || refreshToken,
    exp: decodeJwt(result.IdToken).exp,
  });
}

let pending = null; // { email, session } while a NEW_PASSWORD_REQUIRED challenge is open

export async function signIn(email, password) {
  const data = await cognito('InitiateAuth', {
    AuthFlow: 'USER_PASSWORD_AUTH',
    ClientId: cfg().cognitoClientId,
    AuthParameters: { USERNAME: email.trim(), PASSWORD: password },
  });
  if (data.ChallengeName === 'NEW_PASSWORD_REQUIRED') {
    pending = { email: email.trim(), session: data.Session };
    return { challenge: 'NEW_PASSWORD_REQUIRED' };
  }
  if (data.ChallengeName) throw new AuthError(data.ChallengeName, 'This sign-in step is not supported by Cropaganda yet.');
  saveTokens(data.AuthenticationResult);
  return { user: user() };
}

export async function completeNewPassword(newPassword) {
  if (!pending) throw new AuthError('NoSession', 'Your sign-in session expired. Sign in again.');
  try {
    const data = await cognito('RespondToAuthChallenge', {
      ClientId: cfg().cognitoClientId,
      ChallengeName: 'NEW_PASSWORD_REQUIRED',
      Session: pending.session,
      ChallengeResponses: { USERNAME: pending.email, NEW_PASSWORD: newPassword },
    });
    saveTokens(data.AuthenticationResult);
    pending = null;
    return { user: user() };
  } catch (err) {
    if (err.code === 'NotAuthorizedException') {
      pending = null;
      throw new AuthError('NoSession', 'Your sign-in session expired. Sign in again.');
    }
    throw err;
  }
}

export async function forgotPassword(email) {
  await cognito('ForgotPassword', { ClientId: cfg().cognitoClientId, Username: email.trim() });
}

export async function confirmForgotPassword(email, code, newPassword) {
  await cognito('ConfirmForgotPassword', {
    ClientId: cfg().cognitoClientId,
    Username: email.trim(),
    ConfirmationCode: code.trim(),
    Password: newPassword,
  });
}

export async function signOut() {
  const t = store.get();
  store.clear();
  if (t?.refreshToken) {
    // Revokes the refresh token and the tokens issued from it. Best effort.
    await cognito('RevokeToken', { ClientId: cfg().cognitoClientId, Token: t.refreshToken }).catch(() => {});
  }
}

export function user() {
  const t = store.get();
  if (!t) return null;
  try {
    const claims = decodeJwt(t.idToken);
    return { email: claims.email, sub: claims.sub };
  } catch {
    store.clear();
    return null;
  }
}

/** A valid ID token, refreshed when it is about to expire. */
export async function idToken() {
  const t = store.get();
  if (!t) throw new AuthError('NoSession', 'not signed in');
  if (t.exp - 60 > Date.now() / 1000) return t.idToken;
  if (!t.refreshToken) throw new AuthError('NoSession', 'session expired');
  try {
    const data = await cognito('InitiateAuth', {
      AuthFlow: 'REFRESH_TOKEN_AUTH',
      ClientId: cfg().cognitoClientId,
      AuthParameters: { REFRESH_TOKEN: t.refreshToken },
    });
    saveTokens(data.AuthenticationResult, t.refreshToken);
  } catch (err) {
    store.clear();
    throw new AuthError('NoSession', 'session expired');
  }
  return store.get().idToken;
}

/** Same rules as the user pool's password policy (infra/api.tf). */
export function passwordProblems(pw) {
  const problems = [];
  if (pw.length < 12) problems.push('at least 12 characters');
  if (!/[a-z]/.test(pw)) problems.push('a lowercase letter');
  if (!/[A-Z]/.test(pw)) problems.push('an uppercase letter');
  if (!/[0-9]/.test(pw)) problems.push('a number');
  return problems;
}
