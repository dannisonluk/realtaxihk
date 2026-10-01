import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
// Imported for its side effect *before* `App`: initialises i18next and sets
// `<html lang>` from the stored/browser locale, so the first render is already
// translated rather than flashing the fallback keys.
import { i18n } from './i18n';
import { App } from './app/App';

const container = document.getElementById('root');
if (!container) {
  // A pre-mount failure with no React tree to render into. Still translated —
  // `./i18n` is imported above, so `t` is ready even here.
  throw new Error(i18n.t('errors.noRoot'));
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
