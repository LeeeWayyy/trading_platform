(() => {
  if (window.__ngDisconnectOverlayInstalled) {
    return;
  }
  window.__ngDisconnectOverlayInstalled = true;

  const MAX_ATTEMPTS = 5;
  const BACKOFF_SECONDS = [0.5, 1, 2, 4, 8];
  let attempt = 0;
  let timerId = null;

  function ensureOverlay() {
    let overlay = document.getElementById('ng-disconnect-overlay');
    if (overlay) {
      return overlay;
    }

    overlay = document.createElement('div');
    overlay.id = 'ng-disconnect-overlay';
    overlay.style.cssText = [
      'position:fixed',
      'inset:0',
      'background:rgba(15, 23, 42, 0.9)',
      'color:#f8fafc',
      'display:none',
      'align-items:center',
      'justify-content:center',
      'z-index:9999',
      'font-family:"IBM Plex Mono", "SFMono-Regular", Menlo, monospace',
    ].join(';');

    overlay.innerHTML = `
      <div style="text-align:center; max-width:420px; padding:24px;">
        <div style="font-size:20px; font-weight:600; margin-bottom:8px;">Connection Lost</div>
        <div id="ng-disconnect-status" style="font-size:14px; margin-bottom:16px;">Reconnecting...</div>
        <div id="ng-disconnect-retry" style="font-size:12px; margin-bottom:16px;"></div>
        <button id="ng-disconnect-reload" style="display:none; padding:8px 12px; background:#0ea5e9; color:#0f172a; border:none; border-radius:6px; font-weight:600; cursor:pointer;">Reload now</button>
      </div>
    `;

    document.body.appendChild(overlay);

    const button = overlay.querySelector('#ng-disconnect-reload');
    if (button) {
      button.addEventListener('click', () => window.location.reload());
    }

    return overlay;
  }

  function showOverlay(message) {
    const overlay = ensureOverlay();
    const status = overlay.querySelector('#ng-disconnect-status');
    if (status) {
      status.textContent = message || 'Reconnecting...';
    }
    overlay.style.display = 'flex';
  }

  function hideOverlay() {
    const overlay = document.getElementById('ng-disconnect-overlay');
    if (overlay) {
      overlay.style.display = 'none';
    }
    const retry = document.getElementById('ng-disconnect-retry');
    if (retry) {
      retry.textContent = '';
    }
    const button = document.getElementById('ng-disconnect-reload');
    if (button) {
      button.style.display = 'none';
    }
  }

  function updateRetryText(text) {
    const retry = document.getElementById('ng-disconnect-retry');
    if (retry) {
      retry.textContent = text;
    }
  }

  function scheduleRetry() {
    clearTimeout(timerId);

    if (attempt >= MAX_ATTEMPTS) {
      updateRetryText('Reconnection failed. Reloading...');
      const button = document.getElementById('ng-disconnect-reload');
      if (button) {
        button.style.display = 'inline-block';
      }
      setTimeout(() => window.location.reload(), 2000);
      return;
    }

    const delay = BACKOFF_SECONDS[Math.min(attempt, BACKOFF_SECONDS.length - 1)];
    updateRetryText(`Attempt ${attempt + 1}/${MAX_ATTEMPTS} — retrying in ${delay}s`);
    timerId = setTimeout(() => {
      attempt += 1;
      scheduleRetry();
    }, delay * 1000);
  }

  function handleDisconnect() {
    attempt = 0;
    showOverlay('Connection lost. Reconnecting...');
    scheduleRetry();
  }

  function handleConnect() {
    attempt = 0;
    clearTimeout(timerId);
    hideOverlay();
  }

  function findSocket() {
    if (window.socket) return window.socket;
    if (window.nicegui && window.nicegui.socket) return window.nicegui.socket;
    if (window.NiceGUI && window.NiceGUI.socket) return window.NiceGUI.socket;
    return null;
  }

  function attachSocketListeners(socket) {
    if (!socket || socket.__ngOverlayBound) {
      return;
    }
    socket.__ngOverlayBound = true;

    socket.on('disconnect', handleDisconnect);
    socket.on('connect', handleConnect);
    socket.on('reconnect_attempt', () => showOverlay('Reconnecting...'));
    socket.on('reconnect_failed', () => scheduleRetry());
  }

  const socketCheck = setInterval(() => {
    const socket = findSocket();
    if (socket) {
      attachSocketListeners(socket);
      clearInterval(socketCheck);
    }
  }, 250);

  window.addEventListener('offline', handleDisconnect);
  window.addEventListener('online', handleConnect);
})();
