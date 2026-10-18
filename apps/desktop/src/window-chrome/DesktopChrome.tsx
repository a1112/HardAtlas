import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { NativeEffectsToggle } from './NativeEffectsToggle';
import { windowShortcut } from './window-controls';

export function DesktopChrome() {
  const [available, setAvailable] = useState(false);
  const [mode, setMode] = useState({ maximized: false, fullscreen: false });
  const [error, setError] = useState('');
  const action = (action: string) => {
    void invoke('desktop_window_action', { action }).catch(() => setError('窗口操作未完成，请重试'));
  };
  useEffect(() => {
    if (!('__TAURI_INTERNALS__' in window)) return;
    let active = true;
    void invoke<boolean>('window_chrome_available').then(value => {
      if (!active) return;
      setAvailable(value); document.documentElement.classList.toggle('atlas-desktop', value);
    }).catch(() => {});
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!available) return;
    const read = () => setMode({ maximized: document.documentElement.dataset.windowMaximized === 'true', fullscreen: document.documentElement.dataset.windowFullscreen === 'true' });
    const key = (event: KeyboardEvent) => {
      const command = windowShortcut(event);
      if (command) { event.preventDefault(); action(command); }
    };
    read(); window.addEventListener('project-window-state', read); window.addEventListener('keydown', key);
    return () => { window.removeEventListener('project-window-state', read); window.removeEventListener('keydown', key); };
  }, [available]);
  if (!available) return null;
  return <header className="desktop-titlebar" data-tauri-drag-region aria-label="Atlas 窗口控制">
    <strong data-tauri-drag-region>Atlas</strong>
    <NativeEffectsToggle />
    <div className="desktop-window-buttons" data-no-drag>
      <button type="button" aria-label="最小化" title="最小化 (⌘M)" onClick={() => action('minimize')}>−</button>
      <button type="button" aria-label={mode.fullscreen ? '退出全屏' : mode.maximized ? '恢复窗口' : '最大化'} onClick={() => action('maximize')}>{mode.maximized || mode.fullscreen ? '恢复' : '最大化'}</button>
      <button type="button" aria-label={mode.fullscreen ? '退出全屏' : '进入全屏'} title="全屏 (⌃⌘F / F11)" onClick={() => action('fullscreen')}>⛶</button>
      <button type="button" aria-label="关闭窗口" title="关闭窗口 (⌘W)" onClick={() => action('close')}>×</button>
    </div>
    {error && <span role="alert">{error}</span>}
  </header>;
}
