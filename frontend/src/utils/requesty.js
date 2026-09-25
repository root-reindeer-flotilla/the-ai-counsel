import { api } from '../api';

/*
 * Fork: Requesty is an aggregator source like OpenRouter. Its models come
 * from /api/models/requesty (ids prefixed `requesty:`), not /api/models/direct,
 * and it has its own toggle, enabled_providers.requesty.
 */

/** Requesty models are offered when a key is saved and the source is not switched off. */
export function isRequestySourceEnabled(settings) {
  const ep = settings?.enabled_providers || {};
  return !!settings?.requesty_api_key_set && ep.requesty !== false;
}

/** Requesty models for a model picker, or [] when the source is off or unreachable. */
export function loadRequestyModels(settings) {
  if (!isRequestySourceEnabled(settings)) return Promise.resolve([]);
  return api.getRequestyModels()
    .then((d) => d.models || [])
    .catch(() => []);
}
