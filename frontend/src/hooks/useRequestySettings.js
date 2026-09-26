/*
 * Fork: Requesty in the Settings panel (key, Test, Disconnect, model list).
 *
 * Kept out of Settings.jsx so upstream merges into that file stay small.
 * Mirrors Settings.jsx's OpenRouter handlers. Requesty models come from their
 * own route (/api/models/requesty, ids prefixed `requesty:`).
 */
import { useState } from 'react';
import { api } from '../api';

/**
 * @param {object} deps
 * @param {object|null} deps.settings
 * @param {object} deps.enabledProviders
 * @param {Function} deps.setEnabledProviders
 * @param {Function} deps.setSuccess
 * @param {Function} deps.panel returns Settings' { loadSettings, handleDisconnectProviderKey }
 *   (called lazily: they are defined after the hook call)
 */
export function useRequestySettings({ settings, enabledProviders, setEnabledProviders, setSuccess, panel }) {
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState([]);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  /** Load the model list for `data` (a settings response). */
  const loadModels = async (data) => {
    if (!data?.requesty_api_key_set) {
      setModels([]);
      return;
    }
    try {
      const result = await api.getRequestyModels();
      setModels(result.models || []);
    } catch (err) {
      console.warn('Failed to load Requesty models:', err);
      setModels([]);
    }
  };

  /** Clear the typed key and the last test result (Disconnect All, import). */
  const reset = () => {
    setApiKey('');
    setTestResult(null);
  };

  // Mirrors Settings.jsx's handleTestOpenRouter.
  const handleTest = async () => {
    if (!apiKey && !settings?.requesty_api_key_set) {
      setTestResult({ success: false, message: 'Please enter an API key first' });
      return;
    }
    setIsTesting(true);
    setTestResult(null);
    try {
      // If input is empty but key is configured, pass null to test the saved key
      const result = await api.testRequestyKey(apiKey || null);
      setTestResult(result);

      // Auto-save API key if validation succeeds and a new key was provided
      if (result.success && apiKey) {
        const nextEnabled = { ...enabledProviders, requesty: true };
        await api.updateSettings({
          requesty_api_key: apiKey,
          enabled_providers: nextEnabled,
        });
        setApiKey(''); // Clear input after save
        setEnabledProviders(nextEnabled);

        // Reload settings (and the Requesty model list)
        await panel().loadSettings();

        setSuccess(true);
        setTimeout(() => setSuccess(false), 3000);
      }
    } catch {
      setTestResult({ success: false, message: 'Test failed' });
    } finally {
      setIsTesting(false);
    }
  };

  const handleDisconnect = () => panel().handleDisconnectProviderKey({
    secretField: 'requesty_api_key',
    label: 'Requesty',
    enabledPatch: { requesty: false },
    onLocalClear: () => {
      reset();
      setModels([]);
    },
  });

  return {
    models,
    loadModels,
    reset,
    // Props for settings/ProviderSettings.jsx's Requesty section.
    providerProps: {
      requestyApiKey: apiKey,
      setRequestyApiKey: (val) => { setApiKey(val); setTestResult(null); },
      handleTestRequesty: handleTest,
      isTestingRequesty: isTesting,
      requestyTestResult: testResult,
      requestyAvailableModels: models,
      onDisconnectRequesty: handleDisconnect,
    },
  };
}
