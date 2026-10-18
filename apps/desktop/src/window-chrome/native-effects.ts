export type BackdropStatus = {
  enabled: boolean; applied: boolean; reducedTransparency: boolean; highContrast: boolean;
  platform: string; effect: string; detail: string;
};
type StoragePort = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

// Persist only this application's appearance choice; no geometry/storage rewrite.
export function createEffectsController(options: {
  invoke: Invoke; storage: StoragePort; events: EventTarget; key: string;
  onStatus: (status: BackdropStatus) => void; onError: (error: unknown) => void;
}) {
  let disposed = false;
  let generation = 0;
  const refresh = async () => {
    const request = ++generation;
    const status = await options.invoke('native_effects_status') as BackdropStatus;
    if (!disposed && request === generation) options.onStatus(status);
  };
  const changed = () => { void refresh().catch(options.onError); };
  const start = async () => {
    options.events.addEventListener('native-backdrop-changed', changed);
    // Storage failures leave the native policy solid instead of silently opting in.
    const enabled = options.storage.getItem(options.key) !== 'false';
    await options.invoke('set_native_effects_enabled', { enabled });
    if (!disposed) await refresh();
  };
  const setEnabled = async (enabled: boolean) => {
    const old = options.storage.getItem(options.key);
    options.storage.setItem(options.key, String(enabled));
    try { await options.invoke('set_native_effects_enabled', { enabled }); }
    catch (error) {
      if (old === null) options.storage.removeItem(options.key);
      else options.storage.setItem(options.key, old);
      throw error;
    }
    await refresh();
  };
  return { start, setEnabled, refresh, dispose() {
    disposed = true; ++generation;
    options.events.removeEventListener('native-backdrop-changed', changed);
  } };
}
