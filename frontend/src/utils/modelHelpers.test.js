import { describe, it, expect } from 'vitest';

import { getModelVisuals, getShortModelName } from './modelHelpers';


describe('getShortModelName', () => {
  it('returns Unknown for empty model id', () => {
    expect(getShortModelName('')).toBe('Unknown');
    expect(getShortModelName(null)).toBe('Unknown');
  });

  it('handles provider/model format', () => {
    expect(getShortModelName('google/gemini-3-flash-preview')).toBe('gemini-3-flash-preview');
  });

  it('returns the trailing segment for deep slash paths', () => {
    expect(getShortModelName('google/models/gemini-2.0-flash')).toBe('gemini-2.0-flash');
  });

  it('handles provider:model format', () => {
    expect(getShortModelName('google:gemini-3-flash-preview')).toBe('gemini-3-flash-preview');
  });
});


describe('getModelVisuals', () => {
  it('prioritizes ollama prefix over llama substring', () => {
    expect(getModelVisuals('ollama:llama3.1').name).toBe('Ollama');
  });

  it('detects groq before llama/meta patterns', () => {
    expect(getModelVisuals('groq:llama-3.3-70b-versatile').name).toBe('Groq');
  });
});
