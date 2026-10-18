import { useEffect, useRef, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { createEffectsController, type BackdropStatus } from './native-effects';

export function NativeEffectsToggle() {
  const [status, setStatus] = useState<BackdropStatus>();
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const controller = useRef<ReturnType<typeof createEffectsController> | undefined>(undefined);
  useEffect(() => {
    let active = true;
    const instance = createEffectsController({
      invoke, storage: {
        getItem: key => localStorage.getItem(key),
        setItem: (key, value) => localStorage.setItem(key, value),
        removeItem: key => localStorage.removeItem(key),
      }, events: window, key: 'native-effects-enabled',
      onStatus(next) {
        if (!active) return;
        setStatus(next);
        document.documentElement.classList.toggle('has-native-backdrop', next.applied);
        document.documentElement.classList.toggle('native-high-contrast', next.highContrast);
      },
      onError() { if (active) setError('背景效果暂不可用，使用纯色背景'); },
    });
    controller.current = instance;
    void instance.start().catch(() => { if (active) setError('无法恢复背景设置，使用纯色背景'); })
      .finally(() => { if (active) setBusy(false); });
    const media = ['(prefers-reduced-transparency: reduce)', '(prefers-contrast: more)', '(forced-colors: active)']
      .map(query => matchMedia(query));
    const changed = () => { void invoke('refresh_native_effects').catch(() => {}); };
    media.forEach(query => query.addEventListener('change', changed));
    return () => {
      active = false; instance.dispose();
      media.forEach(query => query.removeEventListener('change', changed));
    };
  }, []);
  const description = error || (!status?.enabled ? '已关闭' : status.highContrast || status.reducedTransparency
    ? '遵循系统偏好，使用纯色背景' : status.applied ? '原生窗后背景' : '当前系统使用纯色背景');
  return <span className="native-effects-control" data-no-drag>
    <button type="button" aria-label="窗后背景模糊" aria-pressed={status?.enabled ?? false}
      disabled={busy} title={`窗后背景模糊：${description}`} onClick={() => {
        setBusy(true); setError('');
        void controller.current?.setEnabled(!status?.enabled).catch(() => setError('无法保存背景设置'))
          .finally(() => setBusy(false));
      }}>背景模糊{status?.enabled ? '：开' : '：关'}</button>
    <span className="native-effects-status" role="status">{description}</span>
  </span>;
}
