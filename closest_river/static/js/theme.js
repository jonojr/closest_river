// Light/dark theme toggle.
//
// The initial theme is set by an inline script in base.html's <head> so the
// page never flashes the wrong colours; this module only handles changes. An
// explicit choice is remembered in localStorage, otherwise the theme follows
// the operating system. Other scripts (the map) listen for 'themechange'.

import { trackSetting } from './analytics';

const STORAGE_KEY = 'theme';
const root = document.documentElement;
const systemDark = window.matchMedia('(prefers-color-scheme: dark)');

function storedTheme() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function currentTheme() {
  return root.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';
}

function syncToggle() {
  const toggle = document.querySelector('#theme-toggle');
  if (!toggle) {
    return;
  }
  const dark = currentTheme() === 'dark';
  toggle.setAttribute(
    'aria-label',
    dark ? 'Switch to light mode' : 'Switch to dark mode',
  );
  toggle.title = toggle.getAttribute('aria-label');
  toggle.querySelector('i').className = dark ? 'bi bi-sun' : 'bi bi-moon-stars';
}

function applyTheme(theme) {
  if (theme === currentTheme()) {
    return;
  }
  root.setAttribute('data-bs-theme', theme);
  syncToggle();
  document.dispatchEvent(new CustomEvent('themechange', { detail: { theme } }));
}

document.addEventListener('DOMContentLoaded', () => {
  syncToggle();
  document.querySelector('#theme-toggle')?.addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private mode or blocked storage: the choice just won't persist.
    }
    applyTheme(next);
    trackSetting('theme', next);
  });
});

systemDark.addEventListener('change', (event) => {
  if (!storedTheme()) {
    applyTheme(event.matches ? 'dark' : 'light');
  }
});
