interface Window {
  wikiTideNative?: {
    version: number;
    register(name: string, state: () => { dirty?: boolean; busy?: boolean }): () => void;
    state(): { canRestart: boolean };
  };
}
