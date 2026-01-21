/**
 * Keyboard hotkey handler for trading workflows.
 */
window.HotkeyHandler = {
    bindings: [],
    enabled: true,
    currentScope: 'global',
    listenerRegistered: false,
    _keydownHandler: null,

    INPUT_SELECTORS: 'input, textarea, select, [contenteditable="true"]',

    init(bindings) {
        this.bindings = bindings || [];
        this._registerListener();
        console.log('HotkeyHandler initialized with', this.bindings.length, 'bindings');
    },

    setScope(scope) {
        this.currentScope = scope;
    },

    setEnabled(enabled) {
        this.enabled = enabled;
    },

    _registerListener() {
        if (this.listenerRegistered) {
            return;
        }
        this._keydownHandler = (event) => {
            if (!this.enabled) return;
            if (event.repeat || event.isComposing) return;

            const isInput = event.target.matches(this.INPUT_SELECTORS);

            const modifiers = [];
            if (event.ctrlKey || event.metaKey) modifiers.push('ctrl');
            if (event.shiftKey && !this._isShiftedPunctuation(event.key)) {
                modifiers.push('shift');
            }
            if (event.altKey) modifiers.push('alt');

            const binding = this._findBinding(event.key, modifiers, isInput);

            if (binding) {
                event.preventDefault();
                event.stopPropagation();
                this._dispatchAction(binding.action);
            }
        };
        document.addEventListener('keydown', this._keydownHandler);
        this.listenerRegistered = true;
    },

    _isShiftedPunctuation(key) {
        const shiftedPunctuation = '!@#$%^&*()_+{}|:"<>?~';
        return key.length === 1 && shiftedPunctuation.includes(key);
    },

    _findBinding(key, modifiers, isInput) {
        for (const binding of this.bindings) {
            if (!binding.enabled) continue;
            if (binding.key.toLowerCase() !== key.toLowerCase()) continue;

            const bindingMods = new Set(binding.modifiers || []);
            const eventMods = new Set(modifiers);
            if (bindingMods.size !== eventMods.size) continue;
            let modsMatch = true;
            for (const mod of bindingMods) {
                if (!eventMods.has(mod)) {
                    modsMatch = false;
                    break;
                }
            }
            if (!modsMatch) continue;

            if (binding.scope === 'global' && modifiers.length > 0) {
                return binding;
            }

            if (binding.scope === 'global' && !isInput) {
                return binding;
            }

            if (binding.scope === 'order_form') {
                if (this.currentScope === 'order_form' || this._isInOrderForm()) {
                    return binding;
                }
            }

            if (binding.scope === 'grid' && this.currentScope === 'grid') {
                return binding;
            }
        }
        return null;
    },

    _isInOrderForm() {
        const activeEl = document.activeElement;
        if (!activeEl) return false;
        return activeEl.closest('[data-order-form]') !== null;
    },

    _dispatchAction(action) {
        console.log('Hotkey action:', action);
        window.dispatchEvent(new CustomEvent('hotkey_action', {
            detail: { action }
        }));
    },

    formatHotkey(binding) {
        const parts = [];
        if (binding.modifiers) {
            for (const mod of binding.modifiers) {
                if (mod === 'ctrl') parts.push('Ctrl');
                else if (mod === 'shift') parts.push('Shift');
                else if (mod === 'alt') parts.push('Alt');
            }
        }
        let keyDisplay = binding.key;
        if (binding.key === 'Enter') keyDisplay = 'Enter';
        else if (binding.key === 'Escape') keyDisplay = 'Esc';
        else if (binding.key === ' ') keyDisplay = 'Space';
        else keyDisplay = binding.key.toUpperCase();

        parts.push(keyDisplay);
        return parts.join('+');
    }
};
