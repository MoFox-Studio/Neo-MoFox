import React, { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';

const SplashScreen: React.FC = () => {
  const [loadingStep, setLoadingStep] = useState(0);
  const steps = [
    "Initializing MoFox Core Environment...",
    "Loading language models and plugins...",
    "Establishing local daemon connection...",
    "Preparing the AI WebUI context...",
    "Waiting for core services to respond..."
  ];

  useEffect(() => {
    const interval = setInterval(() => {
      setLoadingStep((prev) => (prev < steps.length - 1 ? prev + 1 : prev));
    }, 600);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="splash-container">
      <div className="splash-flat-card">
        <div className="splash-flat-content">
          <div className="splash-flat-brand">
            <div className="splash-flat-logo-box">
              <img src="/logo.png" alt="MoFox Code Logo" className="splash-flat-logo" />
            </div>
            <div className="splash-flat-title-box">
              <h1 className="splash-flat-title">MoFox Code</h1>
              <p className="splash-flat-subtitle">AI 编程助手</p>
            </div>
          </div>
          
          <div className="splash-flat-bottom">
            <div className="splash-flat-status">
              <Loader2 className="splash-flat-spinner" size={18} />
              <div className="splash-flat-status-text">
                <span className="splash-flat-loading-label">正在启动</span>
                <span className="splash-flat-loading-step">{steps[loadingStep]}</span>
              </div>
            </div>
            
            <div className="splash-flat-info">
              <p className="splash-flat-version">桌面版 (Desktop Edition)</p>
              <p className="splash-flat-copyright">© 2026 MoFox Team. All rights reserved.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SplashScreen;
