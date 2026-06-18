import { useState, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/core';
import SplashScreen from './components/SplashScreen';
import SetupWizard from './components/SetupWizard';
import SettingsModal from './components/SettingsModal';
import { X } from 'lucide-react';
import './index.css';

type AppState = 'booting' | 'setup' | 'main';

import { getCurrentWindow } from '@tauri-apps/api/window';

const WindowControls = () => {
  const appWindow = getCurrentWindow();
  return (
    <div className="shell-window-controls">
      <button onClick={() => appWindow.minimize()} className="shell-window-btn" title="最小化">
        <svg width="11" height="11" viewBox="0 0 11 11"><rect x="1.5" y="5" width="8" height="1" fill="currentColor"/></svg>
      </button>
      <button onClick={() => appWindow.toggleMaximize()} className="shell-window-btn" title="最大化">
        <svg width="11" height="11" viewBox="0 0 11 11"><rect x="1.5" y="1.5" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1"/></svg>
      </button>
      <button onClick={() => appWindow.close()} className="shell-window-btn shell-window-close" title="关闭">
        <svg width="11" height="11" viewBox="0 0 11 11"><path d="M2,2 L9,9 M9,2 L2,9" stroke="currentColor" strokeWidth="1.2"/></svg>
      </button>
    </div>
  );
};

function App() {
  const [appState, setAppState] = useState<AppState>('booting');
  const [activePort, setActivePort] = useState<number>(8680);
  const [showSettings, setShowSettings] = useState(false);

  useEffect(() => {
    const handleMessage = (e: MessageEvent) => {
      if (e.data === 'tauri-drag') {
        getCurrentWindow().startDragging();
      } else if (e.data === 'open-settings') {
        setShowSettings(true);
      } else if (e.data === 'refresh-iframe') {
        const iframe = document.getElementById('plugin-iframe') as HTMLIFrameElement;
        if (iframe) {
          const url = new URL(iframe.src);
          url.searchParams.set('t', Date.now().toString());
          iframe.src = url.toString();
        }
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  useEffect(() => {
    let interval: number;

    const checkStatus = async () => {
      try {
        const status = await invoke<string>('get_backend_status');
        
        if (status === 'running' || status === 'exited:0') {
          try {
            const PORTS = [8680, 8681, 8682, 8683, 8684];
            
            const checkPort = async (port: number) => {
              const res = await fetch(`http://127.0.0.1:${port}/api/config`);
              if (res.ok) return port;
              throw new Error('Not ok');
            };

            const active = await Promise.any(PORTS.map(p => checkPort(p)));
            setActivePort(active);

            const setupRes = await fetch(`http://127.0.0.1:${active}/api/setup/status`);
            if (setupRes.ok) {
              const data = await setupRes.json();
              if (data.status === 'awaiting_config') {
                setAppState('setup');
              } else {
                setAppState('main');
              }
            } else {
              setAppState('main');
            }

            clearInterval(interval);
          } catch (e) {
            console.log("Waiting for backend API...", e);
          }
        }
      } catch (e) {
        console.error("Failed to check backend status:", e);
      }
    };

    interval = window.setInterval(checkStatus, 1000);
    checkStatus();

    return () => clearInterval(interval);
  }, []);

  const handleSetupComplete = () => {
    if (showSettings) {
      setShowSettings(false);
      const iframe = document.getElementById('plugin-iframe') as HTMLIFrameElement;
      if (iframe) iframe.src = iframe.src;
    } else {
      setAppState('booting');
      setTimeout(() => {
        window.location.reload();
      }, 2000);
    }
  };

  return (
    <div className="shell-container" style={{ flexDirection: 'column' }}>
      {appState === 'booting' && <SplashScreen />}
      
      {appState === 'setup' && <SetupWizard onComplete={handleSetupComplete} port={activePort} />}
      
      {appState === 'main' && (
        <>
          <WindowControls />
          <div 
            data-tauri-drag-region 
            className="shell-drag-region"
            style={{ 
              position: 'absolute', 
              top: 0, 
              left: '260px', 
              right: '320px', 
              height: '48px', 
              zIndex: 9998 
            }} 
          />
          <div className="shell-main-content" style={{ height: '100vh', width: '100vw' }}>
            <iframe 
              id="plugin-iframe"
              src={`http://127.0.0.1:${activePort}/?embedded=1&t=${Date.now()}`} 
              className="plugin-iframe"
              title="MoFox Code WebUI"
              allow="clipboard-read; clipboard-write"
              style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
            />
            {showSettings && (
              <div 
                className="fixed inset-0 z-[10000] bg-black/50 backdrop-blur-sm flex items-center justify-center p-6" 
                onPointerDown={(e) => {
                  if (e.target === e.currentTarget) setShowSettings(false);
                }}
              >
                <div 
                  className="w-full max-w-4xl h-[85vh] bg-white dark:bg-gray-900 rounded-2xl shadow-2xl overflow-hidden flex flex-col animate-in fade-in zoom-in-95 duration-200" 
                  onPointerDown={(e) => e.stopPropagation()}
                >
                  <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800/50 shrink-0">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">系统设置</h2>
                    <button 
                      onClick={() => setShowSettings(false)}
                      className="p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 transition-colors"
                    >
                      <X size={18} />
                    </button>
                  </div>
                  <div className="flex-1 overflow-hidden">
                    <SettingsModal port={activePort} onClose={() => setShowSettings(false)} />
                  </div>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default App;
