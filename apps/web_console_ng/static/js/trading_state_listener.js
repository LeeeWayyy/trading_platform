/**
 * Trading State Listener
 * Updates UI badges based on kill switch and circuit breaker state changes.
 * Listens for 'trading_state_change' custom events dispatched by the backend.
 */
(function () {
  if (window.__tpTradingStateListenerAdded) return;
  window.__tpTradingStateListenerAdded = true;

  window.addEventListener('trading_state_change', (event) => {
    const detail = (event && event.detail) || {};
    const killSwitch = detail.killSwitch;
    const killSwitchState = detail.killSwitchState;
    const circuitBreaker = detail.circuitBreaker;
    const circuitBreakerState = detail.circuitBreakerState;

    // Update kill switch badge
    const ksEl = document.getElementById('kill-switch-badge');
    if (ksEl) {
      if (typeof killSwitchState === 'string') {
        const state = killSwitchState.toUpperCase();
        if (state === 'ENGAGED') {
          ksEl.textContent = 'KILL SWITCH ENGAGED';
          ksEl.classList.add('bg-red-500', 'text-white');
          ksEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
        } else if (state === 'DISENGAGED') {
          ksEl.textContent = 'TRADING ACTIVE';
          ksEl.classList.add('bg-green-500', 'text-white');
          ksEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
        } else {
          ksEl.textContent = `STATE: ${state || 'UNKNOWN'}`;
          ksEl.classList.add('bg-yellow-500', 'text-black');
          ksEl.classList.remove('bg-red-500', 'bg-green-500', 'text-white');
        }
      } else if (typeof killSwitch === 'boolean') {
        if (killSwitch) {
          ksEl.textContent = 'KILL SWITCH ENGAGED';
          ksEl.classList.add('bg-red-500', 'text-white');
          ksEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
        } else {
          ksEl.textContent = 'TRADING ACTIVE';
          ksEl.classList.add('bg-green-500', 'text-white');
          ksEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
        }
      }
    }

    // Update circuit breaker badge
    const cbEl = document.getElementById('circuit-breaker-badge');
    if (cbEl) {
      if (typeof circuitBreakerState === 'string') {
        const state = circuitBreakerState.toUpperCase();
        if (state === 'TRIPPED') {
          cbEl.textContent = 'CIRCUIT TRIPPED';
          cbEl.classList.add('bg-red-500', 'text-white');
          cbEl.classList.remove('bg-green-500', 'bg-yellow-500', 'text-black');
        } else if (state === 'OPEN') {
          cbEl.textContent = 'CIRCUIT OK';
          cbEl.classList.add('bg-green-500', 'text-white');
          cbEl.classList.remove('bg-red-500', 'bg-yellow-500', 'text-black');
        } else if (state === 'QUIET_PERIOD') {
          cbEl.textContent = 'CIRCUIT QUIET PERIOD';
          cbEl.classList.add('bg-yellow-500', 'text-black');
          cbEl.classList.remove('bg-red-500', 'bg-green-500', 'text-white');
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
  });
})();
