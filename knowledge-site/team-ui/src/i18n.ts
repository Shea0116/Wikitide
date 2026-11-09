type InterfaceI18n = {
  locale: string;
  t: (value: string, parameters?: unknown[]) => string;
  html: (value: string) => string;
  fragments: (parts: string[], values: unknown[]) => string;
};
declare global {
  interface Window { WikitideI18n?: InterfaceI18n; WikitideEnglish?: Record<string, string>; WikitideEnglishPatterns?: string[]; }
}
// The base page loads the shared catalog synchronously. Standalone components
// and Chinese-only embedded clients retain the original source labels.
export const ui: InterfaceI18n = {
  get locale() { return window.WikitideI18n?.locale || 'zh-CN'; },
  t: (value, parameters = []) => window.WikitideI18n?.t(value, parameters)
    ?? value.replace(/\{(\d+)\}/g, (match, index) => Number(index) < parameters.length ? String(parameters[Number(index)]) : match),
  html: value => window.WikitideI18n?.html(value) ?? value,
  fragments: (parts, values) => window.WikitideI18n?.fragments(parts, values)
    ?? parts.map((part, index) => part + (index < values.length ? values[index] : '')).join(''),
};
