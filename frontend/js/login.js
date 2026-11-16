// The custom sign-in card: sign in, first-login password change, forgot / reset password.
import * as auth from './auth.js';

const RULES = [
  ['at least 12 characters', (p) => p.length >= 12],
  ['a lowercase letter', (p) => /[a-z]/.test(p)],
  ['an uppercase letter', (p) => /[A-Z]/.test(p)],
  ['a number', (p) => /[0-9]/.test(p)],
];

export function mountLogin(onSignedIn) {
  const card = document.getElementById('auth-card');
  const forms = Object.fromEntries([...card.querySelectorAll('.auth-step')].map((f) => [f.dataset.step, f]));
  let email = '';

  function show(step, { error = '', note = '' } = {}) {
    for (const [name, form] of Object.entries(forms)) form.hidden = name !== step;
    const form = forms[step];
    setMessage(form, error || note, error ? 'error' : 'ok');
    if (email && form.elements.email && !form.elements.email.value) form.elements.email.value = email;
    form.querySelectorAll('[data-email]').forEach((el) => { el.textContent = email; });
    form.querySelectorAll('[data-rules]').forEach((input) => renderRules(input));
    const first = [...form.querySelectorAll('input')].find((i) => !i.value) || form.querySelector('input');
    first?.focus();
  }

  function setMessage(form, text, kind = 'error') {
    const el = form.querySelector('.auth-error');
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle('auth-error--ok', kind === 'ok');
  }

  function renderRules(input) {
    const list = input.closest('form').querySelector('.rules');
    list.replaceChildren(...RULES.map(([label, test]) => {
      const li = document.createElement('li');
      li.textContent = label;
      li.classList.toggle('ok', test(input.value));
      return li;
    }));
  }

  async function busy(form, fn) {
    const btn = form.querySelector('button[type=submit]');
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Please wait…';
    setMessage(form, '');
    try {
      await fn();
    } catch (err) {
      setMessage(form, err.message || 'Something went wrong.');
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  // Generic UI: show/hide password, live password rules, step links
  card.addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-toggle-pw]');
    if (toggle) {
      const input = toggle.parentElement.querySelector('input');
      const showing = input.type === 'text';
      input.type = showing ? 'password' : 'text';
      toggle.textContent = showing ? 'Show' : 'Hide';
      toggle.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
      return;
    }
    const goto = e.target.closest('[data-goto]');
    if (goto) {
      const from = goto.closest('form');
      if (from.elements.email?.value) email = from.elements.email.value.trim();
      show(goto.dataset.goto);
    }
  });
  card.addEventListener('input', (e) => {
    if (e.target.matches('[data-rules]')) renderRules(e.target);
  });

  // 1. Sign in
  forms.signin.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = e.target.elements;
    email = f.email.value.trim();
    if (!email || !f.password.value) return setMessage(e.target, 'Enter your email and password.');
    busy(e.target, async () => {
      const result = await auth.signIn(email, f.password.value);
      f.password.value = '';
      if (result.challenge === 'NEW_PASSWORD_REQUIRED') show('newpw');
      else onSignedIn(result.user);
    });
  });

  // 2. First sign-in: new password
  forms.newpw.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const problems = auth.passwordProblems(f.password.value);
    if (problems.length) return setMessage(e.target, `Your password needs ${problems.join(', ')}.`);
    if (f.password.value !== f.confirm.value) return setMessage(e.target, 'The two passwords are different.');
    busy(e.target, async () => {
      try {
        const result = await auth.completeNewPassword(f.password.value);
        onSignedIn(result.user);
      } catch (err) {
        if (err.code === 'NoSession') show('signin', { error: err.message });
        else throw err;
      }
    });
  });

  // 3. Forgot password: send code
  forms.forgot.addEventListener('submit', (e) => {
    e.preventDefault();
    email = e.target.elements.email.value.trim();
    if (!email) return setMessage(e.target, 'Enter your email.');
    busy(e.target, async () => {
      await auth.forgotPassword(email);
      forms.reset.reset();
      show('reset');
    });
  });

  // 4. Reset password with the code
  forms.reset.addEventListener('submit', (e) => {
    e.preventDefault();
    const f = e.target.elements;
    if (!f.code.value.trim()) return setMessage(e.target, 'Enter the code from the email.');
    const problems = auth.passwordProblems(f.password.value);
    if (problems.length) return setMessage(e.target, `Your password needs ${problems.join(', ')}.`);
    busy(e.target, async () => {
      await auth.confirmForgotPassword(email, f.code.value, f.password.value);
      forms.signin.reset();
      show('signin', { note: 'Password changed. Sign in with your new password.' });
    });
  });

  show('signin');
}
