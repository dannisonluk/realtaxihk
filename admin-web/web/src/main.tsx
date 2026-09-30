import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import { App } from './app/App';

const container = document.getElementById('root');
if (!container) throw new Error('找不到 #root 容器。');

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
