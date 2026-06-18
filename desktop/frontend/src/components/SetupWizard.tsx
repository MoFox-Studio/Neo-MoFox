import React, { useState } from 'react';
import { ArrowRight, ArrowLeft, Check, Plus, Trash2, Sparkles, Loader2 } from 'lucide-react';
import { getCurrentWindow } from '@tauri-apps/api/window';

const WindowControls = () => {
  const appWindow = getCurrentWindow();
  return (
    <div className="shell-window-controls z-[9999] absolute top-0 right-0 p-2 flex">
      <button onClick={() => appWindow.minimize()} className="shell-window-btn p-2 hover:bg-gray-200 dark:hover:bg-gray-800 rounded" title="最小化">
        <svg width="11" height="11" viewBox="0 0 11 11"><rect x="1.5" y="5" width="8" height="1" fill="currentColor"/></svg>
      </button>
      <button onClick={() => appWindow.toggleMaximize()} className="shell-window-btn p-2 hover:bg-gray-200 dark:hover:bg-gray-800 rounded" title="最大化">
        <svg width="11" height="11" viewBox="0 0 11 11"><rect x="1.5" y="1.5" width="8" height="8" fill="none" stroke="currentColor" strokeWidth="1"/></svg>
      </button>
      <button onClick={() => appWindow.close()} className="shell-window-btn p-2 hover:bg-red-500 hover:text-white rounded" title="关闭">
        <svg width="11" height="11" viewBox="0 0 11 11"><path d="M2,2 L9,9 M9,2 L2,9" stroke="currentColor" strokeWidth="1.2"/></svg>
      </button>
    </div>
  );
};

interface SetupWizardProps {
  onComplete: () => void;
  port: number;
}

/* ── 数据结构 ──────────────────────────────────────────── */

interface Provider {
  name: string;
  client_type: string;
  api_key: string;
  base_url: string;
}

interface ModelEntry {
  model_id: string;
  api_provider: string;
  max_context?: string | number;
}

const BASE_URL_DEFAULTS: Record<string, string> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
};



/* ── 主组件 ────────────────────────────────────────────── */

