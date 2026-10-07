/**
 * The shared Modal, tested for what jsdom can see: it renders through a portal
 * into `document.body`, and it marks the app root (`#root`) `inert` while open
 * so the page behind it is removed from the tab order and the accessibility
 * tree. Both must be undone on unmount, and a pre-existing `inert` value must
 * come back rather than being wiped.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { Modal } from './primitives';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe('Modal', () => {
  let host: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(() => {
    if (root) {
      const mounted = root;
      act(() => mounted.unmount());
      root = null;
    }
    host.remove();
  });

  it('renders into a portal and marks the app root inert while open', async () => {
    const appRoot = document.createElement('div');
    appRoot.id = 'root';
    document.body.appendChild(appRoot);
    try {
      await act(async () => {
        root?.render(
          <Modal title="title" onClose={() => {}} footer={<button>ok</button>}>
            <p>body</p>
          </Modal>,
        );
      });

      expect(appRoot.hasAttribute('inert')).toBe(true);
      expect(appRoot.getAttribute('aria-hidden')).toBe('true');
      // Portal: the dialog is a sibling of `#root`, never a child of it.
      expect(document.body.querySelector('.modal')).not.toBeNull();
      expect(appRoot.querySelector('.modal')).toBeNull();

      await act(async () => {
        root?.unmount();
        root = null;
      });
      expect(appRoot.hasAttribute('inert')).toBe(false);
      expect(appRoot.hasAttribute('aria-hidden')).toBe(false);
    } finally {
      appRoot.remove();
    }
  });

  it('restores a pre-existing inert value instead of clobbering it', async () => {
    const appRoot = document.createElement('div');
    appRoot.id = 'root';
    appRoot.setAttribute('inert', 'until-ready');
    document.body.appendChild(appRoot);
    try {
      await act(async () => {
        root?.render(
          <Modal title="title" onClose={() => {}} footer={<button>ok</button>}>
            <p>body</p>
          </Modal>,
        );
      });
      expect(appRoot.getAttribute('inert')).toBe('');

      await act(async () => {
        root?.unmount();
        root = null;
      });
      expect(appRoot.getAttribute('inert')).toBe('until-ready');
      expect(appRoot.hasAttribute('aria-hidden')).toBe(false);
    } finally {
      appRoot.remove();
    }
  });
});
