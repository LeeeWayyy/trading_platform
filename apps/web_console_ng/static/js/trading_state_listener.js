/**
 * Trading State Listener
 * Updates UI badges based on kill switch and circuit breaker state changes.
 * Listens for 'trading_state_change' custom events dispatched by the backend.
 */
(function () {
  if (window.__tpTradingStateListenerAdded) return;
  window.__tpTradingStateListenerAdded = true;

  const updateGlobalStatusBanner = () => {
    const banner = document.getElementById('global-status-banner');
    const label = document.getElementById('global-status-banner-label');
    if (!banner || !label) return;
    if (window._tradingState && window._tradingState.statusStale) return;

    const state = ((window._tradingState && window._tradingState.killSwitchState) || 'UNKNOWN')
      .toUpperCase();
    const circuit = (
      (window._tradingState && window._tradingState.circuitBreakerState) || 'UNKNOWN'
    ).toUpperCase();

    if (circuit === 'TRIPPED') {
      label.textContent = 'TRADING HALTED (CIRCUIT)';
      banner.classList.add('bg-red-600', 'text-white');
      banner.classList.remove('bg-green-600', 'bg-yellow-500', 'text-black');
      return;
    }

    if (state === 'ENGAGED') {
      label.textContent = 'TRADING HALTED';
      banner.classList.add('bg-red-600', 'text-white');
      banner.classList.remove('bg-green-600', 'bg-yellow-500', 'text-black');
      return;
    }

    if (state === 'DISENGAGED' || state === 'ACTIVE' || circuit === 'OPEN' || circuit === 'QUIET_PERIOD') {
      label.textContent = 'TRADING ACTIVE';
      banner.classList.add('bg-green-600', 'text-white');
      banner.classList.remove('bg-red-600', 'bg-yellow-500', 'text-black');
      return;
    }

    label.textContent = 'TRADING STATUS UNKNOWN';
    banner.classList.add('bg-yellow-500', 'text-black');
    banner.classList.remove('bg-red-600', 'bg-green-600', 'text-white');
  };

  window.addEventListener('trading_state_change', (event) => {
    const detail = (event && event.detail) || {};
    const killSwitch = detail.killSwitch;
    const killSwitchState = detail.killSwitchState;
    const circuitBreaker = detail.circuitBreaker;
    const circuitBreakerState =
      typeof detail.circuitBreakerState === 'string'
        ? detail.circuitBreakerState
        : detail.circuitState;
    const readOnly = detail.readOnly;
    const connectionState = detail.connectionState;
    const hasStatusStale = Object.prototype.hasOwnProperty.call(detail, 'statusStale');
    const hasFreshTradingState =
      typeof killSwitchState === 'string' ||
      typeof killSwitch === 'boolean' ||
      typeof circuitBreakerState === 'string' ||
      typeof circuitBreaker === 'boolean';

    window._tradingState = window._tradingState || {};
    if (hasStatusStale) {
      window._tradingState.statusStale = detail.statusStale === true;
    } else if (hasFreshTradingState) {
      window._tradingState.statusStale = false;
    }
    const statusStale = window._tradingState.statusStale === true;

    // Update kill switch badge
    const ksEl = document.getElementById('kill-switch-badge');
    if (ksEl && !statusStale) {
      if (typeof killSwitchState === 'string') {
        const state = killSwitchState.toUpperCase();
        if (state === 'ENGAGED') {
          ksEl.textContent = 'KILL SWITCH: ENGAGED';
          ksEl.classList.add('bg-red-500', 'text-white');
          ksEl.classList.remove('bg-slate-700', 'bg-green-500', 'bg-yellow-500', 'text-black');
        } else if (state === 'DISENGAGED') {
          ksEl.textContent = 'KILL SWITCH: DISENGAGED';
          ksEl.classList.add('bg-slate-700', 'text-slate-100');
          ksEl.classList.remove('bg-red-500', 'bg-green-500', 'bg-yellow-500', 'text-white', 'text-black');
        } else {
          ksEl.textContent = `KILL SWITCH: ${state || 'UNKNOWN'}`;
          ksEl.classList.add('bg-yellow-500', 'text-black');
          ksEl.classList.remove('bg-red-500', 'bg-green-500', 'bg-slate-700', 'text-white', 'text-slate-100');
        }
      } else if (typeof killSwitch === 'boolean') {
        if (killSwitch) {
          ksEl.textContent = 'KILL SWITCH: ENGAGED';
          ksEl.classList.add('bg-red-500', 'text-white');
          ksEl.classList.remove('bg-slate-700', 'bg-green-500', 'bg-yellow-500', 'text-black');
        } else {
          ksEl.textContent = 'KILL SWITCH: DISENGAGED';
          ksEl.classList.add('bg-slate-700', 'text-slate-100');
          ksEl.classList.remove('bg-red-500', 'bg-green-500', 'bg-yellow-500', 'text-white', 'text-black');
        }
      }
    }

    // Update circuit breaker badge
    const cbEl = document.getElementById('circuit-breaker-badge');
    if (cbEl && !statusStale) {
      if (typeof circuitBreakerState === 'string') {
        const state = circuitBreakerState.toUpperCase();
        if (state === 'TRIPPED') {
          cbEl.textContent = 'CIRCUIT TRIPPED';
          cbEl.classList.add('bg-red-500', 'text-white');
          cbEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
        } else if (state === 'OPEN' || state === 'QUIET_PERIOD') {
          cbEl.textContent = 'CIRCUIT OK';
          cbEl.classList.add('bg-green-500', 'text-white');
          cbEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
        } else {
          cbEl.textContent = `CIRCUIT: ${state || 'UNKNOWN'}`;
          cbEl.classList.add('bg-yellow-500', 'text-black');
          cbEl.classList.remove('bg-red-500', 'bg-green-500', 'text-white');
        }
      } else if (typeof circuitBreaker === 'boolean') {
        if (circuitBreaker) {
          cbEl.textContent = 'CIRCUIT TRIPPED';
          cbEl.classList.add('bg-red-500', 'text-white');
          cbEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
        } else {
          cbEl.textContent = 'CIRCUIT OK';
          cbEl.classList.add('bg-green-500', 'text-white');
          cbEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
        }
      }
    }

    if (typeof killSwitchState === 'string') {
      window._tradingState.killSwitchState = killSwitchState;
    } else if (typeof killSwitch === 'boolean') {
      window._tradingState.killSwitchState = killSwitch ? 'ENGAGED' : 'DISENGAGED';
    }
    if (typeof circuitBreakerState === 'string') {
      window._tradingState.circuitBreakerState = circuitBreakerState;
    } else if (typeof circuitBreaker === 'boolean') {
      window._tradingState.circuitBreakerState = circuitBreaker ? 'TRIPPED' : 'NORMAL';
    }

    if (typeof readOnly === 'boolean') {
      window._tradingState = window._tradingState || {};
      window._tradingState.readOnly = readOnly;
    }
    if (typeof connectionState === 'string') {
      window._tradingState = window._tradingState || {};
      window._tradingState.connectionState = connectionState;
    }

    const readOnlyTargets = document.querySelectorAll('[data-readonly-disable=\"true\"]');
    if (readOnlyTargets.length > 0 && typeof readOnly === 'boolean') {
      readOnlyTargets.forEach((el) => {
        if (readOnly) {
          // Store original disabled state and title before overriding
          if (!el.hasAttribute('data-original-disabled-stored')) {
            el.dataset.originalDisabled = el.hasAttribute('disabled') ? 'true' : 'false';
            el.dataset.originalTitle = el.title || '';
            el.setAttribute('data-original-disabled-stored', 'true');
          }
          el.setAttribute('disabled', 'true');
          el.classList.add('opacity-50', 'cursor-not-allowed');
          el.title = el.dataset.readonlyTooltip || 'Connection lost - read-only mode';
        } else {
          // Restore original disabled state and title
          if (el.hasAttribute('data-original-disabled-stored')) {
            if (el.dataset.originalDisabled === 'true') {
              el.setAttribute('disabled', 'true');
            } else {
              el.removeAttribute('disabled');
            }
            el.title = el.dataset.originalTitle || '';
            // Clear stored state
            el.removeAttribute('data-original-disabled-stored');
            delete el.dataset.originalDisabled;
            delete el.dataset.originalTitle;
          } else {
            // No stored state - just clear read-only effects
            el.removeAttribute('disabled');
            el.title = '';
          }
          el.classList.remove('opacity-50', 'cursor-not-allowed');
        }
      });
    }

    updateGlobalStatusBanner();
  });
})();