const SetupWizard: React.FC<SetupWizardProps> = ({ onComplete, port }) => {
  const TOTAL_STEPS = 5;
  const steps = [
    { id: 1, title: '连接到 AI 世界', desc: '输入您的 API 凭据，以便我们可以调用大模型能力。' },
    { id: 2, title: '配置模型清单', desc: '添加你需要使用的具体模型，并关联对应的服务商。' },
    { id: 3, title: '分配角色职责', desc: '为不同的任务指定默认模型。' },
    { id: 4, title: '定制助手人格', desc: '赋予您的 AI 助手独特的名字、身份和性格。' },
    { id: 5, title: '接入外部工具', desc: '连接至 MCP 获取额外的工具能力。' }
  ];
  const [step, setStep] = useState<number | 'completed'>(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Step 1 — API Providers
  const [providers, setProviders] = useState<Provider[]>([
    { name: "OpenAI", client_type: "openai", api_key: "", base_url: "https://api.openai.com/v1" },
  ]);
  const [editingProvider, setEditingProvider] = useState(0);

  // Step 1 — Import
  const [importPath, setImportPath] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [importSuccess, setImportSuccess] = useState(false);

  // Step 2 — Models
  const [models, setModels] = useState<ModelEntry[]>([
    { model_id: "gpt-4o", api_provider: "OpenAI" },
  ]);

  // Step 3 — Role defaults
  const [chatModelName, setChatModelName] = useState("");
  const [coderModelName, setCoderModelName] = useState("");
  const [researcherModelName, setResearcherModelName] = useState("");
  const [reviewerModelName, setReviewerModelName] = useState("");
  const [titleModelName, setTitleModelName] = useState("");

  // Step 4 — Personality
  const [botName, setBotName] = useState("MoFox");
  const [aliasNames, setAliasNames] = useState("");
  const [botBio, setBotBio] = useState("我是一个AI编程助手，致力于帮助你编写高质量代码。");
  const [personalityCore, setPersonalityCore] = useState("友好、专业、简洁");
  const [personalitySide, setPersonalitySide] = useState("");
  const [replyStyle, setReplyStyle] = useState("自然口语化");
  const [botIdentity, setBotIdentity] = useState("AI编程助手");

  // Step 5 — MCP
  const [mcpServers, setMcpServers] = useState<Array<{ name: string; command: string; args: string }>>([]);

  /* ── 辅助 ────────────────────────────────────────────── */

  const modelName = (m: ModelEntry) => `${m.api_provider}/${m.model_id}`;
  const allModelNames = models.map(modelName);
  const firstModelName = allModelNames[0] ?? "";

  const effectiveChat = chatModelName || firstModelName;

  /* ── Provider CRUD ───────────────────────────────────── */

  const addProvider = () => {
    setProviders([...providers, { name: "", client_type: "openai", api_key: "", base_url: "" }]);
    setEditingProvider(providers.length);
  };

  const removeProvider = (i: number) => {
    if (providers.length <= 1) return;
    const next = providers.filter((_, idx) => idx !== i);
    setProviders(next);
    setEditingProvider(Math.min(editingProvider, next.length - 1));
    setModels((prev) => prev.filter((m) => m.api_provider !== providers[i].name));
  };

  const updateProvider = (i: number, patch: Partial<Provider>) => {
    const next = [...providers];
    next[i] = { ...next[i], ...patch };
    if (patch.name !== undefined) {
      const oldName = providers[i].name;
      setModels((prev) => prev.map((m) => (m.api_provider === oldName ? { ...m, api_provider: patch.name! } : m)));
    }
    setProviders(next);
  };

  /* ── Model CRUD ──────────────────────────────────────── */

  const addModel = () => {
    const defaultProv = providers[0]?.name ?? "";
    setModels([...models, { model_id: "", api_provider: defaultProv }]);
  };

  const updateModel = (i: number, patch: Partial<ModelEntry>) => {
    const next = [...models];
    next[i] = { ...next[i], ...patch };
    setModels(next);
  };

  /* ── MCP helpers ─────────────────────────────────────── */

  const addMcp = () => setMcpServers([...mcpServers, { name: "", command: "", args: "" }]);
  const removeMcp = (i: number) => setMcpServers(mcpServers.filter((_, idx) => idx !== i));
  const updateMcp = (i: number, field: string, value: string) => {
    const next = [...mcpServers];
    next[i] = { ...next[i], [field]: value };
    setMcpServers(next);
  };

  /* ── Step validation ─────────────────────────────────── */

  const nextStep = () => {
    setError("");
    if (step === 1) {
      if (providers.some((p) => !p.name.trim())) { setError("请填写所有提供商名称"); return; }
      if (providers.some((p) => !p.api_key.trim())) { setError("请填写所有 API Key"); return; }
      providers.forEach((p, i) => {
        if (!p.base_url.trim()) {
          updateProvider(i, { base_url: BASE_URL_DEFAULTS[p.client_type] ?? "" });
        }
      });
    }
    if (step === 2) {
      if (models.length === 0) { setError("至少配置一个模型"); return; }
      if (models.some((m) => !m.model_id.trim())) { setError("请填写所有模型 ID"); return; }
    }
    if (step === 3) {
      if (!effectiveChat) { setError("请为对话角色选择模型"); return; }
    }
    if (typeof step === 'number') {
      setStep(step + 1);
    }
  };

  const prevStep = () => {
    setError("");
    if (typeof step === 'number') {
      setStep(step - 1);
    }
  };

  /* ── Import ──────────────────────────────────────────── */

  const handleImport = async () => {
    if (!importPath.trim()) return;
    setIsImporting(true);
    setError("");
    setImportSuccess(false);
    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/setup/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_dir: importPath.trim() }),
      });
      const json = await res.json();
      if (json.status !== "ok") throw new Error(json.message || "导入失败");
      const d = json.data;

      const impProviders: Provider[] = (d.api_providers ?? []).map((p: any) => ({
        name: p.name ?? "",
        client_type: (p.client_type ?? "openai") as "openai" | "anthropic",
        api_key: p.api_key ?? "",
        base_url: p.base_url ?? "",
      }));
      if (impProviders.length > 0) { setProviders(impProviders); setEditingProvider(0); }

      const impModels: ModelEntry[] = (d.models ?? []).map((m: any) => {
        let mc = m.max_context;
        if (typeof mc === 'number') {
          mc = (mc >= 1024 && mc % 1024 === 0) ? `${mc / 1024}k` : mc.toString();
        } else if (!mc) {
          mc = "";
        }
        return {
          model_id: m.model_id ?? "",
          api_provider: m.api_provider ?? impProviders[0]?.name ?? "",
          max_context: mc,
        };
      });
      if (impModels.length > 0) setModels(impModels);

      const roles = d.roles ?? {};
      setChatModelName(roles.main ?? "");
      setCoderModelName(roles.coder ?? "");
      setResearcherModelName(roles.researcher ?? "");
      setReviewerModelName(roles.reviewer ?? "");
      setTitleModelName(roles.title ?? "");

      const personality = d.personality ?? {};
      if (personality.nickname) setBotName(personality.nickname);
      if (personality.alias_names) setAliasNames(personality.alias_names.join(", "));
      if (personality.background_story) setBotBio(personality.background_story);
      if (personality.personality_core) setPersonalityCore(personality.personality_core);
      if (personality.personality_side) setPersonalitySide(personality.personality_side);
      if (personality.reply_style) setReplyStyle(personality.reply_style);
      if (personality.identity) setBotIdentity(personality.identity);

      const mcpList = (d.mcp_servers ?? []).map((s: any) => ({
        name: s.name ?? "",
        command: s.command ?? "",
        args: Array.isArray(s.args) ? s.args.join(" ") : (s.args ?? ""),
      }));
      if (mcpList.length > 0) setMcpServers(mcpList);

      setImportSuccess(true);
    } catch (err: any) {
      setError(err.message || "导入失败");
    } finally {
      setIsImporting(false);
    }
  };

  /* ── Submit ──────────────────────────────────────────── */

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setError("");

    const chat = chatModelName || firstModelName;
    const coder = coderModelName || chat;
    const researcher = researcherModelName || chat;
    const reviewer = reviewerModelName || chat;
    const title = titleModelName || chat;

    const parsedMcp = mcpServers
      .filter((s) => s.name.trim() && s.command.trim())
      .map((s) => ({
        name: s.name.trim(),
        command: s.command.trim(),
        args: s.args.split(" ").filter((a) => a.trim()),
        enabled: true,
      }));

    const parseMaxContext = (val?: string | number): number | undefined => {
      if (!val) return undefined;
      if (typeof val === 'number') return val;
      const s = val.toString().trim().toLowerCase();
      if (s.endsWith('k')) {
        const num = parseFloat(s.slice(0, -1));
        if (!isNaN(num)) return num * 1024;
      }
      const num = parseInt(s, 10);
      return isNaN(num) ? undefined : num;
    };

    const configPayload = {
      api_providers: providers.map((p) => ({
        name: p.name.trim(),
        client_type: p.client_type,
        api_key: p.api_key.trim(),
        base_url: p.base_url.trim() || (BASE_URL_DEFAULTS[p.client_type] ?? ""),
      })),
      models: models.map((m) => ({
        model_id: m.model_id.trim(),
        api_provider: m.api_provider,
        max_context: parseMaxContext(m.max_context),
      })),
      roles: { main: chat, coder, researcher, reviewer, title },
      personality: {
        nickname: botName.trim(),
        alias_names: aliasNames.split(',').map(s => s.trim()).filter(Boolean),
        background_story: botBio.trim(),
        personality_core: personalityCore.trim(),
        personality_side: personalitySide.trim(),
        reply_style: replyStyle.trim(),
        identity: botIdentity.trim(),
      },
      model_profiles: [],
      mcp_servers: parsedMcp,
    };

    try {
      const res = await fetch(`http://127.0.0.1:${port}/api/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configPayload),
      });
      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.message || "提交配置失败");
      }
      // 触发成功演出 (Celebration)
      setStep('completed');
      setTimeout(() => {
        onComplete();
      }, 2500); // 展示2.5秒
    } catch (err: any) {
      setError(err.message || "发生未知错误");
      setIsSubmitting(false);
    }
  };
  const startSetup = () => {
    if (document.startViewTransition) {
      document.startViewTransition(() => {
        setStep(1);
      });
    } else {
      setStep(1);
    }
  };

  /* ── Render ──────────────────────────────────────────── */

  // 庆祝界面
  return (
    <div className="flex items-center justify-center w-full min-h-screen bg-gray-50 dark:bg-gray-950 p-4 sm:p-8 relative overflow-hidden">
      
      {/* 拖动区 & 顶栏控件 */}
      <WindowControls />
      <div data-tauri-drag-region className="absolute top-0 left-0 right-32 h-10 z-[9998]" />

      {/* 背景光晕层 (美化效果) */}
      <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-blue-500/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-purple-500/10 rounded-full blur-3xl pointer-events-none" />

      {step === 0 ? (
        <div className="flex flex-col items-center justify-center relative z-10 w-full max-w-2xl text-center space-y-8 animate-in fade-in duration-700">
          <div className="w-32 h-32 relative mx-auto" style={{ viewTransitionName: 'mofox-logo' }}>
            <img src="/logo.png" className="w-full h-full object-contain rounded-3xl drop-shadow-2xl" alt="MoFox" onError={(e) => { e.currentTarget.style.display = 'none'; e.currentTarget.nextElementSibling?.classList.remove('hidden'); }} />
            <div className="hidden absolute inset-0 w-full h-full rounded-3xl bg-blue-600 flex items-center justify-center text-white shadow-xl shadow-blue-500/30">
              <Sparkles className="w-12 h-12" />
            </div>
          </div>
          
          <div className="space-y-4" style={{ viewTransitionName: 'mofox-title' }}>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-gray-900 dark:text-white">
              欢迎使用 MoFox Code
            </h1>
            <p className="text-lg text-gray-500 dark:text-gray-400 font-medium max-w-lg mx-auto">
              您的下一代 AI 编程助手。<br/>让我们进行一些基础配置，释放它的全部潜能。
            </p>
          </div>

          <button 
            onClick={startSetup}
            className="mt-8 px-8 py-4 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-2xl shadow-xl hover:shadow-2xl hover:-translate-y-1 transition-all duration-300 flex items-center gap-2 mx-auto group"
          >
            <span>开始体验</span>
            <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
          </button>
        </div>
      ) : (
      <div className="w-full max-w-[1000px] h-[85vh] min-h-[550px] bg-white dark:bg-gray-900 rounded-[2rem] shadow-2xl flex flex-col md:flex-row overflow-hidden relative z-10 border border-gray-100 dark:border-gray-800 animate-in fade-in zoom-in-95 duration-500">
        
        {/* 左侧向导区域 */}
        <div className="w-full md:w-80 bg-gray-50 dark:bg-gray-900/50 p-8 sm:p-10 border-r border-gray-100 dark:border-gray-800 flex flex-col shrink-0">
          <div className="flex items-center gap-3 mb-12">
            <div className="w-10 h-10 relative" style={{ viewTransitionName: 'mofox-logo' }}>
              <img src="/logo.png" className="w-full h-full object-contain rounded-xl drop-shadow-sm" alt="MoFox" onError={(e) => { e.currentTarget.style.display = 'none'; e.currentTarget.nextElementSibling?.classList.remove('hidden'); }} />
              <div className="hidden absolute inset-0 w-full h-full rounded-xl bg-blue-600 flex items-center justify-center text-white shadow-lg shadow-blue-500/30">
                <Sparkles className="w-5 h-5" />
              </div>
            </div>
            <h1 className="text-xl font-bold text-gray-900 dark:text-white tracking-tight" style={{ viewTransitionName: 'mofox-title' }}>MoFox Setup</h1>
          </div>

          <div className="flex-1 relative">
            <h2 className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-6">配置向导</h2>
            <div className="space-y-6 relative">
              {/* 纵向进度线 */}
              <div className="absolute left-[11px] top-4 bottom-4 w-px bg-gray-200 dark:bg-gray-800 -z-10" />
              
              {steps.map((s) => {
                const isActive = step === s.id;
                const isPast = step !== 'completed' && step > s.id;
                const isCompleted = step === 'completed';
                
                return (
                  <div key={s.id} className="flex items-center gap-4 relative">
                    <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-medium transition-colors duration-300 ${
                      isActive ? 'bg-blue-600 text-white shadow-md shadow-blue-500/30' : 
                      (isPast || isCompleted) ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400' : 'bg-gray-100 dark:bg-gray-800 text-gray-400'
                    }`}>
                      {(isPast || isCompleted) ? <Check size={12} strokeWidth={3} /> : s.id}
                    </div>
                    <span className={`text-sm font-medium transition-colors duration-300 ${
                      isActive ? 'text-gray-900 dark:text-white' : 
                      (isPast || isCompleted) ? 'text-gray-600 dark:text-gray-400' : 'text-gray-400 dark:text-gray-600'
                    }`}>
                      {s.title}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* 右侧内容区域 */}
        <div className="flex-1 flex flex-col p-8 sm:p-12 relative overflow-hidden bg-white dark:bg-gray-900">
          
          {step === 'completed' ? (
            <div className="flex flex-col items-center justify-center h-full space-y-6 animate-in fade-in zoom-in duration-500">
              <style>{`
                .check-circle {
                  animation: stroke 0.6s cubic-bezier(0.65, 0, 0.45, 1) forwards;
                }
                .check-path {
                  animation: stroke 0.3s cubic-bezier(0.65, 0, 0.45, 1) 0.6s forwards;
                }
                @keyframes stroke {
                  100% { stroke-dashoffset: 0; }
                }
              `}</style>
              <div className="relative w-24 h-24 flex items-center justify-center">
                <svg className="w-full h-full text-green-500" viewBox="0 0 52 52">
                  <circle className="check-circle" cx="26" cy="26" r="25" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="166" strokeDashoffset="166" />
                  <path className="check-path" fill="none" stroke="currentColor" strokeWidth="3" d="M14.1 27.2l7.1 7.2 16.7-16.8" strokeDasharray="48" strokeDashoffset="48" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div className="text-center space-y-2">
                <h2 className="text-2xl font-bold text-gray-900 dark:text-white">一切准备就绪！</h2>
                <p className="text-sm text-gray-500 font-medium">正在启动 MoFox Code...</p>
              </div>
            </div>
          ) : (
            <>
              {/* 标题 */}
              <div className="mb-8 relative z-20 shrink-0">
                <h2 className="text-2xl font-bold text-gray-900 dark:text-white mb-2">{steps[step - 1].title}</h2>
                <p className="text-gray-500 dark:text-gray-400 text-sm">{steps[step - 1].desc}</p>
              </div>

              {/* 动态表单内容 */}
              <div className="flex-1 overflow-y-auto pr-2 relative -mx-2 px-2" key={`step-${step}`}>
                
                {/* ══ STEP 1: API Providers ══ */}
                {step === 1 && (
                  <div className="space-y-6 animate-in fade-in slide-in-from-right-4 duration-500">
                    
                    {/* 导入区 */}
                    <div className={`${
                      importSuccess 
                        ? 'bg-green-50 dark:bg-green-900/10 border-green-200 dark:border-green-900/30' 
                        : 'bg-blue-50/50 dark:bg-blue-900/10 border-blue-100 dark:border-blue-900/30'
                    } border rounded-xl p-5 transition-colors`}>
                      <h3 className={`text-sm font-semibold mb-3 flex items-center gap-1.5 ${
                        importSuccess ? 'text-green-800 dark:text-green-400' : 'text-blue-800 dark:text-blue-400'
                      }`}>
                        {importSuccess ? <Check size={16} /> : <div className="w-1.5 h-1.5 rounded-full bg-blue-500 mr-0.5" />} 
                        导入已有配置
                      </h3>
                      <div className="flex gap-3">
                        <input type="text" value={importPath} onChange={(e) => setImportPath(e.target.value)} className={`flex-1 px-3 py-2 border rounded-lg text-sm outline-none focus:ring-2 transition-colors ${
                          importSuccess 
                            ? 'border-green-200 dark:border-green-900/50 bg-white dark:bg-gray-950 focus:ring-green-500/50 text-green-900 dark:text-green-100' 
                            : 'border-blue-200 dark:border-blue-800/50 bg-white dark:bg-gray-950 focus:ring-blue-500/50'
                        }`} placeholder="配置目录路径..." />
                        <button onClick={handleImport} disabled={isImporting} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg disabled:opacity-50 transition-colors shrink-0">
                          {isImporting ? '...' : importSuccess ? '重新导入' : '导入配置'}
                        </button>
                      </div>
                      {error && <p className="text-red-500 text-xs mt-2">{error}</p>}
                    </div>

                    <div className="w-full h-px bg-gray-100 dark:bg-gray-800" />

                    {/* 手动添加区 */}
                    <div className="space-y-4">
                      <div className="flex flex-wrap gap-2">
                        {providers.map((p, idx) => (
                          <button key={idx} onClick={() => setEditingProvider(idx)} className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors flex items-center gap-2 ${
                            editingProvider === idx 
                              ? 'bg-blue-50 border-blue-200 text-blue-700 dark:bg-blue-900/20 dark:border-blue-800 dark:text-blue-400' 
                              : 'bg-white border-gray-200 text-gray-700 hover:bg-gray-50 dark:bg-gray-900 dark:border-gray-800 dark:text-gray-300 dark:hover:bg-gray-800'
                          }`}>
                            {p.name || '未命名'}
                            <span onClick={(e) => { e.stopPropagation(); removeProvider(idx); }} className="p-0.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-red-500">
                              <Trash2 size={14} />
                            </span>
                          </button>
                        ))}
                        <button onClick={addProvider} className="px-4 py-2 rounded-lg text-sm font-medium border border-dashed border-gray-300 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors flex items-center gap-1">
                          <Plus size={16} /> 添加
                        </button>
                      </div>

                      {editingProvider !== null && providers[editingProvider] && (
                        <div className="bg-gray-50 dark:bg-gray-900/50 rounded-2xl p-6 border border-gray-100 dark:border-gray-800/60 space-y-5 animate-in zoom-in-95 duration-200">
                          
                          <div className="flex items-center gap-2 mb-2">
                            {['OpenAI 兼容', 'Anthropic', '自定义'].map(type => {
                              const typeMap: Record<string, string> = { 'OpenAI 兼容': 'openai', 'Anthropic': 'anthropic', '自定义': 'custom' };
                              const isActive = providers[editingProvider].client_type === typeMap[type];
                              return (
                                <button key={type} onClick={() => updateProvider(editingProvider, { client_type: typeMap[type] })} className={`px-3 py-1.5 rounded-md text-xs font-medium border transition-colors ${
                                  isActive ? 'bg-white dark:bg-gray-800 shadow-sm border-gray-200 dark:border-gray-700 text-gray-900 dark:text-white' : 'border-transparent text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-800'
                                }`}>
                                  {type}
                                </button>
                              );
                            })}
                          </div>

                          <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-1.5">
                              <label className="text-xs font-semibold text-gray-900 dark:text-white">识别名称</label>
                              <input type="text" value={providers[editingProvider].name} onChange={(e) => updateProvider(editingProvider, { name: e.target.value })} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-white dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none" placeholder="如: OpenAI" />
                            </div>
                            <div className="space-y-1.5">
                              <label className="text-xs font-semibold text-gray-900 dark:text-white">客户端类型</label>
                              <select className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-white dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20width%3D%2220%22%20height%3D%2220%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M5%208l5%205%205-5%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] bg-[length:20px] bg-[position:right_0.5rem_center] bg-no-repeat pr-10" value={providers[editingProvider].client_type} onChange={(e) => updateProvider(editingProvider, { client_type: e.target.value })}>
                                <option value="openai">OpenAI 兼容</option>
                                <option value="anthropic">Anthropic</option>
                                <option value="google">Google</option>
                              </select>
                            </div>
                          </div>

                          <div className="space-y-1.5">
                            <label className="text-xs font-semibold text-gray-900 dark:text-white">API Key</label>
                            <input type="password" value={providers[editingProvider].api_key} onChange={(e) => updateProvider(editingProvider, { api_key: e.target.value })} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-white dark:bg-gray-950 font-mono focus:ring-2 focus:ring-blue-500/50 outline-none" placeholder="sk-..." />
                          </div>

                          <div className="space-y-1.5">
                            <label className="text-xs font-semibold text-gray-900 dark:text-white">Base URL (选填)</label>
                            <input type="text" value={providers[editingProvider].base_url} onChange={(e) => updateProvider(editingProvider, { base_url: e.target.value })} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-white dark:bg-gray-950 font-mono focus:ring-2 focus:ring-blue-500/50 outline-none" placeholder="https://api.openai.com/v1" />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* ══ STEP 2: Models ══ */}
                {step === 2 && (
                  <div className="space-y-4 animate-in fade-in slide-in-from-right-4 duration-500 pb-8">
                    {models.map((m, idx) => (
                      <div key={idx} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl p-4 shadow-sm flex flex-col sm:flex-row gap-4 relative group">
                        <button onClick={() => setModels(prev => prev.filter((_, i) => i !== idx))} className="absolute top-2 right-2 p-1.5 text-gray-400 hover:text-red-500 rounded bg-transparent hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors">
                          <Trash2 size={16} />
                        </button>
                        <div className="flex-1 space-y-1.5">
                          <label className="text-[11px] font-semibold text-gray-500 uppercase">模型 ID</label>
                          <input type="text" value={m.model_id} onChange={(e) => updateModel(idx, { model_id: e.target.value })} className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none" placeholder="例如: gpt-4o" />
                        </div>
                        <div className="w-full sm:w-48 shrink-0 space-y-1.5">
                          <label className="text-[11px] font-semibold text-gray-500 uppercase">所属服务商</label>
                          <select className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm bg-gray-50 dark:bg-gray-950 focus:ring-2 focus:ring-blue-500/50 outline-none appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20width%3D%2220%22%20height%3D%2220%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M5%208l5%205%205-5%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] bg-[length:20px] bg-[position:right_0.5rem_center] bg-no-repeat pr-8" value={m.api_provider} onChange={(e) => updateModel(idx, { api_provider: e.target.value })}>
                            {providers.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                          </select>
                        </div>
                        <div className="w-full sm:w-32 shrink-0 space-y-1.5">
                          <label className="text-[11px] font-semibold text-gray-500 uppercase">最大上下文</label>
                          <input className="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50" value={m.max_context || ""} onChange={(e) => updateModel(idx, { max_context: e.target.value })} placeholder="如: 128k" />
                        </div>
                      </div>
                    ))}
                    <button type="button" onClick={addModel} className="w-full py-4 border-2 border-dashed border-gray-300 dark:border-gray-700 rounded-xl text-sm font-medium text-gray-600 dark:text-gray-400 hover:border-blue-500 hover:text-blue-600 dark:hover:border-blue-500 dark:hover:text-blue-400 transition-colors flex items-center justify-center gap-2">
                      <Plus size={18} /> 添加新模型
                    </button>
                  </div>
                )}

              {/* ══ STEP 3: Role defaults ══ */}
              {step === 3 && (
                <div className="space-y-4 max-w-2xl animate-in fade-in slide-in-from-right-4 duration-500">
                  <div className="bg-gray-50 dark:bg-gray-900/50 rounded-2xl p-6 border border-gray-100 dark:border-gray-800/60 space-y-4">
                    
                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <label className="text-sm font-semibold text-gray-900 dark:text-white">主干模型 (Main/Chat)</label>
                        <p className="text-[11px] text-gray-500">负责基础的闲聊和大部分综合任务。</p>
                      </div>
                      <select className="w-56 px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm font-medium outline-none shadow-sm appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20width%3D%2220%22%20height%3D%2220%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M5%208l5%205%205-5%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] bg-[length:20px] bg-[position:right_0.5rem_center] bg-no-repeat pr-8" value={chatModelName || firstModelName} onChange={(e) => setChatModelName(e.target.value)}>
                        {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>
                    
                    <div className="w-full h-px bg-gray-200 dark:bg-gray-800" />

                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <label className="text-sm font-semibold text-gray-900 dark:text-white">代码模型 (Coder)</label>
                        <p className="text-[11px] text-gray-500">负责编写核心代码，推荐使用能力最强的模型。</p>
                      </div>
                      <select className="w-56 px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm font-medium outline-none shadow-sm appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20width%3D%2220%22%20height%3D%2220%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M5%208l5%205%205-5%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] bg-[length:20px] bg-[position:right_0.5rem_center] bg-no-repeat pr-8" value={coderModelName || effectiveChat} onChange={(e) => setCoderModelName(e.target.value)}>
                        {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>

                    <div className="w-full h-px bg-gray-200 dark:bg-gray-800" />

                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <label className="text-sm font-medium text-gray-800 dark:text-gray-200">研究员 (Researcher)</label>
                        <p className="text-[11px] text-gray-500">负责代码搜索、网络检索与问题研究。</p>
                      </div>
                      <select className="w-56 px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none shadow-sm appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20width%3D%2220%22%20height%3D%2220%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M5%208l5%205%205-5%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] bg-[length:20px] bg-[position:right_0.5rem_center] bg-no-repeat pr-8" value={researcherModelName || effectiveChat} onChange={(e) => setResearcherModelName(e.target.value)}>
                        {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>

                    <div className="w-full h-px bg-gray-200 dark:bg-gray-800" />

                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <label className="text-sm font-medium text-gray-800 dark:text-gray-200">审查员 (Reviewer)</label>
                        <p className="text-[11px] text-gray-500">负责代码变更的自动审核和问题提示。</p>
                      </div>
                      <select className="w-56 px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none shadow-sm appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20width%3D%2220%22%20height%3D%2220%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M5%208l5%205%205-5%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] bg-[length:20px] bg-[position:right_0.5rem_center] bg-no-repeat pr-8" value={reviewerModelName || effectiveChat} onChange={(e) => setReviewerModelName(e.target.value)}>
                        {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>

                    <div className="w-full h-px bg-gray-200 dark:bg-gray-800" />

                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <label className="text-sm font-medium text-gray-800 dark:text-gray-200">标题生成 (Title)</label>
                        <p className="text-[11px] text-gray-500">用于总结对话意图，生成会话标签，可使用轻量模型。</p>
                      </div>
                      <select className="w-56 px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none shadow-sm appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20width%3D%2220%22%20height%3D%2220%22%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E%3Cpath%20d%3D%22M5%208l5%205%205-5%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.5%22%20fill%3D%22none%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E')] bg-[length:20px] bg-[position:right_0.5rem_center] bg-no-repeat pr-8" value={titleModelName || effectiveChat} onChange={(e) => setTitleModelName(e.target.value)}>
                        {allModelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>
                  </div>
                </div>
              )}

              {/* ══ STEP 4: Personality ══ */}
              {step === 4 && (
                <div className="space-y-5">
                  <div className="grid grid-cols-2 gap-5">
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">助手名称</label>
                      <input className="w-full px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50" value={botName} onChange={(e) => setBotName(e.target.value)} placeholder="如: MoFox" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">别名 (逗号分隔)</label>
                      <input className="w-full px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50" value={aliasNames} onChange={(e) => setAliasNames(e.target.value)} placeholder="如: 小狐狸, 莫狐" />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">身份特征 (System Identity)</label>
                    <input className="w-full px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50" value={botIdentity} onChange={(e) => setBotIdentity(e.target.value)} placeholder="如: AI编程助手" />
                  </div>
                  <div className="grid grid-cols-2 gap-5">
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">核心性格</label>
                      <input className="w-full px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50" value={personalityCore} onChange={(e) => setPersonalityCore(e.target.value)} placeholder="如: 友好、专业、简洁" />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-sm font-medium text-gray-700 dark:text-gray-300">扩展设定</label>
                      <input className="w-full px-3 py-2 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50" value={personalitySide} onChange={(e) => setPersonalitySide(e.target.value)} placeholder="如: 说话带有口头禅等" />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">背景故事 / 核心设定</label>
                    <textarea className="w-full px-3 py-3 bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50 resize-none" value={botBio} onChange={(e) => setBotBio(e.target.value)} rows={3} placeholder="描述助手的世界观和背景设定..." />
                  </div>
                </div>
              )}

              {/* ══ STEP 5: MCP ══ */}
              {step === 5 && (
                <div className="space-y-4">
                  {mcpServers.length === 0 ? (
                    <div className="text-center py-16 px-6 border-2 border-dashed border-gray-200 dark:border-gray-800 rounded-2xl">
                      <div className="w-12 h-12 bg-gray-100 dark:bg-gray-800 rounded-full flex items-center justify-center mx-auto mb-3">
                        <Sparkles className="w-6 h-6 text-gray-400" />
                      </div>
                      <h4 className="text-base font-medium text-gray-900 dark:text-white mb-1">未配置 MCP 服务</h4>
                      <p className="text-sm text-gray-500">Model Context Protocol 可以扩展 AI 的工具能力，如果您不需要，可以直接点击右下角的完成。</p>
                    </div>
                  ) : mcpServers.map((server, i) => (
                    <div key={i} className="bg-white dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl p-5 relative shadow-sm group">
                      <button onClick={() => removeMcp(i)} className="absolute top-4 right-4 p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-md transition-all">
                        <Trash2 size={16} />
                      </button>
                      <div className="grid grid-cols-12 gap-4 mb-4">
                        <div className="col-span-4 space-y-1">
                          <label className="text-xs font-medium text-gray-500 uppercase">服务器名称</label>
                          <input className="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50" value={server.name} onChange={(e) => updateMcp(i, "name", e.target.value)} placeholder="例如: fetch" />
                        </div>
                        <div className="col-span-8 space-y-1">
                          <label className="text-xs font-medium text-gray-500 uppercase">启动命令</label>
                          <input className="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50 font-mono" value={server.command} onChange={(e) => updateMcp(i, "command", e.target.value)} placeholder="例如: npx" />
                        </div>
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-gray-500 uppercase">参数 (空格分隔)</label>
                        <input className="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-300 dark:border-gray-700 rounded-lg text-sm outline-none focus:ring-2 focus:ring-blue-500/50 font-mono" value={server.args} onChange={(e) => updateMcp(i, "args", e.target.value)} placeholder="-y @modelcontextprotocol/server-postgres" />
                      </div>
                    </div>
                  ))}
                  <button type="button" onClick={addMcp} className="w-full py-4 border-2 border-dashed border-gray-300 dark:border-gray-700 rounded-xl text-sm font-medium text-gray-600 dark:text-gray-400 hover:border-blue-500 hover:text-blue-600 dark:hover:border-blue-500 dark:hover:text-blue-400 transition-colors flex items-center justify-center gap-2">
                    <Plus size={18} /> 添加 MCP 服务
                  </button>
                </div>
              )}
            </div>
            
            {/* Error Message */}
            {error && (
              <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-lg text-red-600 dark:text-red-400 text-sm font-medium flex items-center z-20">
                {error}
              </div>
            )}

            {/* Nav buttons */}
            <div className="flex items-center justify-between mt-8 pt-6 border-t border-gray-100 dark:border-gray-800 z-20">
              <div>
                {step > 1 && (
                  <button type="button" onClick={prevStep} disabled={isSubmitting} className="px-5 py-2.5 rounded-lg text-sm font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors flex items-center gap-2 disabled:opacity-50">
                    <ArrowLeft size={16} /> 返回上一步
                  </button>
                )}
              </div>
              
              <button type="button" onClick={step < TOTAL_STEPS ? nextStep : handleSubmit} disabled={isSubmitting} className="px-8 py-3 rounded-xl text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 shadow-lg shadow-blue-500/30 hover:shadow-blue-500/50 transition-all flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed">
                {isSubmitting ? (
                  <><Loader2 className="animate-spin" size={18} /> 配置中...</>
                ) : step < TOTAL_STEPS ? (
                  <>继续 <ArrowRight size={18} /></>
                ) : (
                  "完成并启动"
                )}
              </button>
            </div>
            </>
          )}

        </div>
      </div>
      )}
    </div>
  );
};

export default SetupWizard;
